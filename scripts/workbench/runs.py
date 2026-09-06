"""Durable, single-agent replies with transactional authority and idempotency."""
from __future__ import annotations

import json
import uuid

from .store import Store, _text
from . import planning, retrospectives, model_reconciliations, peer_reviews
from .memories import Memories


class Runs:
    def __init__(self, store: Store):
        self.store = store
        self.memories = Memories(store)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS runs_schema_version (version INTEGER PRIMARY KEY)")
            versions = [row[0] for row in db.execute("SELECT version FROM runs_schema_version")]
            if versions and versions != [1]:
                raise ValueError("不支持的 Run 数据库版本")
            db.execute("INSERT OR IGNORE INTO runs_schema_version VALUES (1)")
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                agent_id TEXT NOT NULL REFERENCES agents(id),
                source_message_id TEXT NOT NULL REFERENCES messages(id),
                request_id TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt=1),
                requirement_version INTEGER NOT NULL DEFAULT 1 CHECK(requirement_version=1),
                state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN
                    ('queued','running','completed','failed','cancelled','unknown')),
                model TEXT, usage TEXT, error TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                reply_message_id TEXT UNIQUE REFERENCES messages(id),
                UNIQUE(conversation_id,request_id)
            )""")

            planning.initialize(db)
            retrospectives.initialize(db)
            peer_reviews.initialize(db)
            model_reconciliations.initialize(db)

    @staticmethod
    def _run(db, identity):
        row = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("Run 不存在")
        result = dict(row)
        result["usage"] = json.loads(result["usage"]) if result["usage"] else None
        return result

    def _authorize(self, db, run):
        conversation = self.store._conversation(db, run["conversation_id"], run["agent_id"])
        self.store._enabled_member(db, run["agent_id"])
        if conversation["archived"]:
            raise ValueError("会话已归档，不能生成回复")

    def create(self, conversation_id, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._create(db, conversation_id, payload)

    def _create(self, db, conversation_id, payload, *, is_planning=False, is_retrospective=False, is_peer_review=False):
        if not isinstance(payload, dict) or set(payload) != {"agent_id", "source_message_id", "request_id"}:
            raise ValueError("Run 必须只含 agent_id、source_message_id 和 request_id")
        agent_id = _text(payload["agent_id"], "Agent ID")
        source = _text(payload["source_message_id"], "源消息 ID")
        request = _text(payload["request_id"], "请求 ID", 120)
        previous = db.execute("SELECT id,agent_id,source_message_id FROM runs WHERE conversation_id=? AND request_id=?",
                              (conversation_id, request)).fetchone()
        if previous:
            existing_kind = db.execute("SELECT 1 FROM planning_requests WHERE run_id=?", (previous["id"],)).fetchone() is not None
            existing_retro = db.execute("SELECT 1 FROM retrospective_requests WHERE run_id=?", (previous["id"],)).fetchone() is not None
            existing_peer = db.execute("SELECT 1 FROM peer_review_requests WHERE run_id=?", (previous["id"],)).fetchone() is not None
            if (existing_kind, existing_retro, existing_peer) != (is_planning, is_retrospective, is_peer_review):
                raise ValueError("request_id 已用于不同类型的 Run")
            if (previous["agent_id"], previous["source_message_id"]) != (agent_id, source):
                raise ValueError("request_id 已用于不同 Run")
            return self._run(db, previous["id"])
        if not is_retrospective and model_reconciliations.unresolved(db,
                {"conversation_id": conversation_id, "source_message_id": source, "agent_id": agent_id},
                kind="peer_review" if is_peer_review else "planning" if is_planning else "reply"):
            raise ValueError("同一模型操作存在未核查的未知请求，不能启动替代请求")
        self._authorize(db, {"conversation_id": conversation_id, "agent_id": agent_id})
        if is_peer_review:
            peer_reviews.source(db, conversation_id, source, agent_id)
        elif not db.execute("SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND sender_kind='owner'",
                          (source, conversation_id)).fetchone():
            raise ValueError("源消息必须是本会话中的 Owner 消息")
        if db.execute("SELECT count(*) FROM runs WHERE state IN ('queued','running')").fetchone()[0] >= 100:
            raise ValueError("回复队列已满，请等待现有请求完成或取消排队")
        identity = str(uuid.uuid4())
        db.execute("INSERT INTO runs(id,conversation_id,agent_id,source_message_id,request_id) VALUES(?,?,?,?,?)",
                   (identity, conversation_id, agent_id, source, request))
        return self._run(db, identity)

    def get(self, identity):
        with self.store.connect() as db:
            return self._run(db, identity)

    def list(self, conversation_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.store._conversation(db, conversation_id)
            # Keep every active request discoverable; the global admission cap bounds this list.
            return [self._run(db, row[0]) for row in db.execute("""SELECT id FROM runs WHERE conversation_id=?
                AND id NOT IN (SELECT run_id FROM planning_requests) AND id NOT IN (SELECT run_id FROM retrospective_requests) AND id NOT IN (SELECT run_id FROM peer_review_requests) AND (state IN ('queued','running') OR id IN (SELECT id FROM runs WHERE conversation_id=?
                    AND id NOT IN (SELECT run_id FROM planning_requests) AND id NOT IN (SELECT run_id FROM retrospective_requests) AND id NOT IN (SELECT run_id FROM peer_review_requests) AND state NOT IN ('queued','running') ORDER BY updated_at DESC,id DESC LIMIT 100))
                ORDER BY created_at DESC,id DESC""", (conversation_id, conversation_id))]

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("队列读取上限必须为 1–100")
        with self.store.connect() as db:
            db.execute("BEGIN")
            rows = [self._run(db, row[0]) for row in db.execute(
                "SELECT id FROM runs WHERE state='queued' ORDER BY created_at,id LIMIT 100")]
            return [row for row in rows if not model_reconciliations.unresolved(db, row)][:limit]

    def claim(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            if run["state"] != "queued" or model_reconciliations.unresolved(db, run):
                return False
            return db.execute("""UPDATE runs SET state='running',
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND state='queued'""", (identity,)).rowcount == 1

    def snapshot(self, identity):
        """Internal model input only; never expose this private snapshot in API output."""
        with self.store.connect() as db:
            db.execute("BEGIN")
            run = self._run(db, identity)
            if run["state"] != "running":
                raise ValueError("只有运行中的 Run 可以读取模型上下文")
            self._authorize(db, run)
            agent = self.store._agent(db.execute("SELECT * FROM agents WHERE id=?", (run["agent_id"],)).fetchone())
            instructions = db.execute("SELECT instructions FROM templates WHERE id=?", (agent["template_id"],)).fetchone()[0]
            if peer := peer_reviews.snapshot(db, run, agent):
                return peer
            sequence = db.execute("SELECT sequence FROM messages WHERE id=? AND conversation_id=? AND sender_kind='owner'",
                                  (run["source_message_id"], run["conversation_id"])).fetchone()
            if sequence is None:
                raise ValueError("源消息必须是本会话中的 Owner 消息")
            if retrospective := retrospectives.snapshot(db, run, agent, instructions, self.memories):
                return retrospective
            if proposal := planning.snapshot(db, run, agent, instructions):
                return proposal
            # ponytail: bound context to 100 messages; add token budgeting if large inputs require it.
            rows = list(db.execute("""SELECT * FROM messages WHERE conversation_id=? AND sequence<=?
                ORDER BY sequence DESC LIMIT 101""", (run["conversation_id"], sequence[0])))
            return {"agent": agent, "instructions": instructions,
                    "messages": [dict(row) for row in reversed(rows[:100])],
                    "context_truncated": len(rows) > 100, "source_sequence": sequence[0]}

    def finish(self, identity, content, model, prompt_tokens, completion_tokens):
        content = _text(content, "模型回复", 16000)
        model = _text(model, "模型", 200)
        for count in (prompt_tokens, completion_tokens):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Token 数量必须为非负整数或 null")
        usage = json.dumps({"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            if run["state"] != "running":
                return run
            if run['usage'] is not None and (run['model'] != model or run['usage'] != json.loads(usage)):
                raise ValueError('模型用量回执与已保存记录冲突')
            self._authorize(db, run)
            if retrospectives.finish(db, run, content, self.memories) or planning.finish(db, run, content):
                db.execute("""UPDATE runs SET state='completed',model=?,usage=?,error=NULL,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""", (model, usage, identity))
                return self._run(db, identity)
            peer_reviews.check(db, run)
            reply_id = str(uuid.uuid4())
            db.execute("INSERT INTO messages(id,conversation_id,sender_kind,sender_id,content,request_id) VALUES(?,?,'agent',?,?,?)",
                       (reply_id, run["conversation_id"], run["agent_id"], content, str(uuid.uuid4())))
            db.execute("""UPDATE runs SET state='completed', model=?, usage=?, error=NULL,
                reply_message_id=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                       (model, usage, reply_id, identity))
            db.execute("UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (run["conversation_id"],))
            return self._run(db, identity)

    def record_usage(self, identity, receipt):
        """Persist provider-reported tokens independently of publishing its output."""
        if not isinstance(receipt, dict) or set(receipt) != {'model', 'prompt_tokens', 'completion_tokens'}:
            raise ValueError('模型用量回执字段无效')
        model = _text(receipt['model'], '模型', 200)
        tokens = {key: receipt[key] for key in ('prompt_tokens', 'completion_tokens')}
        if any(value is not None and (type(value) is not int or value < 0) for value in tokens.values()):
            raise ValueError('Token 数量必须为非负整数或 null')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, identity)
            if run['usage'] is not None:
                if run['model'] != model or run['usage'] != tokens:
                    raise ValueError('模型用量回执与已保存记录冲突')
                return
            if run['state'] != 'running':
                raise ValueError('只有运行中的请求可以记录首次用量回执')
            db.execute('UPDATE runs SET model=?,usage=? WHERE id=?', (model, json.dumps(tokens), identity))

    def fail(self, identity, error, state="failed"):
        if state not in ("failed", "unknown"):
            raise ValueError("无效的失败状态")
        error = _text(error, "错误", 2000)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._run(db, identity)
            db.execute("""UPDATE runs SET state=?, error=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND state IN ('queued','running')""", (state, error, identity))
            return self._run(db, identity)

    def cancel(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            if run["state"] == "running":
                raise ValueError("模型请求已开始，不能保证停止，无法取消")
            db.execute("""UPDATE runs SET state='cancelled', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND state='queued'""", (identity,))
            return self._run(db, identity)

    def recover(self):
        with self.store.connect() as db:
            return db.execute("""UPDATE runs SET state='unknown', error='服务重启，模型请求结果未知；未自动重发',
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE state='running'""").rowcount
