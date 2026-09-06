"""SQLite authority for workbench identities; runtime data stays outside source."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_SCOPES = {"read", "write", "execute", "delegate"}


def default_data_dir() -> Path:
    if override := os.environ.get("CORPPILOT_WORKBENCH_HOME"):
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "CorpPilot" / "workbench"


def role_catalog(agents_dir: Path) -> list[dict]:
    """Both department SOULs and specialized role files are actual templates."""
    sources = sorted(set(agents_dir.glob("*/SOUL.md")) | set(agents_dir.glob("*/roles/*.md")))
    roles = []
    for source in sources:
        relative = source.relative_to(agents_dir).as_posix()
        content = source.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), source.stem)
        roles.append({"id": relative.removesuffix(".md"), "name": title,
                      "department": relative.split("/")[0], "source": "agents/" + relative,
                      "instructions": content})
    if not roles:
        raise ValueError("没有找到 Agent 角色定义，请检查 agents 目录")
    return roles


def _text(value: object, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} 必须为 1–{maximum} 字符的文本")
    return value.strip()


def _strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{field} 必须是最多 64 项的列表")
    return list(dict.fromkeys(_text(item, field) for item in value))


class Store:
    def __init__(self, data_dir: Path | None = None, agents_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        roles = role_catalog(Path(agents_dir) if agents_dir is not None else REPO_ROOT / "agents")
        with self.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
                versions = [row[0] for row in db.execute("SELECT version FROM schema_version")]
                if versions not in ([1], [2]):
                    raise ValueError("不支持的工作台数据库版本，请使用匹配版本的软件")
                if versions == [1]:
                    # Concurrent startups must never overwrite another startup's recovery snapshot.
                    # The snapshot's schema_version is authoritative if migration races this backup.
                    backup_path = self.data_dir / f"workbench-before-migration-{uuid.uuid4()}.sqlite3"
                    with closing(sqlite3.connect(backup_path)) as backup:
                        db.backup(backup)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
                    source TEXT NOT NULL UNIQUE, instructions TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES templates(id),
                    name TEXT NOT NULL, model TEXT NOT NULL DEFAULT 'default',
                    skills TEXT NOT NULL DEFAULT '[]', tools TEXT NOT NULL DEFAULT '["read"]',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
            """)
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS agent_creation_requests (
                    request_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
                    payload TEXT NOT NULL, response TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS agent_creation_no_update BEFORE UPDATE ON agent_creation_requests
                    BEGIN SELECT RAISE(ABORT,'Agent creation requests are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS agent_creation_no_delete BEFORE DELETE ON agent_creation_requests
                    BEGIN SELECT RAISE(ABORT,'Agent creation requests are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS agent_creation_no_replace BEFORE INSERT ON agent_creation_requests
                    WHEN EXISTS(SELECT 1 FROM agent_creation_requests WHERE request_id=NEW.request_id)
                    BEGIN SELECT RAISE(ABORT,'Agent creation requests are immutable'); END;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL CHECK(type IN ('dm','board','project')),
                    title TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
                    dm_agent_id TEXT UNIQUE REFERENCES agents(id),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS members (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id), PRIMARY KEY(conversation_id,agent_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    sender_kind TEXT NOT NULL CHECK(sender_kind IN ('owner','agent')),
                    sender_id TEXT REFERENCES agents(id), content TEXT NOT NULL, request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(conversation_id,request_id)
                );
                UPDATE schema_version SET version=2;
            """)
            for role in roles:
                db.execute("""INSERT INTO templates VALUES (:id,:name,:department,:source,:instructions)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name, department=excluded.department,
                    source=excluded.source, instructions=excluded.instructions""", role)
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, "corppilot:default:" + role["id"]))
                db.execute("""INSERT OR IGNORE INTO agents(id,template_id,name,is_default)
                    VALUES (?,?,?,1)""", (identity, role["id"], role["name"]))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def templates(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM templates ORDER BY department,id")]

    @staticmethod
    def _agent(row) -> dict:
        value = dict(row)
        for key in ("skills", "tools"):
            value[key] = json.loads(value[key])
        for key in ("enabled", "is_default"):
            value[key] = bool(value[key])
        return value

    def agents(self) -> list[dict]:
        with self.connect() as db:
            return [self._agent(row) for row in db.execute("SELECT * FROM agents ORDER BY created_at,id")]

    def agent(self, agent_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError("Agent 不存在")
        return self._agent(row)

    def save_agent(self, payload: dict, agent_id: str | None = None) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Agent 配置必须是对象")
        allowed = {"name", "template_id", "model", "skills", "tools", "enabled"}
        if agent_id is None:
            allowed.add('request_id')
        if set(payload) - allowed:
            raise ValueError("包含不支持的 Agent 配置字段")
        request_id = _text(payload['request_id'], '创建请求 ID', 120) if 'request_id' in payload else None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if agent_id is not None:
                row = db.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone()
                if row is None:
                    raise KeyError('Agent 不存在')
                current = self._agent(row)
            else:
                current = {"model": "default", "skills": [], "tools": ["read"], "enabled": True}
            values = {**current, **payload}
            name = _text(values.get("name"), "名字", 80)
            template = _text(values.get("template_id"), "角色模板")
            model = _text(values.get("model"), "模型")
            skills = _strings(values.get("skills"), "技能")
            tools = _strings(values.get("tools"), "工具权限")
            if not set(tools) <= TOOL_SCOPES:
                raise ValueError("未知工具权限")
            if type(values.get("enabled")) is not bool:
                raise ValueError("启用状态必须为布尔值")
            normalized_payload = dict(name=name, template_id=template, model=model, skills=skills, tools=tools, enabled=values['enabled'])
            encoded = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if request_id is not None:
                previous = db.execute('SELECT payload,response FROM agent_creation_requests WHERE request_id=?', (request_id,)).fetchone()
                if previous is not None:
                    if previous['payload'] != encoded:
                        raise ValueError('request_id 已用于不同的身份创建请求')
                    return json.loads(previous['response'])
            identity = agent_id if agent_id is not None else str(uuid.uuid4())
            if not db.execute("SELECT 1 FROM templates WHERE id=?", (template,)).fetchone():
                raise ValueError("角色模板不存在")
            if 'skills' in payload or not agent_id:
                previous = db.execute('SELECT skills FROM agents WHERE id=?', (agent_id,)).fetchone() if agent_id else None
                if previous is None or json.loads(previous[0]) != skills:
                    from .skill_inputs import load_selected
                    load_selected(skills)
            fields = (template, name, model, json.dumps(skills), json.dumps(tools), int(values["enabled"]))
            if agent_id:
                # Patch only requested fields; concurrent edits must not restore stale values.
                normalized = dict(zip(("template_id", "name", "model", "skills", "tools", "enabled"), fields))
                columns = sorted(payload)
                if columns:
                    assignments = ",".join(f"{key}=?" for key in columns)
                    db.execute(f"UPDATE agents SET {assignments}, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                               (*[normalized[key] for key in columns], identity))
            else:
                db.execute("INSERT INTO agents(template_id,name,model,skills,tools,enabled,id) VALUES(?,?,?,?,?,?,?)",
                           (*fields, identity))
            response = self._agent(db.execute('SELECT * FROM agents WHERE id=?', (identity,)).fetchone())
            if request_id is not None:
                db.execute('INSERT INTO agent_creation_requests VALUES(?,?,?,?)',
                           (request_id, identity, encoded, json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False)))
            return response

    def agent_request(self, request_id: str) -> dict | None:
        request_id = _text(request_id, '创建请求 ID', 120)
        with self.connect() as db:
            row = db.execute('SELECT * FROM agent_creation_requests WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                return None
            return {'request_id': row['request_id'], 'payload': json.loads(row['payload']), 'agent': json.loads(row['response'])}

    @staticmethod
    def _conversation(db, identity, actor_id=None):
        row = db.execute("SELECT * FROM conversations WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("会话不存在")
        members = [r[0] for r in db.execute("SELECT agent_id FROM members WHERE conversation_id=? ORDER BY agent_id", (identity,))]
        if actor_id is not None and actor_id not in members:
            raise PermissionError("Agent 不是会话成员")
        value = dict(row)
        value["archived"] = bool(value["archived"])
        value["member_ids"] = members
        last = db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY sequence DESC LIMIT 1", (identity,)).fetchone()
        value["last_message"] = dict(last) if last else None
        return value

    def conversations(self, actor_id=None):
        with self.connect() as db:
            db.execute("BEGIN")
            rows = db.execute("""SELECT id FROM conversations WHERE ? IS NULL OR id IN
                (SELECT conversation_id FROM members WHERE agent_id=?) ORDER BY updated_at DESC,id""", (actor_id, actor_id))
            return [self._conversation(db, r[0], actor_id) for r in rows]

    def conversation(self, identity, actor_id=None):
        with self.connect() as db:
            db.execute("BEGIN")
            return self._conversation(db, identity, actor_id)

    @staticmethod
    def _enabled_member(db, agent_id):
        row = db.execute("SELECT enabled FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None or not row[0]:
            raise ValueError("成员必须是存在且启用的 Agent")

    def save_conversation(self, payload, identity=None):
        allowed = {"title", "archived"} if identity is not None else {"title", "type", "member_ids"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("包含不支持的会话字段")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if identity is not None:
                current = self._conversation(db, identity)
                title = _text(payload.get("title", current["title"]), "会话标题", 120)
                archived = payload.get("archived", current["archived"])
                if type(archived) is not bool:
                    raise ValueError("归档状态必须为布尔值")
                db.execute("UPDATE conversations SET title=?, archived=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                           (title, archived, identity))
            else:
                kind = payload.get("type")
                if kind not in ("dm", "board", "project"):
                    raise ValueError("未知会话类型")
                members = _strings(payload.get("member_ids"), "会话成员")
                if not members or (kind == "dm" and len(members) != 1):
                    raise ValueError("私聊必须为一名成员，群聊至少一名成员")
                for member in members:
                    self._enabled_member(db, member)
                title = _text(payload.get("title"), "会话标题", 120)
                existing = db.execute("SELECT id FROM conversations WHERE dm_agent_id=?", (members[0],)).fetchone() if kind == "dm" else None
                if existing:
                    return self._conversation(db, existing[0])
                identity = str(uuid.uuid4())
                db.execute("INSERT INTO conversations(id,type,title,dm_agent_id) VALUES (?,?,?,?)",
                           (identity, kind, title, members[0] if kind == "dm" else None))
                db.executemany("INSERT INTO members VALUES (?,?)", [(identity, member) for member in members])
            return self._conversation(db, identity)

    def set_member(self, conversation_id, agent_id, joined):
        if type(joined) is not bool:
            raise ValueError("成员加入状态必须为布尔值")
        agent_id = _text(agent_id, "Agent ID")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._conversation(db, conversation_id)
            if current["type"] == "dm":
                raise ValueError("私聊成员不可更改")
            if current["archived"]:
                raise ValueError("归档会话不可更改成员")
            if joined:
                self._enabled_member(db, agent_id)
                db.execute("INSERT OR IGNORE INTO members VALUES (?,?)", (conversation_id, agent_id))
            else:
                if agent_id in current["member_ids"] and len(current["member_ids"]) == 1:
                    raise ValueError("群聊必须保留至少一名成员")
                db.execute("DELETE FROM members WHERE conversation_id=? AND agent_id=?", (conversation_id, agent_id))
            db.execute("UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (conversation_id,))
            return self._conversation(db, conversation_id)

    def messages(self, conversation_id, actor_id=None, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("消息分页参数无效")
        with self.connect() as db:
            db.execute("BEGIN")
            self._conversation(db, conversation_id, actor_id)
            return [dict(row) for row in db.execute("SELECT * FROM messages WHERE conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                                                    (conversation_id, after, limit))]

    def send_message(self, conversation_id, payload, actor_id=None):
        if not isinstance(payload, dict) or set(payload) != {"content", "request_id"}:
            raise ValueError("消息必须只含 content 和 request_id")
        content = _text(payload["content"], "消息", 16000)
        request_id = _text(payload["request_id"], "请求 ID", 120)
        sender_kind = "owner" if actor_id is None else "agent"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._conversation(db, conversation_id, actor_id)
            if actor_id is not None:
                self._enabled_member(db, actor_id)
            previous = db.execute("SELECT * FROM messages WHERE conversation_id=? AND request_id=?", (conversation_id, request_id)).fetchone()
            if previous:
                if (previous["content"], previous["sender_id"]) != (content, actor_id):
                    raise ValueError("request_id 已用于不同消息")
                return dict(previous)
            if current["archived"]:
                raise ValueError("会话已归档，不能发送消息")
            identity = str(uuid.uuid4())
            db.execute("INSERT INTO messages(id,conversation_id,sender_kind,sender_id,content,request_id) VALUES(?,?,?,?,?,?)",
                       (identity, conversation_id, sender_kind, actor_id, content, request_id))
            db.execute("UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (conversation_id,))
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (identity,)).fetchone())
