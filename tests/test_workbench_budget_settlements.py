"""Owner declarations supersede prior declared cost, never original execution evidence."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_workbench_budgets import fixture, config
from workbench.budgets import Budgets, BudgetDenied
from workbench import budgets


def payload(amount=4,revision=0,key='bill'):
    return {'request_id':key,'attempt':1,'requirement_version':1,'expected_revision':revision,
            'amount_micro_usd':amount,'evidence_reference':'invoice line 42','note':'Owner checked full charge','confirm':True}


def ended(tmp_path,enabled=True):
    store,tasks,executions,runs,model,cli,budget=fixture(tmp_path)
    budget.save(config(total=20,enabled=enabled))
    runs.claim(model['id']);executions.claim(cli['id'])
    runs.fail(model['id'],'failed');executions.report(cli['id'],1,1,1,'failed')
    return store,tasks,executions,runs,model,cli,budget


def test_latest_declaration_changes_occupancy_not_original_records(tmp_path):
    store,_,_,_,model,cli,budget=ended(tmp_path)
    with store.connect() as db:
        originals={table:[tuple(row) for row in db.execute(f'SELECT * FROM {table}')] for table in ('runs','task_executions','budget_reservations','execution_artifacts')}
    first=budget.settle('model',model['id'],payload())
    assert first['source']=='owner_declared' and first['revision']==1
    metrics=budget.get()
    assert (metrics['reserved_micro_usd'],metrics['unsettled_reserved_micro_usd'],metrics['settled_micro_usd'],metrics['committed_micro_usd'],metrics['available_micro_usd'])==(20,10,4,14,6)
    correction=budget.settle('model',model['id'],payload(30,1,'correction'))
    assert correction['revision']==2
    assert budget.get()['available_micro_usd']==-20
    assert budget.settle('model',model['id'],payload())==first
    assert budget.settlement('model',model['id'])==correction
    assert budget.settlement('model',model['id'],'bill')==first
    assert budget.settlement_history('model',model['id'])==[correction,first]
    assert budget.settlements()==[correction]
    assert budget.settlements(model['agent_id'])==[correction]
    with store.connect() as db:
        for table,before in originals.items(): assert [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]==before
    assert Budgets(store).get()==budget.get()


def test_legacy_no_reservation_explicit_zero_and_overrun(tmp_path):
    store,_,_,_,model,cli,budget=ended(tmp_path,enabled=False)
    assert budget.list()==[]
    store.save_agent({'enabled':False},model['agent_id'])
    budget.settle('model',model['id'],payload(0))
    assert budget.get()['settlement_count']==1 and budget.get()['settled_micro_usd']==0
    budget.settle('cli',cli['id'],payload(10**12))
    assert budget.get()['committed_micro_usd']==10**12
    assert budget.get()['available_micro_usd']==20-10**12
    with pytest.raises(KeyError): budget.settlement('model','missing')
    with pytest.raises(KeyError): budget.settlements('missing')


@pytest.mark.parametrize('changes',[{'confirm':False},{'amount_micro_usd':True},{'amount_micro_usd':-1},{'amount_micro_usd':10**12+1},
    {'expected_revision':True},{'attempt':2},{'requirement_version':2},{'note':' '},{'evidence_reference':''},{'extra':1}])
def test_invalid_declaration_has_no_partial_rows(tmp_path,changes):
    *_,model,cli,budget=ended(tmp_path)
    before=budget.get()
    with pytest.raises(ValueError): budget.settle('model',model['id'],payload()|changes)
    assert budget.get()==before and budget.settlement('model',model['id']) is None


def test_normalized_same_request_and_concurrent_revision_conflict(tmp_path):
    *_,model,cli,budget=ended(tmp_path)
    first=budget.settle('model',model['id'],payload()|{'request_id':' bill ','note':' Owner checked full charge '})
    assert budget.settle('model',model['id'],payload())==first
    with pytest.raises(ValueError): budget.settle('model',model['id'],payload(5))
    def correct(key):
        try: return budget.settle('model',model['id'],payload(3,1,key))['revision']
        except ValueError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(correct,['a','b']))
    assert results.count(2)==1 and results.count('conflict')==1
    assert budget.get()['settled_micro_usd']==3


@pytest.mark.parametrize('kind',['model','cli'])
def test_active_and_unchecked_unknown_forbidden(tmp_path,kind):
    store,_,executions,runs,model,cli,budget=fixture(tmp_path)
    run=model if kind=='model' else cli
    with pytest.raises(ValueError): budget.settle(kind,run['id'],payload())
    (runs if kind=='model' else executions).claim(run['id'])
    with pytest.raises(ValueError): budget.settle(kind,run['id'],payload())
    (runs if kind=='model' else executions).recover()
    with pytest.raises(ValueError,match='未知'): budget.settle(kind,run['id'],payload())
    with store.connect() as db:
        if kind=='model':
            db.execute("INSERT INTO model_run_reconciliations(run_id,request_id,attempt,requirement_version,local_request_stopped,provider_effects_checked,note) VALUES(?,'checked',1,1,1,1,'checked')",(run['id'],))
        else:
            db.execute("INSERT INTO execution_reconciliations(execution_id,request_id,attempt,requirement_version,process_stopped,external_effects_checked,note) VALUES(?,'checked',1,1,1,1,'checked')",(run['id'],))
    assert budget.settle(kind,run['id'],payload())['amount_micro_usd']==4
    assert (runs if kind=='model' else executions).get(run['id'])['state']=='unknown'


def test_atomic_overflow_and_immutable_history(tmp_path,monkeypatch):
    store,_,_,_,model,cli,budget=ended(tmp_path,enabled=False)
    budget.settle('model',model['id'],payload(10))
    monkeypatch.setattr(budgets,'MAX_TOTAL',15)
    with pytest.raises(ValueError,match='安全整数'): budget.settle('cli',cli['id'],payload(10))
    assert budget.settlement('cli',cli['id']) is None and budget.get()['settled_micro_usd']==10
    with store.connect() as db:
        for sql in ('DELETE FROM budget_settlements','UPDATE budget_settlements SET amount_micro_usd=0','INSERT OR REPLACE INTO budget_settlements SELECT * FROM budget_settlements'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_released_difference_allows_only_one_competing_claim(tmp_path):
    _,_,_,runs,model,_,budget=ended(tmp_path)
    budget.save(config(total=20,model=6))
    a=runs.create(model['conversation_id'],{'agent_id':model['agent_id'],'source_message_id':model['source_message_id'],'request_id':'a'})
    b=runs.create(model['conversation_id'],{'agent_id':model['agent_id'],'source_message_id':model['source_message_id'],'request_id':'b'})
    budget.settle('model',model['id'],payload(4))
    def claim(identity):
        try: return runs.claim(identity)
        except BudgetDenied: return False
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(claim,[a['id'],b['id']]))
    assert results.count(True)==1 and budget.get()['available_micro_usd']==0
    budget.settle('model',model['id'],payload(30,1,'raise-cost'))
    waiting=b if runs.get(b['id'])['state']=='queued' else a
    with pytest.raises(BudgetDenied): runs.claim(waiting['id'])


def test_claim_racing_settlement_observes_one_atomic_ledger(tmp_path):
    _,_,_,runs,model,_,budget=ended(tmp_path)
    budget.save(config(total=20,model=6))
    next_run=runs.create(model['conversation_id'],{'agent_id':model['agent_id'],'source_message_id':model['source_message_id'],'request_id':'next'})
    def claim():
        try: return runs.claim(next_run['id'])
        except BudgetDenied: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        charge=pool.submit(budget.settle,'model',model['id'],payload(4))
        start=pool.submit(claim)
        charge.result()
        if not start.result(): assert claim()
    assert budget.get()['committed_micro_usd']==20
    assert budget.get()['reservation_count']==3


def test_recent_hundred_versions_keep_old_request_retrievable(tmp_path):
    *_,model,cli,budget=ended(tmp_path,enabled=False)
    first=None
    for revision in range(101):
        row=budget.settle('model',model['id'],payload(revision,revision,str(revision)))
        if first is None: first=row
    history=budget.settlement_history('model',model['id'])
    assert len(history)==100 and history[0]['revision']==101 and history[-1]['revision']==2
    assert budget.settlement('model',model['id'],'0')==first
    assert budget.settle('model',model['id'],payload(0,0,'0'))==first
    assert budget.get()['settled_micro_usd']==100 and budget.get()['settlement_count']==1
