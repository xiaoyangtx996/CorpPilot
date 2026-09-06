"""Task execution authority. Only a trusted runner may claim or report exits."""
from __future__ import annotations

import uuid
import json

from .store import Store, _text
from .tasks import Tasks, TaskVersionConflict
from . import artifacts as artifact_store
from . import dependencies
from . import memories
from . import reconciliations
from . import budgets
from . import repo_sources
from . import code_reviews
from . import skill_inputs

ACTIVE = ("queued", "running", "stopping")


class Executions:
    def __init__(self, store: Store):
        self.store = store
        self.tasks = Tasks(store)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS executions_schema_version (version INTEGER PRIMARY KEY)")
            versions = [row[0] for row in db.execute("SELECT version FROM executions_schema_version")]
            if versions and versions != [1]:
                raise ValueError("不支持的任务执行数据库版本")
            db.execute("INSERT OR IGNORE INTO executions_schema_version VALUES (1)")
            db.execute("""CREATE TABLE IF NOT EXISTS task_executions (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                agent_id TEXT NOT NULL REFERENCES agents(id),
                requirement_version INTEGER NOT NULL, attempt INTEGER NOT NULL CHECK(attempt>0),
                request_id TEXT NOT NULL, reconciliation_note TEXT NOT NULL,
                previous_execution_id TEXT REFERENCES task_executions(id),
                state TEXT NOT NULL CHECK(state IN
                    ('queued','running','stopping','awaiting_review','failed','cancelled','unknown','superseded')),
                exit_code INTEGER, summary TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(task_id,request_id), UNIQUE(task_id,attempt),
                FOREIGN KEY(task_id,requirement_version) REFERENCES task_revisions(task_id,requirement_version)
            )""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_task_execution ON task_executions(task_id)
                WHERE state IN ('queued','running','stopping')""")
            artifact_store.initialize(db)
            dependencies.initialize_inputs(db)
            memories.initialize(db)
            reconciliations.initialize(db)
            budgets.initialize(db)
            skill_inputs.initialize(db)
            repo_sources.initialize(db)
            code_reviews.initialize(db)
            db.execute('''CREATE TABLE IF NOT EXISTS execution_usage (
                execution_id TEXT PRIMARY KEY REFERENCES task_executions(id),
                attempt INTEGER NOT NULL, requirement_version INTEGER NOT NULL, usage TEXT NOT NULL)''')
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS execution_usage_no_{operation.lower()}
                    BEFORE {operation} ON execution_usage BEGIN SELECT RAISE(ABORT,'Execution usage is immutable'); END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS execution_usage_no_replace BEFORE INSERT ON execution_usage
                WHEN EXISTS(SELECT 1 FROM execution_usage WHERE execution_id=NEW.execution_id)
                BEGIN SELECT RAISE(ABORT,'Execution usage is immutable'); END''')

    @staticmethod
    def _run(db, identity):
        row = db.execute("SELECT * FROM task_executions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("任务执行不存在")
        return {**dict(row), 'usage': Executions._usage(db, identity)}

    @staticmethod
    def _usage(db, identity):
        row = db.execute('SELECT usage FROM execution_usage WHERE execution_id=?', (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_usage(self, identity, attempt, version, usage):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._record_usage(db, identity, attempt, version, usage)

    def _record_usage(self, db, identity, attempt, version, usage):
        if not isinstance(usage, dict) or set(usage) != {'input_tokens', 'output_tokens', 'cached_input_tokens'}:
            raise ValueError('CLI 用量回执字段无效')
        if any(value is not None and (type(value) is not int or value < 0) for value in usage.values()):
            raise ValueError('CLI Token 须为非负整数或 null')
        if usage['input_tokens'] is not None and usage['cached_input_tokens'] is not None and usage['cached_input_tokens'] > usage['input_tokens']:
            raise ValueError('缓存 Token 不能超过输入 Token')
        if any(type(value) is not int or value < 1 for value in (attempt, version)):
            raise ValueError('执行用量绑定无效')
        run = self._run(db, identity)
        if (run['attempt'], run['requirement_version']) != (attempt, version):
            raise ValueError('执行用量绑定不一致')
        if run['usage'] is not None:
            if run['usage'] != usage: raise ValueError('CLI 用量回执冲突')
            return
        if run['state'] not in ('running', 'stopping'):
            raise ValueError('仅运行中的执行能保存首次用量')
        db.execute('INSERT INTO execution_usage VALUES(?,?,?,?)', (identity, attempt, version, json.dumps(usage)))

    @staticmethod
    def _set(db, identity, state, summary, exit_code=None):
        db.execute("""UPDATE task_executions SET state=?,summary=?,exit_code=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                   (state, summary, exit_code, identity))

    def _authorize(self, db, run, *, dispatch=False):
        code_reviews.authorize(self.store, db, run)
        repo_sources.authorize(self.store, db, run.get('id'))
        task = self.tasks._task(db, run["task_id"])
        if task["requirement_version"] != run["requirement_version"]:
            raise TaskVersionConflict("执行需求版本已过期")
        self.tasks._authorize(db, task["conversation_id"], run["agent_id"])
        tools = json.loads(db.execute("SELECT tools FROM agents WHERE id=?", (run["agent_id"],)).fetchone()[0])
        if "execute" not in tools:
            raise PermissionError("负责人尚未获得 execute 工具权限")
        # Dispatch writes artifact files; historical reports/reviews retain their existing authority checks.
        if dispatch:
            missing = [tool for tool in ("read", "write") if tool not in tools]
            if missing:
                raise PermissionError("CLI 读取任务资料并写入成果，负责人尚未获得 " + "、".join(missing) + " 工具权限")
        if run.get("id") and run.get("state") != "queued":
            dependencies.check_bound(db, run)
        return task

    def create(self, task_id, payload):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._create(db, task_id, payload)

    def _create(self, db, task_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"request_id", "expected_version", "reconciliation_note", "previous_execution_id"}:
            raise ValueError("执行请求必须只含 request_id、expected_version、reconciliation_note、previous_execution_id")
        request = _text(payload["request_id"], "请求 ID", 120)
        version = payload["expected_version"]
        if type(version) is not int or version < 1:
            raise ValueError("expected_version 必须为正整数")
        note = payload["reconciliation_note"]
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("执行结果核查说明必须为最多2000字符的文本")
        note = note.strip()
        prior_id = payload["previous_execution_id"]
        if prior_id is not None:
            prior_id = _text(prior_id, "前次执行 ID")
        previous = db.execute("SELECT * FROM task_executions WHERE task_id=? AND request_id=?",
                              (task_id, request)).fetchone()
        if previous:
            if (previous["requirement_version"], previous["reconciliation_note"], previous["previous_execution_id"]) != (version, note, prior_id):
                raise ValueError("request_id 已用于不同任务执行")
            return self._run(db, previous['id'])
        task = self.tasks._task(db, task_id)
        if reconciliations.unresolved(db, task_id):
            raise ValueError("本任务存在未核实的执行，不能启动替代实例")
        self._authorize(db, {"task_id": task_id, "requirement_version": version, "agent_id": task["agent_id"]}, dispatch=True)
        if db.execute("SELECT 1 FROM task_executions WHERE task_id=? AND state IN ('queued','running','stopping')",
                      (task_id,)).fetchone():
            raise ValueError("任务仍有排队或未确认停止的执行")
        latest = db.execute("SELECT id,attempt FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1", (task_id,)).fetchone()
        if prior_id != (latest["id"] if latest else None):
            raise ValueError("必须核对并引用本任务最近一次执行")
        attempt = latest["attempt"] + 1 if latest else 1
        if attempt > 1 and not note:
            raise ValueError("再次执行前必须记录已核查的结果、副作用及重试理由")
        if db.execute("SELECT count(*) FROM task_executions WHERE state IN ('queued','running','stopping')").fetchone()[0] >= 100:
            raise ValueError("任务执行队列已满")
        identity = str(uuid.uuid4())
        db.execute("""INSERT INTO task_executions(id,task_id,agent_id,requirement_version,attempt,
            request_id,reconciliation_note,previous_execution_id,state) VALUES(?,?,?,?,?,?,?,?,'queued')""",
                   (identity, task_id, task["agent_id"], version, attempt, request, note, prior_id))
        repo_sources.freeze(self.store, db, identity, task['conversation_id'])
        return self._run(db, identity)

    def get(self, identity):
        with self.store.connect() as db:
            return self._run(db, identity)

    def list(self, task_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.tasks._task(db, task_id)
            return [self._run(db, row['id']) for row in db.execute(
                "SELECT * FROM task_executions WHERE task_id=? ORDER BY attempt DESC", (task_id,))]

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("队列读取上限必须为1–100")
        with self.store.connect() as db:
            return [self._run(db, row['id']) for row in db.execute(
                "SELECT * FROM task_executions WHERE state='queued' ORDER BY created_at,id LIMIT ?", (limit,))]

    def claim(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            if run["state"] != "queued":
                return False
            if reconciliations.unresolved(db, run["task_id"]):
                return False
            try:
                self._authorize(db, run, dispatch=True)
            except TaskVersionConflict:
                self._set(db, identity, "superseded", "需求已修改，此次排队未执行")
                return False
            except (ValueError, PermissionError, KeyError):
                self._set(db, identity, "failed", "负责人或会话权限已变化，此次排队未执行")
                return False
            try:
                inputs = dependencies.ready_inputs(db, run["task_id"], run["requirement_version"], identity)
            except dependencies.DependencyBlocked:
                return False
            try:
                skill_inputs.freeze(db, 'cli', run)
            except (ValueError, PermissionError, KeyError):
                self._set(db, identity, 'failed', '绑定 Skill 不可用，此次排队未执行')
                return False
            budgets.reserve(db, 'cli', run)
            db.executemany("INSERT INTO execution_inputs VALUES(?,?,?)",
                           [(identity, item["dependency_task_id"], item["upstream_execution_id"]) for item in inputs])
            task = self.tasks._task(db, run["task_id"])
            memories.freeze(db, run, task["conversation_id"])
            self._set(db, identity, "running", None)
            return True

    def snapshot(self, identity, *, include_artifacts=False):
        with self.store.connect() as db:
            db.execute("BEGIN")
            run = self._run(db, identity)
            if run["state"] != "running":
                raise ValueError("只有运行中的执行可取得上下文")
            task = self._authorize(db, run, dispatch=True)
            agent = self.store._agent(db.execute("SELECT * FROM agents WHERE id=?", (run["agent_id"],)).fetchone())
            instructions = db.execute("SELECT instructions FROM templates WHERE id=?", (agent["template_id"],)).fetchone()[0]
            frozen = skill_inputs.snapshot(db, 'cli', run)
            if not {s['id'] for s in frozen['skills']} <= set(agent['skills']):
                raise PermissionError('本次绑定 Skill 已撤销，未准备 CLI 输入')
            instructions = skill_inputs.augment(instructions, frozen)
            source = db.execute("SELECT * FROM messages WHERE id=?", (task["source_message_id"],)).fetchone()
            # Only explicit task requirements and their source, never all private conversations.
            bindings = dependencies.bound_inputs(db, identity)
            result = {"task": task, "agent": agent, "instructions": instructions, "source_message": dict(source),
                      "dependency_inputs": bindings, "skills": frozen['skills']}
            if include_artifacts:
                result["input_artifacts"] = artifact_store.input_snapshots(db, bindings, downstream_execution_id=identity)
                result["memories"] = memories.snapshot(db, run, task["conversation_id"])
                repository = repo_sources.bound(db, identity)
                if repository is not None:
                    result['repository'] = repository
            return result

    def cancel(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._cancel(db, identity)

    def _cancel(self, db, identity):
        run = self._run(db, identity)
        if run["state"] == "queued":
            self._set(db, identity, "cancelled", "排队取消，未启动执行")
        elif run["state"] == "running":
            self._set(db, identity, "stopping", "已请求停止，尚未确认执行实例退出")
        return self._run(db, identity)

    def recover(self):
        """Call only under the controller's exclusive lifetime lock after restart.

        This records uncertainty, not proof that an orphan process has stopped.
        Admission stays paused until the Owner records a stopped-process and
        external-effects check. That declaration is not a machine exit result.
        """
        with self.store.connect() as db:
            return db.execute("""UPDATE task_executions SET state='unknown',
                summary='控制服务重启；实例和副作用尚未核查，禁止自动重试',
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE state IN ('running','stopping')""").rowcount

    def report(self, identity, attempt, requirement_version, exit_code, summary, *, success=False, not_started=False, artifacts=None, usage=None):
        """Internal runner callback: a numeric exit requires observed process-tree termination.

        None means termination/result is unverified. Never expose this as an Owner HTTP mutation.
        Exit zero plus explicit success only admits review; it never approves artifacts.
        not_started is reserved for a trusted pre-launch path, never an arbitrary exception.
        """
        for number in (attempt, requirement_version):
            if type(number) is not int or number < 1:
                raise ValueError("回调尝试与需求版本必须为正整数")
        if exit_code is not None and type(exit_code) is not int:
            raise ValueError("退出码必须为整数或null")
        if type(success) is not bool or type(not_started) is not bool or (not_started and (success or exit_code is not None)):
            raise ValueError("执行结果标志无效")
        summary = _text(summary, "执行摘要", 2000)
        if usage is not None:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                run = self._run(db, identity)
                if (run['attempt'], run['requirement_version']) != (attempt, requirement_version) or run['state'] not in ('running', 'stopping'):
                    return run
                self._record_usage(db, identity, attempt, requirement_version, usage)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, identity)
            if (run["attempt"], run["requirement_version"]) != (attempt, requirement_version) or run["state"] not in ("running", "stopping"):
                return run
            if exit_code is None and not not_started:
                state = "unknown"
            elif run["state"] == "stopping":
                state = "cancelled"
            else:
                try:
                    self._authorize(db, run)
                    state = "awaiting_review" if exit_code == 0 and success else "failed"
                except TaskVersionConflict:
                    state = "superseded"
                except dependencies.DependencyBlocked:
                    state = "failed"
                    summary = "前置成果已变化，当前结果不能提交验收。" + summary[:1900]
                except (ValueError, PermissionError, KeyError):
                    state = "failed"
                    summary = "执行后负责人或会话权限已撤销，结果不能提交验收。" + summary[:1900]
            self._set(db, identity, state, summary, exit_code)
            if state == "awaiting_review" and artifacts is not None:
                artifact_store.persist(db, identity, artifacts)
            return self._run(db, identity)
