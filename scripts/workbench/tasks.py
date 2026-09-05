"""Versioned requirements, independent of model runs and execution state."""
from __future__ import annotations

import json
import uuid

from .store import Store, _text


class TaskVersionConflict(ValueError):
    """The caller must read the current requirements before revising again."""


class Tasks:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS tasks_schema_version (version INTEGER PRIMARY KEY)")
            versions = [row[0] for row in db.execute("SELECT version FROM tasks_schema_version")]
            if versions and versions != [1]:
                raise ValueError("不支持的 Task 数据库版本")
            db.execute("INSERT OR IGNORE INTO tasks_schema_version VALUES (1)")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                source_message_id TEXT NOT NULL REFERENCES messages(id),
                request_id TEXT NOT NULL, creation_payload TEXT NOT NULL,
                requirement_version INTEGER NOT NULL CHECK(requirement_version>=1),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(conversation_id,request_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS task_revisions (
                task_id TEXT NOT NULL REFERENCES tasks(id),
                requirement_version INTEGER NOT NULL CHECK(requirement_version>=1),
                title TEXT NOT NULL, scope TEXT NOT NULL, acceptance TEXT NOT NULL,
                agent_id TEXT NOT NULL REFERENCES agents(id),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY(task_id,requirement_version)
            )""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS task_revisions_no_{operation.lower()}
                    BEFORE {operation} ON task_revisions BEGIN
                    SELECT RAISE(ABORT, 'Task revisions are immutable'); END""")

    @staticmethod
    def _task(db, identity):
        row = db.execute("""SELECT t.id,t.conversation_id,t.source_message_id,t.request_id,
            t.requirement_version,r.title,r.scope,r.acceptance,r.agent_id,t.created_at,t.updated_at
            FROM tasks t JOIN task_revisions r ON r.task_id=t.id
            AND r.requirement_version=t.requirement_version WHERE t.id=?""", (identity,)).fetchone()
        if row is None:
            raise KeyError("Task 不存在")
        return dict(row)

    @staticmethod
    def _requirements(payload):
        return (_text(payload["title"], "标题"), _text(payload["scope"], "范围", 16000),
                _text(payload["acceptance"], "验收标准", 16000), _text(payload["agent_id"], "Agent ID"))

    def _authorize(self, db, conversation_id, agent_id):
        conversation = self.store._conversation(db, conversation_id, agent_id)
        self.store._enabled_member(db, agent_id)
        if conversation["archived"]:
            raise ValueError("会话已归档，不能更改任务")

    def create(self, conversation_id, payload):
        if not isinstance(payload, dict) or set(payload) != {
                "source_message_id", "request_id", "title", "scope", "acceptance", "agent_id"}:
            raise ValueError("Task 必须只含 source_message_id、request_id、title、scope、acceptance、agent_id")
        conversation_id = _text(conversation_id, "会话 ID")
        source = _text(payload["source_message_id"], "源消息 ID")
        request = _text(payload["request_id"], "请求 ID", 120)
        fields = self._requirements(payload)
        original = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id,creation_payload FROM tasks WHERE conversation_id=? AND request_id=?",
                                  (conversation_id, request)).fetchone()
            if previous:
                if previous["creation_payload"] != original:
                    raise ValueError("request_id 已用于不同 Task")
                return self._task(db, previous["id"])
            self._authorize(db, conversation_id, fields[3])
            if not db.execute("SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND sender_kind='owner'",
                              (source, conversation_id)).fetchone():
                raise ValueError("源消息必须是本会话中的 Owner 消息")
            identity = str(uuid.uuid4())
            db.execute("""INSERT INTO tasks(id,conversation_id,source_message_id,request_id,creation_payload,
                requirement_version) VALUES(?,?,?,?,?,1)""", (identity, conversation_id, source, request, original))
            db.execute("""INSERT INTO task_revisions(task_id,requirement_version,title,scope,acceptance,agent_id)
                VALUES(?,1,?,?,?,?)""", (identity, *fields))
            return self._task(db, identity)

    def get(self, identity):
        with self.store.connect() as db:
            return self._task(db, _text(identity, "Task ID"))

    def list(self, conversation_id):
        conversation_id = _text(conversation_id, "会话 ID")
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.store._conversation(db, conversation_id)
            return [self._task(db, row[0]) for row in db.execute(
                "SELECT id FROM tasks WHERE conversation_id=? ORDER BY created_at DESC,id DESC", (conversation_id,))]

    def history(self, identity):
        identity = _text(identity, "Task ID")
        with self.store.connect() as db:
            db.execute("BEGIN")
            self._task(db, identity)
            return [dict(row) for row in db.execute(
                "SELECT * FROM task_revisions WHERE task_id=? ORDER BY requirement_version", (identity,))]

    def revise(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {"expected_version", "title", "scope", "acceptance", "agent_id"}:
            raise ValueError("修订必须只含 expected_version、title、scope、acceptance、agent_id")
        expected = payload["expected_version"]
        if type(expected) is not int or expected < 1:
            raise ValueError("expected_version 必须为正整数")
        identity = _text(identity, "Task ID")
        fields = self._requirements(payload)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._task(db, identity)
            if task["requirement_version"] != expected:
                raise TaskVersionConflict("任务版本冲突，请重新读取最新版本")
            self._authorize(db, task["conversation_id"], fields[3])
            if fields == tuple(task[key] for key in ("title", "scope", "acceptance", "agent_id")):
                return task
            db.execute("""INSERT INTO task_revisions(task_id,requirement_version,title,scope,acceptance,agent_id)
                VALUES(?,?,?,?,?,?)""", (identity, expected + 1, *fields))
            db.execute("""UPDATE tasks SET requirement_version=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND requirement_version=?""", (expected + 1, identity, expected))
            return self._task(db, identity)
