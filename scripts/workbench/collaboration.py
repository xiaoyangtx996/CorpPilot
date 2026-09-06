"""Atomic Owner-approved project formation; no implicit model or worker invocation."""
import json
import re
import uuid

from .store import _text
from .tasks import Tasks
from . import dependencies


def require_delegate(db, agent_id):
    row = db.execute('SELECT tools FROM agents WHERE id=?', (agent_id,)).fetchone()
    if row is None:
        raise KeyError('协调人不存在')
    if 'delegate' not in json.loads(row['tools']):
        raise PermissionError('协调人尚未获得 delegate 工具权限')


class Collaboration:
    @staticmethod
    def _receipt(row):
        return {**json.loads(row['snapshot']), 'approved_plan': json.loads(row['payload'])}

    def __init__(self, store):
        self.store = store
        self.tasks = Tasks(store)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS collaboration_receipts (
                id TEXT PRIMARY KEY, source_conversation_id TEXT NOT NULL REFERENCES conversations(id),
                request_id TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(source_conversation_id,request_id))""")
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS collaboration_no_{operation.lower()}
                    BEFORE {operation} ON collaboration_receipts BEGIN
                    SELECT RAISE(ABORT, 'Collaboration receipts are immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS collaboration_no_replace BEFORE INSERT ON collaboration_receipts
                WHEN EXISTS(SELECT 1 FROM collaboration_receipts WHERE id=NEW.id OR
                    (source_conversation_id=NEW.source_conversation_id AND request_id=NEW.request_id))
                BEGIN SELECT RAISE(ABORT, 'Collaboration receipts are immutable'); END""")

    @staticmethod
    def _validate(payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'source_message_id', 'title', 'shared_brief', 'coordinator_id', 'tasks'}:
            raise ValueError('协作计划字段不完整或含未知字段')
        try:
            original = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if len(original.encode('utf-8')) > 65536:
                raise ValueError('协作计划不能超过64KiB')
        except (TypeError, UnicodeError, RecursionError) as exc:
            raise ValueError('协作计划必须是有效 UTF-8 JSON') from exc
        _text(payload['request_id'], '请求 ID', 120)
        _text(payload['source_message_id'], '源消息 ID')
        _text(payload['title'], '项目标题', 120)
        _text(payload['shared_brief'], '共享摘要', 16000)
        _text(payload['coordinator_id'], '协调人 ID')
        rows = payload['tasks']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ValueError('协作任务必须为1–16项')
        keys = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'key', 'title', 'scope', 'acceptance', 'agent_id', 'depends_on'}:
                raise ValueError('协作任务字段不完整或含未知字段')
            key = row['key']
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', key) or key in keys:
                raise ValueError('任务 key 必须为不重复的1–32位字母开头短标识')
            keys.add(key)
            Tasks._requirements(row)
        for row in rows:
            targets = row['depends_on']
            if (not isinstance(targets, list) or len(targets) > 16
                    or any(not isinstance(target, str) or target not in keys for target in targets)
                    or len(set(targets)) != len(targets)):
                raise ValueError('依赖必须是不重复的本计划任务 key')
        return original

    def create(self, source_conversation_id, payload):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._create(db, source_conversation_id, payload)

    def _create(self, db, source_conversation_id, payload):
        source_conversation_id = _text(source_conversation_id, '源会话 ID')
        original = self._validate(payload)
        request = _text(payload['request_id'], '请求 ID', 120)
        prior = db.execute('SELECT payload,snapshot FROM collaboration_receipts WHERE source_conversation_id=? AND request_id=?', (source_conversation_id, request)).fetchone()
        if prior:
            if prior['payload'] != original:
                raise ValueError('request_id 已用于不同协作计划')
            return self._receipt(prior)
        coordinator = _text(payload['coordinator_id'], '协调人 ID')
        require_delegate(db, coordinator)
        source = self.store._conversation(db, source_conversation_id, coordinator)
        if source['archived']:
            raise ValueError('源会话已归档')
        source_message = _text(payload['source_message_id'], '源消息 ID')
        if not db.execute("SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND sender_kind='owner'", (source_message, source_conversation_id)).fetchone():
            raise ValueError('源消息必须是本会话的 Owner 消息')
        members = sorted({coordinator, *(_text(row['agent_id'], 'Agent ID') for row in payload['tasks'])})
        for member in members:
            self.store._enabled_member(db, member)
        identity, project_id, message_id = (str(uuid.uuid4()) for _ in range(3))
        db.execute("INSERT INTO conversations(id,type,title) VALUES(?,'project',?)", (project_id, payload['title'].strip()))
        db.executemany('INSERT INTO members VALUES(?,?)', [(project_id, member) for member in members])
        db.execute("INSERT INTO messages(id,conversation_id,sender_kind,content,request_id) VALUES(?,?,'owner',?,?)", (message_id, project_id, payload['shared_brief'].strip(), identity))
        task_ids = {}
        for row in payload['tasks']:
            task = self.tasks._create(db, project_id, {**{key: row[key] for key in ('title', 'scope', 'acceptance', 'agent_id')}, 'source_message_id': message_id, 'request_id': row['key']})
            task_ids[row['key']] = task['id']
        for row in payload['tasks']:
            db.executemany('INSERT INTO task_dependencies VALUES(?,1,?)', [(task_ids[row['key']], task_ids[target]) for target in row['depends_on']])
        for task_id in task_ids.values():
            dependencies.validate_graph(db, task_id, project_id, dependencies.ids(db, task_id, 1))
        created = db.execute('SELECT created_at FROM conversations WHERE id=?', (project_id,)).fetchone()[0]
        snapshot = dict(id=identity, source_conversation_id=source_conversation_id, source_message_id=source_message,
                        request_id=request, project_conversation_id=project_id, shared_message_id=message_id,
                        coordinator_id=coordinator, member_ids=members, task_ids=task_ids, created_at=created)
        db.execute('INSERT INTO collaboration_receipts(id,source_conversation_id,request_id,payload,snapshot) VALUES(?,?,?,?,?)', (identity, source_conversation_id, request, original, json.dumps(snapshot, ensure_ascii=False)))
        return {**snapshot, 'approved_plan': json.loads(original)}

    def get(self, identity):
        with self.store.connect() as db:
            row = db.execute('SELECT snapshot,payload FROM collaboration_receipts WHERE id=?', (_text(identity, '协作 ID'),)).fetchone()
            if row is None:
                raise KeyError('协作计划不存在')
            return self._receipt(row)

    def for_project(self, project_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            conversation = self.store._conversation(db, project_id)
            if conversation["type"] != "project":
                raise ValueError("来源计划查询只适用于项目会话")
            row = db.execute("SELECT snapshot,payload FROM collaboration_receipts WHERE json_extract(snapshot,'$.project_conversation_id')=?",
                             (project_id,)).fetchone()
            return self._receipt(row) if row else None

    def list(self, source_conversation_id):
        source_conversation_id = _text(source_conversation_id, '源会话 ID')
        with self.store.connect() as db:
            db.execute('BEGIN')
            self.store._conversation(db, source_conversation_id)
            return [self._receipt(row) for row in db.execute('SELECT snapshot,payload FROM collaboration_receipts WHERE source_conversation_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (source_conversation_id,))]
