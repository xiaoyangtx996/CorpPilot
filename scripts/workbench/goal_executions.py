"""One frozen Owner mandate connects one planning Run to one existing launch."""
import json
import threading
import uuid

from .planning import Planning, _json
from .store import _text


class GoalExecutions:
    def __init__(self, store, cli):
        self.store, self.cli = store, cli
        self.planning = Planning(store)
        self.lock = threading.RLock()
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS goal_executions (
                id TEXT PRIMARY KEY, source_conversation_id TEXT NOT NULL REFERENCES conversations(id),
                request_id TEXT NOT NULL, payload TEXT NOT NULL,
                planning_run_id TEXT NOT NULL UNIQUE REFERENCES planning_requests(run_id),
                launch_request_id TEXT NOT NULL UNIQUE,
                launch_id TEXT UNIQUE REFERENCES project_launches(id),
                stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
                stop_applied INTEGER NOT NULL DEFAULT 0 CHECK(stop_applied IN (0,1)),
                failure_state TEXT CHECK(failure_state IN ('failed','unknown')), error TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(source_conversation_id,request_id))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS goals_no_delete BEFORE DELETE ON goal_executions
                BEGIN SELECT RAISE(ABORT,'Goal authority is immutable'); END""")

            # Recheck all candidates inside the actual launch transaction, including unused ones.
            db.execute("""CREATE TRIGGER IF NOT EXISTS goal_launch_guard BEFORE INSERT ON project_launches
                WHEN EXISTS(SELECT 1 FROM goal_executions g
                    WHERE g.source_conversation_id=NEW.source_conversation_id AND g.launch_request_id=NEW.request_id
                    AND (g.stop_requested=1 OR g.failure_state IS NOT NULL OR EXISTS(
                        SELECT 1 FROM json_each(g.payload,'$.candidate_ids') c
                        LEFT JOIN agents a ON a.id=c.value WHERE a.id IS NULL OR a.enabled!=1)))
                BEGIN SELECT RAISE(ABORT,'Goal authority changed before launch'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS goals_no_replace BEFORE INSERT ON goal_executions
                WHEN EXISTS(SELECT 1 FROM goal_executions WHERE id=NEW.id OR planning_run_id=NEW.planning_run_id
                    OR launch_request_id=NEW.launch_request_id OR
                    (source_conversation_id=NEW.source_conversation_id AND request_id=NEW.request_id))
                BEGIN SELECT RAISE(ABORT,'Goal authority is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS goals_guard_update BEFORE UPDATE ON goal_executions
                WHEN NEW.id IS NOT OLD.id OR NEW.source_conversation_id IS NOT OLD.source_conversation_id
                    OR NEW.request_id IS NOT OLD.request_id OR NEW.payload IS NOT OLD.payload
                    OR NEW.planning_run_id IS NOT OLD.planning_run_id OR NEW.launch_request_id IS NOT OLD.launch_request_id
                    OR NEW.created_at IS NOT OLD.created_at OR NEW.stop_requested<OLD.stop_requested
                    OR NEW.stop_applied<OLD.stop_applied OR (OLD.launch_id IS NOT NULL AND NEW.launch_id IS NOT OLD.launch_id)
                    OR (OLD.failure_state IS NOT NULL AND NEW.failure_state IS NOT OLD.failure_state)
                BEGIN SELECT RAISE(ABORT,'Goal authority is immutable'); END""")

    @staticmethod
    def _validate(payload):
        required = {'request_id','source_message_id','agent_id','candidate_ids','shared_brief','max_tasks','confirm_execution','confirm_handoff'}
        if (not isinstance(payload, dict) or set(payload) != required or payload['confirm_execution'] is not True
                or type(payload['confirm_handoff']) is not bool or type(payload['max_tasks']) is not int
                or not 1 <= payload['max_tasks'] <= 16):
            raise ValueError('目标授权字段无效；必须明确执行、交接选择及1–16项任务上限')
        for key, maximum in (('request_id',120),('source_message_id',200),('agent_id',200),('shared_brief',16000)):
            if _text(payload[key], key, maximum) != payload[key]:
                raise ValueError('授权字段不得含首尾空格')
        if not isinstance(payload['candidate_ids'], list) or payload['agent_id'] not in payload['candidate_ids']:
            raise ValueError('协调人必须在明确授权候选中')
        return _json(payload)

    @staticmethod
    def _row(db, identity):
        row = db.execute('SELECT * FROM goal_executions WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError('目标执行不存在')
        return dict(row)

    @staticmethod
    def _expected_launch(row, planning):
        consent = json.loads(row['payload'])
        proposal = planning['proposal']
        if (planning['state'] != 'completed' or not proposal or len(proposal['tasks']) > consent['max_tasks']
                or any(t['agent_id'] not in consent['candidate_ids'] for t in proposal['tasks'])):
            raise ValueError('模型计划超出原任务数或候选授权')
        return {'plan':{**proposal,'shared_brief':consent['shared_brief'], 'source_message_id':consent['source_message_id'],
                'coordinator_id':consent['agent_id'], 'request_id':row['launch_request_id']},
                'confirm_execution':True,'confirm_handoff':consent['confirm_handoff']}

    def _launch(self, db, row, planning):
        found = db.execute('SELECT snapshot FROM project_launches WHERE source_conversation_id=? AND request_id=?',
                           (row['source_conversation_id'], row['launch_request_id'])).fetchone()
        if not found:
            return None, False
        launch = json.loads(found[0])
        try:
            if _json(launch['request_payload']) != _json(self._expected_launch(row, planning)):
                return None, True
        except (ValueError, KeyError, TypeError):
            return None, True
        return launch, False

    def _get(self, db, identity):
        row = self._row(db, identity)
        planning = self.planning._get(db, row['planning_run_id'])
        # Recover visibility even if the launch committed just before its association write failed.
        launch, mismatch = self._launch(db, row, planning)
        state = 'launched' if launch else row['failure_state'] or ('ready_to_launch' if planning['state']=='completed' else
                    planning['state'] if planning['state'] in ('failed','unknown','cancelled') else 'planning')
        batch = self.cli.project_executions._get(db, launch['batch']['id']) if launch else None
        if row['stop_requested']:
            active = planning['state'] in ('queued','running','unknown') or batch and any(i['execution']['state'] in ('queued','running','stopping','unknown') for i in batch['items'])
            state = 'stop_requested' if active or not row['stop_applied'] else 'stopped'
        if mismatch:
            state = 'unknown'
        return {key: row[key] for key in ('id','source_conversation_id','request_id','planning_run_id','launch_request_id','created_at')} | {
            'request_payload':json.loads(row['payload']), 'launch_id':launch['id'] if launch else row['launch_id'],
            'stop_requested':bool(row['stop_requested']), 'state':state,
            'error':'固定启动请求对应另一计划，禁止关联或停止其批次' if mismatch else row['error'],
            'planning':planning, 'launch':launch, 'batch':batch}

    def get(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._get(db, _text(identity,'目标执行 ID'))

    def list(self, source):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self.store._conversation(db, source)
            ids = db.execute("""SELECT g.id FROM goal_executions g JOIN runs r ON r.id=g.planning_run_id
                WHERE g.source_conversation_id=? AND (g.id IN (
                    SELECT id FROM goal_executions WHERE source_conversation_id=? ORDER BY created_at DESC,id DESC LIMIT 100)
                    OR r.state IN ('queued','running') OR (g.stop_requested=1 AND g.stop_applied=0)
                    OR (g.launch_id IS NULL AND g.failure_state IS NULL AND r.state='completed' AND g.stop_requested=0)
                    OR EXISTS(SELECT 1 FROM project_launches l,json_each(l.snapshot,'$.batch.tasks') b
                        JOIN task_executions e ON e.id=json_extract(b.value,'$.execution_id')
                        WHERE l.source_conversation_id=g.source_conversation_id AND l.request_id=g.launch_request_id
                            AND e.state IN ('queued','running','stopping','unknown')))
                ORDER BY g.created_at DESC,g.id DESC""", (source,source)).fetchall()
            return [self._get(db, row[0]) for row in ids]

    def create(self, source, payload):
        encoded = self._validate(payload)
        source = _text(source, '源会话 ID')
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id,payload FROM goal_executions WHERE source_conversation_id=? AND request_id=?', (source,payload['request_id'])).fetchone()
            if old:
                if old['payload'] != encoded:
                    raise ValueError('request_id 已用于不同目标授权')
                return self._get(db, old['id'])
            request = {key:payload[key] for key in ('source_message_id','agent_id','candidate_ids')}
            request['request_id'] = str(uuid.uuid4())
            planned = self.planning._create(db, source, request)
            identity = str(uuid.uuid4())
            db.execute('INSERT INTO goal_executions(id,source_conversation_id,request_id,payload,planning_run_id,launch_request_id) VALUES(?,?,?,?,?,?)',
                       (identity,source,payload['request_id'],encoded,planned['id'],str(uuid.uuid4())))
            return self._get(db, identity)

    def _advance(self, identity):
        with self.store.connect() as db:
            row = self._row(db, identity)
            planning = self.planning._get(db, row['planning_run_id'])
            launch, mismatch = self._launch(db, row, planning)
            if mismatch:
                if row['error'] != '固定启动请求不匹配；未关联或停止其他批次':
                    db.execute("UPDATE goal_executions SET failure_state=COALESCE(failure_state,'unknown'),error='固定启动请求不匹配；未关联或停止其他批次' WHERE id=?", (identity,))
                if row['stop_requested'] and not row['stop_applied']:
                    db.execute("UPDATE runs SET state='cancelled' WHERE id=? AND state='queued'", (row['planning_run_id'],))
                    db.execute('UPDATE goal_executions SET stop_applied=1 WHERE id=?', (identity,))
                return
            if launch and row['launch_id'] is None:
                db.execute('UPDATE goal_executions SET launch_id=? WHERE id=?', (launch['id'],identity))
        if row['stop_requested']:
            if row['stop_applied']:
                return
            # Only cancel a still queued Run; a running provider call is not claimed stopped.
            with self.store.connect() as db:
                db.execute("UPDATE runs SET state='cancelled' WHERE id=? AND state='queued'", (row['planning_run_id'],))
            if launch:
                self.cli.stop_project(launch['batch']['id'])
            with self.store.connect() as db:
                db.execute('UPDATE goal_executions SET stop_applied=1 WHERE id=?', (identity,))
            return
        if launch or row['failure_state'] or planning['state'] != 'completed':
            return
        consent = json.loads(row['payload'])
        try:
            with self.store.connect() as db:
                self.planning.runs._authorize(db, planning)
                for candidate in consent['candidate_ids']:
                    self.store._enabled_member(db, candidate)
            launch = self.cli.launch_project(row['source_conversation_id'], self._expected_launch(row, planning))
        except (ValueError, PermissionError, KeyError):
            with self.store.connect() as db:
                db.execute("UPDATE goal_executions SET failure_state='failed',error='计划或执行条件不符合原授权；未自动重新规划或启动' WHERE id=?", (identity,))
            return
        except Exception:
            # Read the stable launch key on later ticks, but never repeat an uncertain new launch.
            with self.store.connect() as db:
                db.execute("UPDATE goal_executions SET failure_state='unknown',error='启动结果待核查；仅按固定请求读取，不自动重试' WHERE id=?", (identity,))
            return
        with self.store.connect() as db:
            db.execute('UPDATE goal_executions SET launch_id=? WHERE id=?', (launch['id'],identity))

    def tick(self):
        with self.lock:
            with self.store.connect() as db:
                ids = [r[0] for r in db.execute("""SELECT g.id FROM goal_executions g JOIN runs r ON r.id=g.planning_run_id
                    WHERE (g.launch_id IS NULL AND ((g.failure_state IS NULL AND r.state='completed' AND g.stop_requested=0)
                        OR EXISTS(SELECT 1 FROM project_launches l WHERE l.source_conversation_id=g.source_conversation_id AND l.request_id=g.launch_request_id)))
                        OR (g.stop_requested=1 AND g.stop_applied=0) ORDER BY g.created_at,g.id""")]
            for identity in ids:
                self._advance(identity)

    def stop_goal(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {'confirm'} or payload['confirm'] is not True:
            raise ValueError('停止目标执行须明确 confirm=true')
        with self.lock:
            with self.store.connect() as db:
                self._row(db, identity)
                db.execute('UPDATE goal_executions SET stop_requested=1 WHERE id=?', (identity,))
            self._advance(identity)
            return self.get(identity)
