"""Atomic admission and fixed-instance cancellation for Owner-approved project plans."""
import json
import uuid

from .collaboration import Collaboration
from .executions import Executions
from .reviews import Reviews
from .tasks import Tasks
from .store import _text


def _payload(value):
    if not isinstance(value, dict) or set(value) != {'request_id', 'tasks'}:
        raise ValueError('批次请求必须只含 request_id 和 tasks')
    if _text(value['request_id'], '请求 ID', 120) != value['request_id']:
        raise ValueError('请求 ID 不得含首尾空格')
    items = value['tasks']
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise ValueError('批次须选择1–16项任务')
    identities = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'task_id', 'expected_version', 'previous_execution_id', 'reconciliation_note'}:
            raise ValueError('批次任务字段不完整或含未知字段')
        identity = _text(item['task_id'], '任务 ID', 120)
        if identity != item['task_id']:
            raise ValueError('任务 ID 不得含首尾空格')
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise ValueError('批次任务不得重复')
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 65536:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('批次请求必须为最多64KiB的有效 JSON') from None
    return encoded


class ProjectExecutions:
    def __init__(self, store):
        self.store = store
        self.collaboration = Collaboration(store)
        self.executions = Executions(store)
        Reviews(store)
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS project_execution_batches (
                id TEXT PRIMARY KEY, collaboration_id TEXT NOT NULL REFERENCES collaboration_receipts(id),
                request_id TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(collaboration_id,request_id))""")
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS project_batches_no_{operation.lower()}
                    BEFORE {operation} ON project_execution_batches BEGIN
                    SELECT RAISE(ABORT,'Project execution batches are immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS project_batches_no_replace BEFORE INSERT ON project_execution_batches
                WHEN EXISTS(SELECT 1 FROM project_execution_batches WHERE id=NEW.id OR
                    (collaboration_id=NEW.collaboration_id AND request_id=NEW.request_id))
                BEGIN SELECT RAISE(ABORT,'Project execution batches are immutable'); END""")

    @staticmethod
    def _receipt(db, identity):
        row = db.execute('SELECT snapshot FROM project_execution_batches WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError('项目执行批次不存在')
        return json.loads(row[0])

    def create(self, collaboration_id, payload):
        encoded = _payload(payload)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload,snapshot FROM project_execution_batches WHERE collaboration_id=? AND request_id=?',
                             (collaboration_id, payload['request_id'])).fetchone()
            if old:
                if old['payload'] != encoded:
                    raise ValueError('request_id 已用于不同项目执行批次')
                return json.loads(old['snapshot'])
            row = db.execute('SELECT snapshot FROM collaboration_receipts WHERE id=?', (collaboration_id,)).fetchone()
            if row is None:
                raise KeyError('协作计划不存在')
            plan = json.loads(row[0])
            allowed = set(plan['task_ids'].values())
            if any(item['task_id'] not in allowed for item in payload['tasks']):
                raise ValueError('只能执行此批准计划中明确选择的任务')
            bindings = []
            for item in payload['tasks']:
                request = str(uuid.uuid4())
                run = self.executions._create(db, item['task_id'], {
                    'request_id': request, **{k: item[k] for k in ('expected_version', 'previous_execution_id', 'reconciliation_note')}})
                bindings.append({'task_id': item['task_id'], 'execution_id': run['id'], 'request_id': request})
            identity = str(uuid.uuid4())
            created = db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            receipt = {'id': identity, 'collaboration_id': collaboration_id, 'project_conversation_id': plan['project_conversation_id'],
                       'request_id': payload['request_id'], 'request_payload': payload, 'tasks': bindings, 'created_at': created}
            db.execute('INSERT INTO project_execution_batches VALUES(?,?,?,?,?,?)',
                       (identity, collaboration_id, payload['request_id'], encoded, json.dumps(receipt, ensure_ascii=False), created))
            return receipt

    def _get(self, db, identity):
        receipt = self._receipt(db, identity)
        items = []
        for binding in receipt['tasks']:
            task = Tasks._task(db, binding['task_id'])
            execution = self.executions._run(db, binding['execution_id'])
            latest = db.execute('SELECT id FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1', (task['id'],)).fetchone()[0]
            items.append({'task': task, 'execution': execution, 'review': Reviews._review(db, execution['id']),
                          'dependencies': Tasks._dependencies(db, task), 'latest_execution_id': latest})
        return {**receipt, 'items': items}

    def get(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._get(db, identity)

    def list(self, collaboration_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            if not db.execute('SELECT 1 FROM collaboration_receipts WHERE id=?', (collaboration_id,)).fetchone():
                raise KeyError('协作计划不存在')
            return [json.loads(row[0]) for row in db.execute('SELECT snapshot FROM project_execution_batches WHERE collaboration_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (collaboration_id,))]

    def stop(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = self._receipt(db, identity)
            for binding in receipt['tasks']:
                self.executions._cancel(db, binding['execution_id'])
            return self._get(db, identity)
