"""Durable Owner-assigned USD admission reservations, not provider billing."""
import json

from .store import _text

DEFAULTS = {'enabled': False, 'total_micro_usd': 0,
            'model_reserve_micro_usd': 1000000, 'cli_reserve_micro_usd': 1000000}
MAX_AMOUNT = 10 ** 12


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
    reserved = db.execute('SELECT COALESCE(sum(amount_micro_usd),0) FROM budget_reservations').fetchone()[0]
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
        row=db.execute('SELECT COALESCE(sum(amount_micro_usd),0),count(*) FROM budget_reservations').fetchone()
        return {**config,'currency':'USD','revision':revision,'reserved_micro_usd':row[0],
                'available_micro_usd':config['total_micro_usd']-row[0],'reservation_count':row[1]}

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
