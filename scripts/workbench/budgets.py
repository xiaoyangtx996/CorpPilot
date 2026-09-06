"""Durable Owner-assigned USD admission reservations, not provider billing."""
import json

from .store import _text

DEFAULTS = {'enabled': False, 'total_micro_usd': 0,
            'model_reserve_micro_usd': 1000000, 'cli_reserve_micro_usd': 1000000}
MAX_AMOUNT = 10 ** 12
MAX_TOTAL = 9007199254740991


class BudgetDenied(ValueError):
    pass


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS budget_settings (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=1), config TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS budget_reservations (
        kind TEXT NOT NULL CHECK(kind IN ('model','cli')), run_id TEXT NOT NULL,
        agent_id TEXT NOT NULL REFERENCES agents(id), attempt INTEGER NOT NULL CHECK(attempt>=1),
        requirement_version INTEGER NOT NULL CHECK(requirement_version>=1),
        amount_micro_usd INTEGER NOT NULL CHECK(amount_micro_usd>=1 AND amount_micro_usd<=1000000000000),
        config_revision INTEGER NOT NULL CHECK(config_revision>=1),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(kind,run_id))''')
    for operation in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS budget_reservations_no_{operation.lower()} BEFORE {operation} ON budget_reservations BEGIN SELECT RAISE(ABORT,'Budget reservations are immutable'); END")
    db.execute("""CREATE TRIGGER IF NOT EXISTS budget_reservations_no_replace BEFORE INSERT ON budget_reservations
        WHEN EXISTS(SELECT 1 FROM budget_reservations WHERE kind=NEW.kind AND run_id=NEW.run_id)
        BEGIN SELECT RAISE(ABORT,'Budget reservations are immutable'); END""")
    db.execute('''CREATE TABLE IF NOT EXISTS budget_settlements (
        kind TEXT NOT NULL CHECK(kind IN ('model','cli')), run_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=1), request_id TEXT NOT NULL,
        agent_id TEXT NOT NULL REFERENCES agents(id), amount_micro_usd INTEGER NOT NULL CHECK(amount_micro_usd>=0 AND amount_micro_usd<=1000000000000),
        payload TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(kind,run_id,revision), UNIQUE(kind,run_id,request_id))''')
    for operation in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS budget_settlements_no_{operation.lower()} BEFORE {operation} ON budget_settlements BEGIN SELECT RAISE(ABORT,'Budget settlements are immutable'); END")
    db.execute('''CREATE TRIGGER IF NOT EXISTS budget_settlements_no_replace BEFORE INSERT ON budget_settlements
        WHEN EXISTS(SELECT 1 FROM budget_settlements WHERE kind=NEW.kind AND run_id=NEW.run_id AND (revision=NEW.revision OR request_id=NEW.request_id))
        BEGIN SELECT RAISE(ABORT,'Budget settlements are immutable'); END''')


def totals(db):
    reserved,count = db.execute('SELECT COALESCE(sum(amount_micro_usd),0),count(*) FROM budget_reservations').fetchone()
    unsettled = db.execute('''SELECT COALESCE(sum(amount_micro_usd),0) FROM budget_reservations r WHERE NOT EXISTS
        (SELECT 1 FROM budget_settlements s WHERE s.kind=r.kind AND s.run_id=r.run_id)''').fetchone()[0]
    settled,settlement_count = db.execute('''SELECT COALESCE(sum(amount_micro_usd),0),count(*) FROM budget_settlements s
        WHERE revision=(SELECT max(revision) FROM budget_settlements p WHERE p.kind=s.kind AND p.run_id=s.run_id)''').fetchone()
    result = {'reserved_micro_usd':reserved,'reservation_count':count,'unsettled_reserved_micro_usd':unsettled,
              'settled_micro_usd':settled,'settlement_count':settlement_count,'committed_micro_usd':unsettled+settled}
    if any(type(value) is not int or not 0 <= value <= MAX_TOTAL for value in result.values()):
        raise ValueError('预算累计金额或记录数超过安全整数上限')
    return result


def validate(payload):
    if not isinstance(payload,dict) or set(payload) != set(DEFAULTS) or type(payload['enabled']) is not bool:
        raise ValueError('预算配置须完整包含 enabled 和三个整数微美元金额')
    for field in ('total_micro_usd','model_reserve_micro_usd','cli_reserve_micro_usd'):
        if type(payload[field]) is not int or not (0 if field=='total_micro_usd' else 1) <= payload[field] <= MAX_AMOUNT:
            raise ValueError('预算金额范围无效；总额为0–1000000000000，每次预留为1–1000000000000微美元')
    return dict(payload)


def settings(db):
    row = db.execute('SELECT revision,config FROM budget_settings WHERE id=1').fetchone()
    return (validate(json.loads(row['config'])), row['revision']) if row else (dict(DEFAULTS),0)


def reserve(db, kind, run):
    if kind not in ('model','cli'): raise ValueError('预算执行类型无效')
    table = 'runs' if kind=='model' else 'task_executions'
    actual = db.execute(f'SELECT * FROM {table} WHERE id=?',(run['id'],)).fetchone()
    fields = ('agent_id','attempt','requirement_version')
    if actual is None or any(type(run.get(key)) is not int or run[key]<1 for key in ('attempt','requirement_version')) or any(actual[key]!=run[key] for key in fields):
        raise ValueError('预算预留实例绑定无效')
    previous = db.execute('SELECT * FROM budget_reservations WHERE kind=? AND run_id=?',(kind,run['id'])).fetchone()
    if previous:
        if any(previous[key]!=run[key] for key in fields): raise ValueError('预算预留实例冲突')
        return dict(previous)
    config,revision = settings(db)
    if not config['enabled']: return None
    if actual['state']!='queued': raise ValueError('仅排队实例可以首次预留预算')
    amount = config[f'{kind}_reserve_micro_usd']
    metrics = totals(db)
    reserved = metrics['committed_micro_usd']
    if metrics['reserved_micro_usd'] + amount > MAX_TOTAL:
        raise BudgetDenied('预算历史预留累计超过安全整数上限；未启动')
    if reserved + amount > config['total_micro_usd']:
        raise BudgetDenied(f'预算准入等待：本次需预留 {amount} 微美元，可用 {config["total_micro_usd"]-reserved} 微美元；原任务保留排队')
    db.execute('''INSERT INTO budget_reservations(kind,run_id,agent_id,attempt,requirement_version,amount_micro_usd,config_revision)
               VALUES(?,?,?,?,?,?,?)''',(kind,run['id'],run['agent_id'],run['attempt'],run['requirement_version'],amount,revision))
    return dict(db.execute('SELECT * FROM budget_reservations WHERE kind=? AND run_id=?',(kind,run['id'])).fetchone())


class Budgets:
    def __init__(self,store):
        self.store=store
        with store.connect() as db: initialize(db)

    @staticmethod
    def _get(db):
        config,revision=settings(db)
        metrics=totals(db)
        return {**config,'currency':'USD','revision':revision,**metrics,
                'available_micro_usd':config['total_micro_usd']-metrics['committed_micro_usd']}

    def get(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._get(db)

    def save(self,payload):
        config=validate(payload)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _,revision=settings(db)
            db.execute('''INSERT INTO budget_settings VALUES(1,?,?)
                       ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,config=excluded.config''',(revision+1,json.dumps(config)))
            return self._get(db)

    def list(self,agent_id=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            if agent_id is not None:
                agent_id=_text(agent_id,'Agent ID')
                if not db.execute('SELECT 1 FROM agents WHERE id=?',(agent_id,)).fetchone(): raise KeyError('Agent 不存在')
            return [dict(row) for row in db.execute('SELECT * FROM budget_reservations'+(' WHERE agent_id=?' if agent_id is not None else '')+' ORDER BY created_at DESC,kind DESC,run_id DESC LIMIT 100', (agent_id,) if agent_id is not None else ())]

    @staticmethod
    def _target(db,kind,identity):
        if kind not in ('model','cli'): raise ValueError('费用核查执行类型无效')
        identity=_text(identity,'执行 ID')
        table='runs' if kind=='model' else 'task_executions'
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone(): raise KeyError('执行不存在')
        row=db.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone()
        if row is None: raise KeyError('执行不存在')
        return row

    def settle(self,kind,identity,payload):
        fields={'request_id','attempt','requirement_version','expected_revision','amount_micro_usd','evidence_reference','note','confirm'}
        if not isinstance(payload,dict) or set(payload)!=fields or payload['confirm'] is not True:
            raise ValueError('费用核查须明确确认并完整提供八个字段')
        value={**payload,'request_id':_text(payload['request_id'],'请求 ID',120),
               'evidence_reference':_text(payload['evidence_reference'],'证据参考',500),
               'note':_text(payload['note'],'核查说明',2000)}
        for field in ('attempt','requirement_version','expected_revision','amount_micro_usd'):
            low=0 if field in ('expected_revision','amount_micro_usd') else 1
            high=MAX_AMOUNT if field=='amount_micro_usd' else MAX_TOTAL-1
            if type(value[field]) is not int or not low<=value[field]<=high: raise ValueError('费用核查金额或版本无效')
        encoded=json.dumps(value,sort_keys=True,ensure_ascii=False)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=self._target(db,kind,identity)
            identity=run['id']
            old=db.execute('SELECT payload,snapshot FROM budget_settlements WHERE kind=? AND run_id=? AND request_id=?',(kind,identity,value['request_id'])).fetchone()
            if old:
                if old['payload']!=encoded: raise ValueError('request_id 已用于不同费用核查')
                return json.loads(old['snapshot'])
            latest=db.execute('SELECT COALESCE(max(revision),0) FROM budget_settlements WHERE kind=? AND run_id=?',(kind,identity)).fetchone()[0]
            if latest!=value['expected_revision']: raise ValueError('费用核查版本已变化，请重新读取')
            if any(run[field]!=value[field] for field in ('attempt','requirement_version')): raise ValueError('费用核查与原执行版本不一致')
            if run['state'] in ('queued','running','stopping'): raise ValueError('执行尚未结束，不能核查完整费用')
            if run['state']=='unknown':
                table,field,stopped,checked=('model_run_reconciliations','run_id','local_request_stopped','provider_effects_checked') if kind=='model' else ('execution_reconciliations','execution_id','process_stopped','external_effects_checked')
                declaration=db.execute(f'SELECT * FROM {table} WHERE {field}=?',(identity,)).fetchone()
                if declaration is None or declaration['attempt']!=run['attempt'] or declaration['requirement_version']!=run['requirement_version'] or declaration[stopped]!=1 or declaration[checked]!=1:
                    raise ValueError('未知实例须先核查停止与外部影响，不能认定费用为零')
            created=db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            receipt={'kind':kind,'run_id':identity,'agent_id':run['agent_id'],'attempt':run['attempt'],
                     'requirement_version':run['requirement_version'],'revision':latest+1,'request_id':value['request_id'],
                     'request_payload':value,'amount_micro_usd':value['amount_micro_usd'],
                     'evidence_reference':value['evidence_reference'],'note':value['note'],'source':'owner_declared','created_at':created}
            db.execute('INSERT INTO budget_settlements VALUES(?,?,?,?,?,?,?,?,?)',
                       (kind,identity,latest+1,value['request_id'],run['agent_id'],value['amount_micro_usd'],encoded,json.dumps(receipt,ensure_ascii=False),created))
            totals(db)  # Reject overflow within the same transaction; never truncate a declared amount.
            return receipt

    def settlement(self,kind,identity,request_id=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            identity=self._target(db,kind,identity)['id']
            if request_id is None:
                row=db.execute('SELECT snapshot FROM budget_settlements WHERE kind=? AND run_id=? ORDER BY revision DESC LIMIT 1',(kind,identity)).fetchone()
            else:
                request_id=_text(request_id,'请求 ID',120)
                row=db.execute('SELECT snapshot FROM budget_settlements WHERE kind=? AND run_id=? AND request_id=?',(kind,identity,request_id)).fetchone()
            return json.loads(row[0]) if row else None

    def settlement_history(self,kind,identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            identity=self._target(db,kind,identity)['id']
            return [json.loads(row[0]) for row in db.execute('SELECT snapshot FROM budget_settlements WHERE kind=? AND run_id=? ORDER BY revision DESC LIMIT 100',(kind,identity))]

    def settlements(self,agent_id=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            if agent_id is not None:
                agent_id=_text(agent_id,'Agent ID')
                if not db.execute('SELECT 1 FROM agents WHERE id=?',(agent_id,)).fetchone(): raise KeyError('Agent 不存在')
            return [json.loads(row[0]) for row in db.execute('''SELECT snapshot FROM budget_settlements s
                WHERE revision=(SELECT max(revision) FROM budget_settlements p WHERE p.kind=s.kind AND p.run_id=s.run_id)'''
                + (' AND agent_id=?' if agent_id is not None else '')+' ORDER BY created_at DESC,kind DESC,run_id DESC LIMIT 100',(agent_id,) if agent_id is not None else ())]
