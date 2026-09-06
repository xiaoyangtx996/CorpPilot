"""Immutable Owner declarations about unknown runs, never machine exit evidence."""
from .store import _text
from .tasks import TaskVersionConflict


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS execution_reconciliations (
        execution_id TEXT PRIMARY KEY REFERENCES task_executions(id),
        request_id TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt > 0),
        requirement_version INTEGER NOT NULL CHECK(requirement_version > 0),
        process_stopped INTEGER NOT NULL CHECK(process_stopped = 1),
        external_effects_checked INTEGER NOT NULL CHECK(external_effects_checked = 1),
        note TEXT NOT NULL,
        reconciled_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS reconciliations_no_{operation.lower()}
            BEFORE {operation} ON execution_reconciliations BEGIN
            SELECT RAISE(ABORT, 'Reconciliations are immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS reconciliations_no_replace
        BEFORE INSERT ON execution_reconciliations WHEN EXISTS
            (SELECT 1 FROM execution_reconciliations WHERE execution_id=NEW.execution_id)
        BEGIN SELECT RAISE(ABORT, 'Reconciliations are immutable'); END""")


def unresolved(db, task_id=None):
    return db.execute("""SELECT 1 FROM task_executions e
        WHERE e.state='unknown' AND NOT EXISTS
            (SELECT 1 FROM execution_reconciliations r WHERE r.execution_id=e.id)
        """ + ("AND e.task_id=? " if task_id is not None else "") + "LIMIT 1",
        (task_id,) if task_id is not None else ()).fetchone() is not None


class Reconciliations:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            initialize(db)

    @staticmethod
    def _run(db, identity):
        row = db.execute("SELECT * FROM task_executions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("执行不存在")
        return row

    @staticmethod
    def _get(db, identity):
        row = db.execute("SELECT * FROM execution_reconciliations WHERE execution_id=?", (identity,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("process_stopped", "external_effects_checked"):
            result[field] = bool(result[field])
        return result

    def get(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self._run(db, identity)
            return self._get(db, identity)

    def pending(self, limit=100):
        from .executions import Executions
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit 必须为 1 到 100 的整数")
        with self.store.connect() as db:
            return [Executions._run(db, row['id']) for row in db.execute("""SELECT e.* FROM task_executions e
                WHERE e.state='unknown' AND NOT EXISTS
                    (SELECT 1 FROM execution_reconciliations r WHERE r.execution_id=e.id)
                ORDER BY e.created_at,e.id LIMIT ?""", (limit,))]

    def save(self, identity, payload):
        fields = {"request_id", "attempt", "requirement_version", "process_stopped", "external_effects_checked", "note"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("核查请求必须只含 request_id、attempt、requirement_version、process_stopped、external_effects_checked、note")
        values = {**payload, "request_id": _text(payload["request_id"], "请求 ID", 120),
                  "note": _text(payload["note"], "核查说明", 2000)}
        for field in ("attempt", "requirement_version"):
            if type(values[field]) is not int or values[field] < 1:
                raise ValueError(f"{field} 必须为正整数")
        for field in ("process_stopped", "external_effects_checked"):
            if values[field] is not True:
                raise ValueError("必须确认进程已停止并已核查外部影响")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            previous = self._get(db, identity)
            if previous is not None:
                if any(previous[key] != value for key, value in values.items()):
                    raise ValueError("此次执行已有不可覆盖的核查记录")
                return previous
            if run["state"] != "unknown":
                raise ValueError("只有状态未知的执行可提交人工核查")
            if any(run[field] != values[field] for field in ("attempt", "requirement_version")):
                raise TaskVersionConflict("核查 attempt 或需求版本与原执行不一致")
            db.execute("""INSERT INTO execution_reconciliations
                (execution_id,request_id,attempt,requirement_version,process_stopped,external_effects_checked,note)
                VALUES(?,?,?,?,?,?,?)""", (identity, values["request_id"], values["attempt"],
                    values["requirement_version"], True, True, values["note"]))
            return self._get(db, identity)
