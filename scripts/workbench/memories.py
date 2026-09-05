"""Owner-approved scope documents and immutable execution memory bindings."""
import json
import uuid

from .store import Store, _text
from .tasks import Tasks, TaskVersionConflict
from . import dependencies
from .artifacts import input_snapshots


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS memory_candidates (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('agent','project')),
        scope_id TEXT NOT NULL, expected_version INTEGER NOT NULL CHECK(expected_version>=0),
        source_execution_id TEXT NOT NULL REFERENCES task_executions(id),
        source_task_id TEXT NOT NULL REFERENCES tasks(id), source_requirement_version INTEGER NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    db.execute("""CREATE TABLE IF NOT EXISTS memory_revisions (
        scope TEXT NOT NULL CHECK(scope IN ('agent','project')), scope_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version>0), content TEXT NOT NULL,
        candidate_id TEXT REFERENCES memory_candidates(id), target_version INTEGER, note TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(scope,scope_id,version))""")
    db.execute("""CREATE TABLE IF NOT EXISTS memory_decisions (
        candidate_id TEXT PRIMARY KEY REFERENCES memory_candidates(id), request_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')), note TEXT NOT NULL,
        result_version INTEGER NOT NULL,
        decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    db.execute("""CREATE TABLE IF NOT EXISTS memory_requests (
        request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, response TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_memory_snapshots (
        execution_id TEXT NOT NULL REFERENCES task_executions(id),
        scope TEXT NOT NULL CHECK(scope IN ('agent','project')), scope_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version>=0), PRIMARY KEY(execution_id,scope))""")
    keys = {"memory_candidates": "id=NEW.id", "memory_revisions":
            "scope=NEW.scope AND scope_id=NEW.scope_id AND version=NEW.version",
            "memory_decisions": "candidate_id=NEW.candidate_id", "memory_requests": "request_id=NEW.request_id",
            "execution_memory_snapshots": "execution_id=NEW.execution_id AND scope=NEW.scope"}
    for table, key in keys.items():
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Memory records are immutable'); END""")
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM {table} WHERE {key})
            BEGIN SELECT RAISE(ABORT,'Memory records are immutable'); END""")


def _scope(db, scope, identity, write=False):
    if scope == "agent":
        row = db.execute("SELECT enabled FROM agents WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("Agent 不存在")
        if write and not row[0]:
            raise ValueError("Agent 已停用")
    elif scope == "project":
        conversation = Store._conversation(db, identity)
        if conversation["type"] == "dm":
            raise ValueError("私聊没有项目共享记忆")
        if write and conversation["archived"]:
            raise ValueError("会话已归档")
    else:
        raise ValueError("记忆范围必须为 agent 或 project")


def _document(db, scope, identity, version=None):
    if version == 0:
        return {"scope": scope, "scope_id": identity, "version": 0, "content": ""}
    sql = "SELECT scope,scope_id,version,content FROM memory_revisions WHERE scope=? AND scope_id=?"
    args = [scope, identity]
    if version is not None:
        sql += " AND version=?"; args.append(version)
    row = db.execute(sql + " ORDER BY version DESC LIMIT 1", args).fetchone()
    if row is None and version is not None:
        raise ValueError("记忆版本不存在")
    return dict(row) if row else _document(db, scope, identity, 0)


def _scopes(db, run, conversation_id):
    task = Tasks._task(db, run["task_id"])
    if task["conversation_id"] != conversation_id:
        raise PermissionError("记忆会话不匹配")
    conversation = Store._conversation(db, conversation_id, run["agent_id"])
    scopes = [("agent", run["agent_id"])]
    if conversation["type"] != "dm":
        scopes.append(("project", conversation_id))
    return scopes


def freeze(db, run, conversation_id):
    for scope, identity in _scopes(db, run, conversation_id):
        version = _document(db, scope, identity)["version"]
        db.execute("INSERT INTO execution_memory_snapshots VALUES(?,?,?,?)", (run["id"], scope, identity, version))


def snapshot(db, run, conversation_id):
    expected = _scopes(db, run, conversation_id)
    rows = db.execute("SELECT scope,scope_id,version FROM execution_memory_snapshots WHERE execution_id=?",
                      (run["id"],)).fetchall()
    if sorted((r["scope"], r["scope_id"]) for r in rows) != sorted(expected):
        raise ValueError("执行缺少有效的记忆版本绑定，不能使用当前记忆替代")
    return [_document(db, r["scope"], r["scope_id"], r["version"]) for r in rows if r["version"]]


def _payload(payload, keys):
    if not isinstance(payload, dict) or set(payload) != set(keys.split()):
        raise ValueError("记忆请求字段不符合要求")
    result = dict(payload)
    for key in ("expected_version", "target_version"):
        if key in result and (type(result[key]) is not int or result[key] < 0):
            raise ValueError("记忆版本必须为非负整数")
    for key, limit in (("request_id", 120), ("source_execution_id", 120), ("content", 8000), ("note", 2000)):
        if key in result:
            result[key] = _text(result[key], key, limit)
    if "decision" in result and result["decision"] not in ("approved", "rejected"):
        raise ValueError("请选择批准或拒绝")
    return result


def _replay(db, operation, target, payload):
    encoded = json.dumps([operation, target, payload], sort_keys=True, ensure_ascii=False)
    row = db.execute("SELECT * FROM memory_requests WHERE request_id=?", (payload["request_id"],)).fetchone()
    if row and row["payload"] != encoded:
        raise ValueError("request_id 已用于不同记忆请求")
    return encoded, json.loads(row["response"]) if row else None


def _record(db, payload, encoded, response):
    db.execute("INSERT INTO memory_requests VALUES(?,?,?)",
               (payload["request_id"], encoded, json.dumps(response, ensure_ascii=False)))
    return response


class Memories:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            initialize(db)

    @staticmethod
    def _candidate(db, identity):
        row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("记忆候选不存在")
        return dict(row)

    def candidate(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            result = self._candidate(db, identity)
            decision = db.execute("SELECT * FROM memory_decisions WHERE candidate_id=?", (identity,)).fetchone()
            return {**result, "decision": dict(decision) if decision else None}

    def get(self, scope, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            _scope(db, scope, identity)
            return _document(db, scope, identity)

    def history(self, scope, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            _scope(db, scope, identity)
            return [dict(row) for row in db.execute("SELECT * FROM memory_revisions WHERE scope=? AND scope_id=? ORDER BY version",
                                                   (scope, identity))]

    def candidates(self, scope, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            _scope(db, scope, identity)
            result = []
            for row in db.execute("SELECT * FROM memory_candidates WHERE scope=? AND scope_id=? ORDER BY created_at,id", (scope, identity)):
                decision = db.execute("SELECT * FROM memory_decisions WHERE candidate_id=?", (row["id"],)).fetchone()
                result.append({**dict(row), "decision": dict(decision) if decision else None})
            return result

    def _source(self, db, scope, identity, execution_id):
        run = db.execute("SELECT * FROM task_executions WHERE id=?", (execution_id,)).fetchone()
        if run is None:
            raise KeyError("来源执行不存在")
        task = Tasks._task(db, run["task_id"])
        if (scope == "agent" and run["agent_id"] != identity or
                scope == "project" and task["conversation_id"] != identity):
            raise PermissionError("记忆来源不属于当前范围")
        if task["requirement_version"] != run["requirement_version"]:
            raise TaskVersionConflict("来源需求版本已过期")
        # Reuse authority methods without constructing schema writers inside this transaction.
        Tasks._authorize(self, db, task["conversation_id"], run["agent_id"])
        tools = json.loads(db.execute("SELECT tools FROM agents WHERE id=?", (run["agent_id"],)).fetchone()[0])
        if "execute" not in tools:
            raise PermissionError("来源 Agent 缺少执行权限")
        latest = db.execute("SELECT id FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1", (run["task_id"],)).fetchone()[0]
        review = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_reviews'").fetchone():
            review = db.execute("SELECT decision,artifact_ids FROM execution_reviews WHERE execution_id=?", (execution_id,)).fetchone()
        if run["state"] != "awaiting_review" or latest != execution_id or review is None or review["decision"] != "approved":
            raise ValueError("记忆来源必须是当前最新且已获 Owner 批准的成果")
        dependencies.check_bound(db, dict(run))
        input_snapshots(db, [{"upstream_execution_id": execution_id}])
        return run

    def propose(self, scope, identity, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._propose(db, scope, identity, payload)

    def _propose(self, db, scope, identity, payload):
        p = _payload(payload, "request_id expected_version source_execution_id content")
        encoded, old = _replay(db, "propose", [scope, identity], p)
        if old is not None:
            return old
        _scope(db, scope, identity, True)
        if _document(db, scope, identity)["version"] != p["expected_version"]:
            raise TaskVersionConflict("记忆版本已变化")
        run = self._source(db, scope, identity, p["source_execution_id"])
        candidate = str(uuid.uuid4())
        db.execute("""INSERT INTO memory_candidates(id,scope,scope_id,expected_version,source_execution_id,
            source_task_id,source_requirement_version,content) VALUES(?,?,?,?,?,?,?,?)""",
            (candidate, scope, identity, p["expected_version"], run["id"], run["task_id"], run["requirement_version"], p["content"]))
        return _record(db, p, encoded, self._candidate(db, candidate))

    def decide(self, identity, payload):
        p = _payload(payload, "request_id decision note")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, old = _replay(db, "decide", identity, p)
            if old is not None:
                return old
            candidate = self._candidate(db, identity)
            if db.execute("SELECT 1 FROM memory_decisions WHERE candidate_id=?", (identity,)).fetchone():
                raise ValueError("候选已有不可覆盖的决定")
            scope, scope_id = candidate["scope"], candidate["scope_id"]
            version = _document(db, scope, scope_id)["version"]
            if p["decision"] == "approved":
                _scope(db, scope, scope_id, True)
                if candidate["expected_version"] != version:
                    raise TaskVersionConflict("记忆版本已变化，请重新提案")
                self._source(db, scope, scope_id, candidate["source_execution_id"])
                version += 1
                db.execute("""INSERT INTO memory_revisions(scope,scope_id,version,content,candidate_id,note)
                    VALUES(?,?,?,?,?,?)""", (scope, scope_id, version, candidate["content"], identity, p["note"]))
            db.execute("INSERT INTO memory_decisions(candidate_id,request_id,decision,note,result_version) VALUES(?,?,?,?,?)",
                       (identity, p["request_id"], p["decision"], p["note"], version))
            result = dict(db.execute("SELECT * FROM memory_decisions WHERE candidate_id=?", (identity,)).fetchone())
            return _record(db, p, encoded, result)

    def rollback(self, scope, identity, payload):
        p = _payload(payload, "request_id expected_version target_version note")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, old = _replay(db, "rollback", [scope, identity], p)
            if old is not None:
                return old
            _scope(db, scope, identity, True)
            version = _document(db, scope, identity)["version"]
            if version != p["expected_version"]:
                raise TaskVersionConflict("记忆版本已变化")
            if p["target_version"] >= version:
                raise ValueError("回滚目标必须为更早版本")
            content = _document(db, scope, identity, p["target_version"])["content"]
            db.execute("""INSERT INTO memory_revisions(scope,scope_id,version,content,target_version,note)
                VALUES(?,?,?,?,?,?)""", (scope, identity, version + 1, content, p["target_version"], p["note"]))
            result = dict(db.execute("SELECT * FROM memory_revisions WHERE scope=? AND scope_id=? AND version=?",
                                    (scope, identity, version + 1)).fetchone())
            return _record(db, p, encoded, result)
