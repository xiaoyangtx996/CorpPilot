"""Immutable metadata for prepared inputs; never proof that a provider received them."""
import hashlib
import json
import re

from .store import _text
from . import artifacts


def _digest(value):
    if not isinstance(value, str):
        raise ValueError('上下文文本格式无效')
    try:
        return {'chars': len(value), 'sha256': hashlib.sha256(value.encode('utf-8')).hexdigest()}
    except UnicodeError:
        raise ValueError('上下文文本不是有效 UTF-8') from None


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 9007199254740991:
        raise ValueError('上下文版本或数量无效')
    return value


def _message(row, content_source):
    if not isinstance(row, dict) or row.get('sender_kind') not in ('owner', 'agent'):
        raise ValueError('上下文消息格式无效')
    return {'id': _text(row['id'], '消息 ID') if row.get('id') is not None else None,
            'sequence': _integer(row['sequence'], 1) if row.get('sequence') is not None else None,
            'sender_kind': row['sender_kind'],
            'sender_id': _text(row['sender_id'], '发送者 ID') if row.get('sender_id') is not None else None,
            'content_source': content_source, **_digest(row.get('content'))}


class ContextReceipts:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS context_receipts (
                kind TEXT NOT NULL CHECK(kind IN ('model','cli')), run_id TEXT NOT NULL,
                payload TEXT NOT NULL, prepared_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY(kind,run_id))''')
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS context_receipts_no_{operation.lower()}
                    BEFORE {operation} ON context_receipts
                    BEGIN SELECT RAISE(ABORT,'Context receipts are immutable'); END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS context_receipts_no_replace
                BEFORE INSERT ON context_receipts WHEN EXISTS
                (SELECT 1 FROM context_receipts WHERE kind=NEW.kind AND run_id=NEW.run_id)
                BEGIN SELECT RAISE(ABORT,'Context receipts are immutable'); END''')

    @staticmethod
    def _run(db, kind, identity):
        if kind not in ('model', 'cli'):
            raise ValueError('上下文类别必须为 model 或 cli')
        identity = _text(identity, '实例 ID')
        table = 'runs' if kind == 'model' else 'task_executions'
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError('实例不存在')
        return dict(row)

    @staticmethod
    def _get(db, kind, identity):
        row = db.execute('SELECT payload,prepared_at FROM context_receipts WHERE kind=? AND run_id=?',
                         (kind, identity)).fetchone()
        return {**json.loads(row['payload']), 'prepared_at': row['prepared_at']} if row else None

    def get(self, kind, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            run = self._run(db, kind, identity)
            return self._get(db, kind, run['id'])

    @staticmethod
    def _base(kind, run, snapshot, model):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('agent'), dict) or snapshot['agent'].get('id') != run['agent_id']:
            raise ValueError('上下文身份与实际实例不一致')
        return {'version': 1, 'kind': kind, 'run_id': run['id'], 'agent_id': run['agent_id'],
                'attempt': run['attempt'], 'requirement_version': run['requirement_version'], 'phase': 'prepared',
                'model': _text(model, '模型标识', 200),
                'template_id': _text(snapshot['agent'].get('template_id'), '模板 ID'),
                'instructions': _digest(snapshot.get('instructions'))}

    def _save(self, db, run, receipt):
        encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        previous = self._get(db, receipt['kind'], run['id'])
        if previous:
            if {k: v for k, v in previous.items() if k != 'prepared_at'} != receipt:
                raise ValueError('已准备上下文回执与原记录冲突')
            return previous
        if run['state'] != 'running':
            raise ValueError('只有运行中的实例可以首次记录已准备上下文')
        db.execute('INSERT INTO context_receipts(kind,run_id,payload) VALUES(?,?,?)',
                   (receipt['kind'], run['id'], encoded))
        return self._get(db, receipt['kind'], run['id'])

    def record_model(self, identity, snapshot, model):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, 'model', identity)
            receipt = self._base('model', run, snapshot, model)
            run_kind = 'reply'
            for kind, table in (('planning', 'planning_requests'), ('retrospective', 'retrospective_requests'), ('peer_review', 'peer_review_requests')):
                if db.execute(f'SELECT 1 FROM {table} WHERE run_id=?', (run['id'],)).fetchone():
                    run_kind = kind
                    break
            authority = None
            if run_kind == 'planning' and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_executions'").fetchone():
                authority = db.execute('SELECT 1 FROM goal_executions WHERE planning_run_id=?', (run['id'],)).fetchone()
            source = 'authorized_shared_brief' if authority else 'retrospective_snapshot' if run_kind == 'retrospective' else 'conversation_message'
            messages = snapshot.get('messages')
            if not isinstance(messages, list) or not 1 <= len(messages) <= 100 or type(snapshot.get('context_truncated')) is not bool:
                raise ValueError('模型上下文必须包含1–100条消息与截断标记')
            receipt.update(run_kind=run_kind, messages=[_message(row, source) for row in messages],
                           context_truncated=snapshot['context_truncated'], source_sequence=_integer(snapshot.get('source_sequence')))
            return self._save(db, run, receipt)

    def record_cli(self, identity, snapshot, model, backend):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, 'cli', identity)
            receipt = self._base('cli', run, snapshot, model)
            task = snapshot.get('task')
            if backend not in ('local', 'docker') or not isinstance(task, dict) or task.get('id') != run['task_id'] or task.get('requirement_version') != run['requirement_version'] or task.get('agent_id') != run['agent_id']:
                raise ValueError('CLI 上下文任务、需求版本或后端不一致')
            message = _message(snapshot.get('source_message'), 'conversation_message')
            if message['id'] != task.get('source_message_id'):
                raise ValueError('CLI 上下文源消息不一致')
            documents, inputs = snapshot.get('memories'), snapshot.get('input_artifacts')
            if not isinstance(documents, list) or len(documents) > 2 or not isinstance(inputs, list) or len(inputs) > artifacts.MAX_FILES:
                raise ValueError('CLI 上下文记忆或成果超出限额')
            memory_refs, seen = [], set()
            for row in documents:
                if not isinstance(row, dict) or row.get('scope') not in ('agent', 'project') or row['scope'] in seen:
                    raise ValueError('CLI 记忆范围无效')
                seen.add(row['scope'])
                expected = run['agent_id'] if row['scope'] == 'agent' else task.get('conversation_id')
                if row.get('scope_id') != expected:
                    raise ValueError('CLI 记忆身份或项目不一致')
                version = _integer(row.get('version'), 1)
                memory_refs.append({'scope': row['scope'], 'scope_id': expected, 'version': version, **_digest(row.get('content'))})
            refs, seen, total = [], set(), 0
            for row in inputs:
                if not isinstance(row, dict):
                    raise ValueError('CLI 输入成果无效')
                identity = artifacts._identity(row.get('id'))
                upstream = artifacts._identity(row.get('execution_id'))
                size, digest = _integer(row.get('size')), row.get('sha256')
                total += size
                if identity in seen or size > artifacts.MAX_FILE_BYTES or total > artifacts.MAX_TOTAL_BYTES or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                    raise ValueError('CLI 输入成果元数据无效或超出限额')
                seen.add(identity)
                refs.append({'id': identity, 'execution_id': upstream, 'path': artifacts._path(row.get('path')), 'size': size, 'sha256': digest})
            receipt.update(backend=backend, task={'id': run['task_id'], 'requirement_version': run['requirement_version'],
                **{key: _digest(task.get(key)) for key in ('title', 'scope', 'acceptance')}},
                source_message=message, memories=memory_refs, input_artifacts=refs)
            return self._save(db, run, receipt)
