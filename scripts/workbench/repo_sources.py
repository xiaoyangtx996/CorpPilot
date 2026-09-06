"""Owner-pinned project sources; execution bindings never follow a moving branch."""
import json
import re

from .store import _text
from .git_checkout import inspect_source


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS project_repositories (
        conversation_id TEXT NOT NULL REFERENCES conversations(id), revision INTEGER NOT NULL,
        request_id TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(conversation_id,revision), UNIQUE(conversation_id,request_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS execution_repositories (
        execution_id TEXT PRIMARY KEY REFERENCES task_executions(id),
        conversation_id TEXT NOT NULL, revision INTEGER NOT NULL,
        FOREIGN KEY(conversation_id,revision) REFERENCES project_repositories(conversation_id,revision))''')
    for table in ('project_repositories','execution_repositories'):
        for action in ('UPDATE','DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Repository binding is immutable'); END''')
        condition = 'conversation_id=NEW.conversation_id AND revision=NEW.revision' if table=='project_repositories' else 'execution_id=NEW.execution_id'
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition})
            BEGIN SELECT RAISE(ABORT,'Repository binding is immutable'); END''')


def _row(row):
    return {key:row[key] for key in ('conversation_id','revision','request_id','created_at')} | {'snapshot':json.loads(row['snapshot'])} if row else None


def bound(db, execution_id):
    if not execution_id:return None
    row=db.execute('''SELECT r.* FROM execution_repositories e JOIN project_repositories r
        ON r.conversation_id=e.conversation_id AND r.revision=e.revision WHERE e.execution_id=?''',(execution_id,)).fetchone()
    return _row(row)


def _authorize(store, db, conversation_id, agent_id=None):
    conversation=store._conversation(db,conversation_id,agent_id)
    if conversation['type']!='project' or conversation['archived']:
        raise ValueError('代码仓库只能绑定未归档的项目群')
    if agent_id:store._enabled_member(db,agent_id)


def authorize(store, db, execution_id):
    record=bound(db,execution_id)
    if record:_authorize(store,db,record['conversation_id'],record['snapshot']['integration_agent_id'])
    return record


def freeze(store, db, execution_id, conversation_id):
    row=db.execute('SELECT * FROM project_repositories WHERE conversation_id=? ORDER BY revision DESC LIMIT 1',(conversation_id,)).fetchone()
    record=_row(row)
    if record and record['snapshot'] is not None:
        _authorize(store,db,conversation_id,record['snapshot']['integration_agent_id'])
        db.execute('INSERT INTO execution_repositories VALUES(?,?,?)',(execution_id,conversation_id,record['revision']))


class RepositorySources:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); initialize(db)

    def get(self, conversation_id):
        with self.store.connect() as db:
            db.execute('BEGIN'); self.store._conversation(db,conversation_id)
            return _row(db.execute('SELECT * FROM project_repositories WHERE conversation_id=? ORDER BY revision DESC LIMIT 1',(conversation_id,)).fetchone())

    def request(self, conversation_id, request_id):
        with self.store.connect() as db:
            db.execute('BEGIN'); self.store._conversation(db,conversation_id)
            return _row(db.execute('SELECT * FROM project_repositories WHERE conversation_id=? AND request_id=?',(conversation_id,request_id)).fetchone())

    def execution(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            run=db.execute('SELECT * FROM task_executions WHERE id=?',(identity,)).fetchone()
            if not run:raise KeyError('执行不存在')
            return dict(execution_id=identity,agent_id=run['agent_id'],attempt=run['attempt'],requirement_version=run['requirement_version'],repository=bound(db,identity))

    def save(self, conversation_id, payload):
        from .tasks import TaskVersionConflict
        fields={'request_id','expected_revision','source_path','commit','integration_agent_id','confirm'}
        if (not isinstance(payload,dict) or set(payload)!=fields or payload['confirm'] is not True
                or type(payload['expected_revision']) is not int or not 0<=payload['expected_revision']<9007199254740991):
            raise ValueError('仓库绑定必须包含请求ID、预期版本、源路径、完整提交、集成人和明确确认')
        payload=dict(payload)
        payload['request_id']=_text(payload['request_id'],'请求 ID',120)
        if not re.fullmatch(r'[A-Za-z0-9_-]+',payload['request_id']):raise ValueError('请求 ID 只允许字母、数字、下划线和连字符')
        disabled=all(payload[key] is None for key in ('source_path','commit','integration_agent_id'))
        if not disabled:
            for key in ('source_path','commit','integration_agent_id'):
                payload[key]=_text(payload[key],key,4096 if key=='source_path' else 200)
        original=json.dumps(payload,sort_keys=True,ensure_ascii=False)
        def check(db):
            prior=db.execute('SELECT * FROM project_repositories WHERE conversation_id=? AND request_id=?',(conversation_id,payload['request_id'])).fetchone()
            if prior:
                if prior['payload']!=original:raise ValueError('请求 ID 已用于不同仓库绑定')
                return _row(prior)
            _authorize(self.store,db,conversation_id,None if disabled else payload['integration_agent_id'])
            revision=db.execute('SELECT COALESCE(MAX(revision),0) FROM project_repositories WHERE conversation_id=?',(conversation_id,)).fetchone()[0]
            if revision!=payload['expected_revision']:raise TaskVersionConflict('项目仓库版本已变化，请重新核查')
        with self.store.connect() as db:
            db.execute('BEGIN'); previous=check(db)
            if previous:return previous
        # Inspection is read-only and outside the write transaction; recheck authorization and CAS afterwards.
        snapshot=None if disabled else inspect_source(payload['source_path'],payload['commit']) | {'integration_agent_id':payload['integration_agent_id']}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); previous=check(db)
            if previous:return previous
            db.execute('INSERT INTO project_repositories(conversation_id,revision,request_id,payload,snapshot) VALUES(?,?,?,?,?)',
                (conversation_id,payload['expected_revision']+1,payload['request_id'],original,json.dumps(snapshot,sort_keys=True,ensure_ascii=False)))
            return _row(db.execute('SELECT * FROM project_repositories WHERE conversation_id=? AND request_id=?',(conversation_id,payload['request_id'])).fetchone())
