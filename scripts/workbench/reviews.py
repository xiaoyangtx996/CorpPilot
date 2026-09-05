"""Owner decisions over exact, current execution artifacts; never a runner callback."""
import json

from .executions import Executions
from .store import _text
from .tasks import TaskVersionConflict
from .artifacts import verify_snapshot, MAX_TOTAL_BYTES


class Reviews:
    def __init__(self, store):
        self.store = store
        self.executions = Executions(store)
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS execution_reviews (
                execution_id TEXT PRIMARY KEY REFERENCES task_executions(id),
                request_id TEXT NOT NULL, requirement_version INTEGER NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
                note TEXT NOT NULL, artifact_ids TEXT NOT NULL,
                reviewed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS reviews_no_{operation.lower()}
                    BEFORE {operation} ON execution_reviews BEGIN
                    SELECT RAISE(ABORT, 'Reviews are immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS reviews_no_replace
                BEFORE INSERT ON execution_reviews WHEN EXISTS
                    (SELECT 1 FROM execution_reviews WHERE execution_id=NEW.execution_id)
                BEGIN SELECT RAISE(ABORT, 'Reviews are immutable'); END""")

    @staticmethod
    def _review(db, identity):
        row = db.execute("SELECT * FROM execution_reviews WHERE execution_id=?", (identity,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["artifact_ids"] = json.loads(result["artifact_ids"])
        return result

    def get(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.executions._run(db, identity)
            return self._review(db, identity)

    def save(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {"request_id", "expected_version", "decision", "note", "artifact_ids"}:
            raise ValueError("评审请求必须只含 request_id、expected_version、decision、note、artifact_ids")
        request_id = _text(payload["request_id"], "请求 ID", 120)
        note = _text(payload["note"], "评审说明", 2000)
        version = payload["expected_version"]
        if type(version) is not int or version < 1:
            raise ValueError("expected_version 必须为正整数")
        decision = payload["decision"]
        if decision not in ("approved", "rejected"):
            raise ValueError("请选择批准或拒绝")
        identifiers = payload["artifact_ids"]
        if (not isinstance(identifiers, list) or len(identifiers) > 100
                or any(not isinstance(item, str) or not item or len(item) > 120 for item in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ValueError("成果 ID 清单无效")
        identifiers = sorted(identifiers)
        values = {"request_id": request_id, "requirement_version": version,
                  "decision": decision, "note": note, "artifact_ids": identifiers}
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self.executions._run(db, identity)
            previous = self._review(db, identity)
            if previous is not None:
                if any(previous[key] != value for key, value in values.items()):
                    raise ValueError("此次执行已有不可覆盖的评审决定")
                return previous
            if run["state"] != "awaiting_review":
                raise ValueError("只有待评审的执行可提交决定")
            if run["requirement_version"] != version:
                raise TaskVersionConflict("评审需求版本与执行不一致")
            self.executions._authorize(db, run)
            latest = db.execute("SELECT id FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                                (run["task_id"],)).fetchone()[0]
            if latest != identity:
                raise ValueError("已有更新的执行，请评审最新结果")
            rows = db.execute("SELECT * FROM execution_artifacts WHERE execution_id=? ORDER BY id",
                              (identity,)).fetchall()
            if [row["id"] for row in rows] != identifiers:
                raise ValueError("成果清单发生变化，请重新读取后评审")
            if decision == "approved":
                if not rows:
                    raise ValueError("没有已保存成果，不能批准交付")
                if sum(len(verify_snapshot(row)["data"]) for row in rows) > MAX_TOTAL_BYTES:
                    raise ValueError("成果总量超过限制，不能批准")
            db.execute("""INSERT INTO execution_reviews
                (execution_id,request_id,requirement_version,decision,note,artifact_ids) VALUES(?,?,?,?,?,?)""",
                       (identity, request_id, version, decision, note, json.dumps(identifiers)))
            return self._review(db, identity)
