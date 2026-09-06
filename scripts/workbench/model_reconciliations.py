"""Owner declarations unblock explicit future model requests, never rewrite unknown runs."""
import json

from .store import _text
from .tasks import TaskVersionConflict


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS model_run_reconciliations (
        run_id TEXT PRIMARY KEY REFERENCES runs(id), request_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK(attempt>0), requirement_version INTEGER NOT NULL CHECK(requirement_version>0),
        local_request_stopped INTEGER NOT NULL CHECK(local_request_stopped=1),
        provider_effects_checked INTEGER NOT NULL CHECK(provider_effects_checked=1), note TEXT NOT NULL,
        reconciled_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    for action in ('UPDATE','DELETE'):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS model_reconciliation_no_{action.lower()}
            BEFORE {action} ON model_run_reconciliations BEGIN SELECT RAISE(ABORT,'Immutable model reconciliation'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS model_reconciliation_no_replace BEFORE INSERT ON model_run_reconciliations
        WHEN EXISTS(SELECT 1 FROM model_run_reconciliations WHERE run_id=NEW.run_id)
        BEGIN SELECT RAISE(ABORT,'Immutable model reconciliation'); END""")


def unresolved(db, run, *, kind=None, retrospective=None):
    """Exact operation scope only; unrelated messages, actors and retrospectives remain usable."""
    if kind is None:
        row = db.execute('SELECT scope,scope_id,payload FROM retrospective_requests WHERE run_id=?',(run['id'],)).fetchone()
        if row:
            kind='retrospective'
            retrospective=(row['scope'],row['scope_id'],json.loads(row['payload'])['source_execution_id'])
        else:
            kind='planning' if db.execute('SELECT 1 FROM planning_requests WHERE run_id=?',(run['id'],)).fetchone() else 'reply'
    query="""SELECT 1 FROM runs r LEFT JOIN retrospective_requests t ON t.run_id=r.id
        LEFT JOIN planning_requests p ON p.run_id=r.id WHERE r.state='unknown'
        AND NOT EXISTS(SELECT 1 FROM model_run_reconciliations c WHERE c.run_id=r.id) """
    if kind=='retrospective':
        if retrospective is None:
            raise ValueError('复盘核查范围缺失')
        query+="AND t.scope=? AND t.scope_id=? AND json_extract(t.payload,'$.source_execution_id')=? LIMIT 1"
        args=retrospective
    else:
        query+='AND t.run_id IS NULL AND '+('p.run_id IS NOT NULL' if kind=='planning' else 'p.run_id IS NULL')
        query+=' AND r.conversation_id=? AND r.source_message_id=? AND r.agent_id=? LIMIT 1'
        args=(run['conversation_id'],run['source_message_id'],run['agent_id'])
    return db.execute(query,args).fetchone() is not None


class ModelReconciliations:
    def __init__(self,store):
        from .runs import Runs
        self.store=store
        Runs(store)

    @staticmethod
    def _run(db,identity):
        row=db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone()
        if row is None:raise KeyError('模型 Run 不存在')
        return row

    @staticmethod
    def _get(db,identity):
        row=db.execute('SELECT * FROM model_run_reconciliations WHERE run_id=?',(identity,)).fetchone()
        return {**dict(row),'local_request_stopped':True,'provider_effects_checked':True} if row else None

    def get(self,identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._run(db,identity)
            return self._get(db,identity)

    def pending(self,limit=100):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('limit 必须为1–100整数')
        with self.store.connect() as db:
            return [{**dict(row),'usage':json.loads(row['usage']) if row['usage'] else None} for row in db.execute("""
                SELECT r.*,CASE WHEN t.run_id IS NOT NULL THEN 'retrospective'
                WHEN p.run_id IS NOT NULL THEN 'planning' ELSE 'reply' END kind,t.scope,t.scope_id
                FROM runs r LEFT JOIN retrospective_requests t ON t.run_id=r.id LEFT JOIN planning_requests p ON p.run_id=r.id
                WHERE r.state='unknown' AND NOT EXISTS(SELECT 1 FROM model_run_reconciliations c WHERE c.run_id=r.id)
                ORDER BY r.created_at,r.id LIMIT ?""",(limit,))]

    def save(self,identity,payload):
        if not isinstance(payload,dict) or set(payload)!={'request_id','attempt','requirement_version','local_request_stopped','provider_effects_checked','note'}:
            raise ValueError('模型核查请求字段不完整或含未知字段')
        values={**payload,'request_id':_text(payload['request_id'],'请求 ID',120),'note':_text(payload['note'],'核查说明',2000)}
        for name in ('attempt','requirement_version'):
            if type(values[name]) is not int or values[name]<1:raise ValueError('版本必须为正整数')
        if values['local_request_stopped'] is not True or values['provider_effects_checked'] is not True:
            raise ValueError('必须确认本地请求已停止并已核查供应商影响')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=self._run(db,identity)
            old=self._get(db,identity)
            if old:
                if any(old[k]!=v for k,v in values.items()):raise ValueError('模型 Run 已有不可覆盖核查记录')
                return old
            if run['state']!='unknown':raise ValueError('只有未知模型 Run 可核查')
            if any(run[k]!=values[k] for k in ('attempt','requirement_version')):
                raise TaskVersionConflict('核查版本与原模型 Run 不一致')
            db.execute('''INSERT INTO model_run_reconciliations
                (run_id,request_id,attempt,requirement_version,local_request_stopped,provider_effects_checked,note)
                VALUES(?,?,?,?,?,?,?)''',(identity,values['request_id'],values['attempt'],values['requirement_version'],True,True,values['note']))
            return self._get(db,identity)
