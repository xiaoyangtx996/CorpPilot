"""SQLite authority for workbench identities; runtime data stays outside source."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
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
                if versions != [1]:
                    raise ValueError("不支持的工作台数据库版本，请使用匹配版本的软件")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES (1);
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
        if set(payload) - allowed:
            raise ValueError("包含不支持的 Agent 配置字段")
        current = self.agent(agent_id) if agent_id else {
            "model": "default", "skills": [], "tools": ["read"], "enabled": True,
        }
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
        identity = agent_id or str(uuid.uuid4())
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM templates WHERE id=?", (template,)).fetchone():
                raise ValueError("角色模板不存在")
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
        return self.agent(identity)
