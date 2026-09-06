"""Shared persistent reservation admission is atomic with actual execution claims."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_workbench_executions import setup, request
from workbench.runs import Runs
from workbench.budgets import Budgets, BudgetDenied, reserve


def fixture(tmp_path):
    store,tasks,executions,task=setup(tmp_path)
    runs=Runs(store)
    model=runs.create(task['conversation_id'],{'agent_id':task['agent_id'],'source_message_id':task['source_message_id'],'request_id':'model'})
    cli=executions.create(task['id'],request())
    return store,tasks,executions,runs,model,cli,Budgets(store)


def config(total=10,model=10,cli=10,enabled=True):
    return {'enabled':enabled,'total_micro_usd':total,'model_reserve_micro_usd':model,'cli_reserve_micro_usd':cli}


@pytest.mark.parametrize('changes',[{'enabled':1},{'enabled':'false'},{'total_micro_usd':-1},{'total_micro_usd':10**12+1},
    {'model_reserve_micro_usd':0},{'cli_reserve_micro_usd':True},{'cli_reserve_micro_usd':1.2},{'extra':True}])
def test_invalid_budget_patch_is_atomic(tmp_path,changes):
    budget=fixture(tmp_path)[-1]
    before=budget.get()
    with pytest.raises(ValueError): budget.save(config()|changes)
    assert budget.get()==before
    with pytest.raises(ValueError): budget.save({'enabled':False})


def test_default_off_old_runs_not_retroactively_charged(tmp_path):
    store,_,executions,runs,model,cli,budget=fixture(tmp_path)
    assert budget.get()['enabled'] is False
    assert runs.claim(model['id']) and executions.claim(cli['id'])
    assert budget.list()==[]
    budget.save(config())
    assert not runs.claim(model['id']) and not executions.claim(cli['id'])
    assert budget.get()['reserved_micro_usd']==0


def test_model_and_cli_compete_for_same_last_amount(tmp_path):
    _,_,executions,runs,model,cli,budget=fixture(tmp_path)
    budget.save(config())
    def start(args):
        api,identity=args
        try: return api.claim(identity)
        except BudgetDenied: return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(start,[(runs,model['id']),(executions,cli['id'])]))
    assert sorted(results,key=str)==[True,'denied']
    assert budget.get()['reserved_micro_usd']==10 and budget.get()['reservation_count']==1
    assert sorted([runs.get(model['id'])['state'],executions.get(cli['id'])['state']])==['queued','running']
    budget.save(config(total=20))
    assert sum([runs.claim(model['id']),executions.claim(cli['id'])])==1
    assert budget.get()['reserved_micro_usd']==20


@pytest.mark.parametrize('kind',['model','cli'])
def test_claim_failure_rolls_back_reservation(tmp_path,kind):
    store,_,executions,runs,model,cli,budget=fixture(tmp_path)
    budget.save(config())
    api,run,table=(runs,model,'runs') if kind=='model' else (executions,cli,'task_executions')
    with store.connect() as db:
        db.execute(f"CREATE TRIGGER injection BEFORE UPDATE ON {table} WHEN NEW.state='running' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError): api.claim(run['id'])
    assert api.get(run['id'])['state']=='queued' and budget.list()==[]


def test_changes_disable_recovery_and_failed_runs_never_refund(tmp_path):
    store,_,executions,runs,model,cli,budget=fixture(tmp_path)
    budget.save(config(total=30,model=10,cli=20))
    runs.claim(model['id']); executions.claim(cli['id'])
    original=budget.list()
    runs.fail(model['id'],'failed')
    executions.cancel(cli['id']); executions.recover()
    changed=budget.save(config(total=1,model=3,cli=4,enabled=False))
    assert changed['available_micro_usd']==-29 and changed['reserved_micro_usd']==30
    assert Budgets(store).list()==original
    with store.connect() as db:
        for kind,run in [('model',model),('cli',cli)]:
            assert reserve(db,kind,run)['amount_micro_usd'] == (10 if kind=='model' else 20)
    assert budget.get()['reservation_count']==2
    assert len(budget.list(model['agent_id']))==2
    with pytest.raises(KeyError): budget.list('missing')
    with pytest.raises(ValueError): budget.list('')
    with store.connect() as db:
        for sql in ('DELETE FROM budget_reservations','UPDATE budget_reservations SET amount_micro_usd=1','INSERT OR REPLACE INTO budget_reservations SELECT * FROM budget_reservations'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_revoked_or_stale_claims_do_not_reserve(tmp_path):
    store,tasks,executions,runs,model,cli,budget=fixture(tmp_path)
    budget.save(config(total=20))
    store.save_agent({'enabled':False},model['agent_id'])
    assert not runs.claim(model['id']) and runs.get(model['id'])['state']=='failed'
    assert not executions.claim(cli['id'])
    assert budget.list()==[]


def test_dependency_wait_does_not_reserve(tmp_path):
    from test_workbench_project_executions import fixture as batch_fixture
    store,_,plan,batches,p=batch_fixture(tmp_path)
    source=batches.create(plan['id'],p)
    budget=Budgets(store);budget.save(config())
    assert not batches.executions.claim(source['tasks'][1]['execution_id'])
    assert budget.list()==[]


def test_duplicate_claim_and_forged_binding_do_not_double_reserve(tmp_path):
    store,_,_,runs,model,_,budget=fixture(tmp_path)
    budget.save(config())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:runs.claim(model['id']),range(3)))
    assert results.count(True)==1 and budget.get()['reservation_count']==1
    with store.connect() as db:
        with pytest.raises(ValueError): reserve(db,'model',{**model,'attempt':2})
        with pytest.raises(ValueError): reserve(db,'unknown',model)
    assert budget.get()['reservation_count']==1


def test_revision_change_and_old_database_initialization_preserve_data(tmp_path):
    store,tasks,executions,_,_,cli,budget=fixture(tmp_path)
    task=tasks.get(cli['task_id'])
    tasks.revise(task['id'],{'expected_version':1,**{k:task[k] for k in ('title','scope','acceptance','agent_id')},'scope':'new version'})
    budget.save(config())
    assert not executions.claim(cli['id']) and executions.get(cli['id'])['state']=='superseded'
    assert budget.list()==[]
    with store.connect() as db:
        before=[tuple(row) for row in db.execute('SELECT * FROM task_executions')]
        db.execute('DROP TABLE budget_reservations');db.execute('DROP TABLE budget_settings')
    Runs(store)
    assert Budgets(store).get()['enabled'] is False
    with store.connect() as db: assert [tuple(row) for row in db.execute('SELECT * FROM task_executions')]==before


def test_recent_hundred_and_revisions_do_not_reprice(tmp_path):
    _,_,_,runs,model,_,budget=fixture(tmp_path)
    assert budget.save(config(total=200,model=1))['revision']==1
    for index in range(101):
        run=model if index==0 else runs.create(model['conversation_id'],{'agent_id':model['agent_id'],'source_message_id':model['source_message_id'],'request_id':str(index)})
        assert runs.claim(run['id'])
        runs.fail(run['id'],'done')
    assert budget.save(config(total=200,model=2))['revision']==2
    assert len(budget.list())==100 and budget.get()['reservation_count']==101
    assert budget.get()['reserved_micro_usd']==101
    assert all(row['amount_micro_usd']==1 and row['config_revision']==1 for row in budget.list())
