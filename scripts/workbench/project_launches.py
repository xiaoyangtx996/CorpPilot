"""One Owner confirmation atomically creates a project and admits its first batch."""
import json
import uuid

from .collaboration import Collaboration
from .project_executions import ProjectExecutions
from .store import _text


def validate(payload):
    if (not isinstance(payload,dict) or set(payload) not in ({'plan','confirm_execution'}, {'plan','confirm_execution','confirm_handoff'})
            or payload['confirm_execution'] is not True or 'confirm_handoff' in payload and type(payload['confirm_handoff']) is not bool):
        raise ValueError('启动请求须含完整 plan、confirm_execution=true，可选 confirm_handoff 必须为布尔值')
    Collaboration._validate(payload['plan'])
    encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if len(encoded.encode('utf-8'))>65536:
        raise ValueError('创建并启动请求超过64KiB')
    return encoded


class ProjectLaunches:
    def __init__(self,store):
        self.store=store
        self.batches=ProjectExecutions(store)
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS project_launches (
                id TEXT PRIMARY KEY,source_conversation_id TEXT NOT NULL REFERENCES conversations(id),
                request_id TEXT NOT NULL,payload TEXT NOT NULL,snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(source_conversation_id,request_id))""")
            for action in ('UPDATE','DELETE'):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS project_launch_no_{action.lower()} BEFORE {action}
                    ON project_launches BEGIN SELECT RAISE(ABORT,'Immutable project launch'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS project_launch_no_replace BEFORE INSERT ON project_launches
                WHEN EXISTS(SELECT 1 FROM project_launches WHERE id=NEW.id OR
                    (source_conversation_id=NEW.source_conversation_id AND request_id=NEW.request_id))
                BEGIN SELECT RAISE(ABORT,'Immutable project launch'); END""")

    def create(self,source_conversation_id,payload):
        source_conversation_id=_text(source_conversation_id,'源会话 ID')
        encoded=validate(payload)
        request=_text(payload['plan']['request_id'],'请求 ID',120)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT payload,snapshot FROM project_launches WHERE source_conversation_id=? AND request_id=?',(source_conversation_id,request)).fetchone()
            if previous:
                if previous['payload']!=encoded:raise ValueError('request_id 已用于不同创建并启动请求')
                return json.loads(previous['snapshot'])
            if db.execute('SELECT 1 FROM collaboration_receipts WHERE source_conversation_id=? AND request_id=?',(source_conversation_id,request)).fetchone():
                raise ValueError('此请求已仅创建协作项目，不能扩大原授权；请使用该项目的批量执行入口')
            collaboration=self.batches.collaboration._create(db,source_conversation_id,payload['plan'])
            batch=self.batches._create(db,collaboration['id'],{'request_id':str(uuid.uuid4()),'tasks':[
                {'task_id':collaboration['task_ids'][item['key']],'expected_version':1,'previous_execution_id':None,'reconciliation_note':''}
                for item in payload['plan']['tasks']]})
            identity=str(uuid.uuid4())
            created=db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            receipt={'id':identity,'source_conversation_id':source_conversation_id,'request_id':request,'request_payload':payload,
                     'collaboration':collaboration,'batch':batch,'created_at':created}
            db.execute('INSERT INTO project_launches VALUES(?,?,?,?,?,?)',(identity,source_conversation_id,request,encoded,json.dumps(receipt,ensure_ascii=False),created))
            if payload.get('confirm_handoff') is True:
                bindings = {item['task_id']: item['execution_id'] for item in batch['tasks']}
                task_ids = collaboration['task_ids']
                db.executemany('INSERT INTO execution_handoff_permissions VALUES(?,?,?,?)', [
                    (bindings[task_ids[item['key']]], task_ids[parent], bindings[task_ids[parent]], identity)
                    for item in payload['plan']['tasks'] for parent in item['depends_on']])
            return receipt

    def get(self,identity):
        with self.store.connect() as db:
            row=db.execute('SELECT snapshot FROM project_launches WHERE id=?',(_text(identity,'启动 ID'),)).fetchone()
            if row is None:raise KeyError('创建并启动回执不存在')
            return json.loads(row[0])

    def list(self,source_conversation_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self.store._conversation(db,source_conversation_id)
            return [json.loads(row[0]) for row in db.execute('SELECT snapshot FROM project_launches WHERE source_conversation_id=? ORDER BY created_at DESC,id DESC LIMIT 100',(source_conversation_id,))]
