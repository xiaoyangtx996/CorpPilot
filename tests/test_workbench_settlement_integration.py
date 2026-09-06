"""Owner fee declarations: HTTP, controller ownership, dispatch and offline recovery."""
from concurrent.futures import Future
from threading import Event
from urllib.parse import quote
import pytest

from test_workbench_api import running, request
from test_workbench_budget_integration import make_run, policy
from test_workbench_cli_controller import fixture, result, until as cli_until
from workbench.budgets import Budgets
from workbench.runs import Runs
from workbench.executions import Executions
from workbench.store import Store
from workbench.settings import Settings
from workbench.controller import ReplyController
from workbench import cli_controller
from workbench.backup import backup, restore


def statement(amount=500000, revision=0, request_id='fee/first'):
    return dict(request_id=request_id, expected_revision=revision, attempt=1, requirement_version=1,
                amount_micro_usd=amount, evidence_reference='Owner fixture invoice reference',
                note='Inspected full run cost; may release budget for existing queue', confirm=True)


def test_real_http_declarations_corrections_and_exact_old_request(tmp_path):
    with running(tmp_path) as port:
        store=Store(tmp_path); runs=Runs(store); budget=Budgets(store); budget.save(policy())
        run=make_run(store,runs); assert runs.claim(run['id']); runs.fail(run['id'],'Fixture ended')
        original=runs.get(run['id']); path='/api/workbench/budget-settlements/model/'+run['id']
        p=statement(2000000)
        assert request(port,'GET',path,headers={'Authorization':''})[0]==401
        assert request(port,'POST',path,p,headers={'Origin':'https://foreign.example'})[0]==400
        assert request(port,'POST',path,{**p,'confirm':False})[0]==400
        assert request(port,'GET',path)==(200,None)
        status,first=request(port,'POST',path,p)
        assert status==201 and first['source']=='owner_declared' and first['revision']==1
        assert budget.get()['available_micro_usd']==-1000000
        store.save_agent({'enabled':False},run['agent_id'])
        correction=statement(500000,1,'fee/correct')
        status,second=request(port,'POST',path,correction)
        assert status==201 and second['revision']==2
        assert request(port,'POST',path,p)==(201,first)
        assert request(port,'GET',path+'/requests/'+quote(p['request_id'],safe=''))==(200,first)
        assert request(port,'GET',path+'/history')==(200,[second,first])
        assert request(port,'GET',path)==(200,second)
        assert request(port,'GET','/api/workbench/budget-settlements')==(200,[second])
        assert request(port,'GET','/api/workbench/agents/'+run['agent_id']+'/budget-settlements')==(200,[second])
        assert request(port,'POST',path,{**correction,'request_id':'stale'})[0]==400
        assert request(port,'POST',path,{**p,'amount_micro_usd':1})[0]==400
        assert budget.get()['committed_micro_usd']==500000
        assert budget.get()['unsettled_reserved_micro_usd']==0
        assert budget.get()['reserved_micro_usd']==1000000
        assert runs.get(run['id'])==original
    with running(tmp_path) as port:
        assert request(port,'GET',path)==(200,second)
        assert request(port,'POST',path,p)==(201,first)


@pytest.mark.parametrize('holder',['future','unsettled','uncertain'])
def test_model_terminal_but_held_cannot_settle_even_with_padded_id(tmp_path,holder):
    store=Store(tmp_path); controller=ReplyController(store,Settings(store)); budget=Budgets(store)
    try:
        run=make_run(store,controller.runs); assert controller.runs.claim(run['id'])
        controller.runs.fail(run['id'],'Ended but controller still owns completion')
        p=statement()
        future=Future()
        with controller.state_lock:
            target=controller.unsettled if holder=='unsettled' else controller.uncertain_submissions
            if holder=='future': controller.futures[future]=run['id']
            else: target.add(run['id'])
            try:
                for identity in (run['id'],' '+run['id']+' '):
                    with pytest.raises(ValueError,match='持有'): controller.settle_fee(identity,p)
                assert budget.settlement('model',run['id']) is None
            finally:
                if holder=='future': del controller.futures[future]
                else: target.remove(run['id'])
        receipt=controller.settle_fee(run['id'],p)
    finally:
        controller.close()
    assert controller.settle_fee(run['id'],p)==receipt
    with pytest.raises(ValueError,match='关闭'): controller.settle_fee(run['id'],statement(0,1,'correction'))


def test_cli_terminal_but_held_is_not_a_fee_release(tmp_path,monkeypatch):
    store,_,controller,_,runs=fixture(tmp_path,monkeypatch)
    run=runs[0]; assert controller.executions.claim(run['id'])
    controller.executions.report(run['id'],1,1,1,'Exited while report cleanup owned')
    try:
        with controller.launch_lock:
            controller.active[run['id']]=(run,Event(),Future())
            try:
                with pytest.raises(ValueError,match='持有'): controller.settle_fee(' '+run['id']+' ',statement())
                assert Budgets(store).settlement('cli',run['id']) is None
            finally:
                del controller.active[run['id']]
        receipt=controller.settle_fee(run['id'],statement())
    finally:
        controller.close()
    assert controller.settle_fee(run['id'],statement())==receipt
    with pytest.raises(ValueError,match='关闭'): controller.settle_fee(run['id'],statement(0,1,'later'))


def test_fee_release_admits_one_waiting_cli_and_correction_blocks_next(tmp_path,monkeypatch):
    store,_,controller,_,runs=fixture(tmp_path,monkeypatch,3)
    budget=Budgets(store); budget.save(policy())
    calls=[]
    monkeypatch.setattr(cli_controller,'run_codex',lambda **kw:calls.append(kw['execution_id']) or result(1,False))
    try:
        cli_until(controller,lambda:len(calls)==1 and not controller.active)
        first=calls[0]; original=controller.executions.get(first)
        assert sum(controller.executions.get(r['id'])['state']=='queued' for r in runs)==2
        controller.settle_fee(first,statement(0))
        cli_until(controller,lambda:len(calls)==2 and not controller.active)
        controller.settle_fee(first,statement(2000000,1,'higher-invoice'))
        for _ in range(3):controller.tick()
        assert len(calls)==2 and budget.get()['available_micro_usd']==-2000000
        assert budget.get()['settled_micro_usd']==2000000
        assert budget.get()['unsettled_reserved_micro_usd']==1000000
        assert controller.executions.get(first)==original
        assert '预算' in controller.status()['error']
    finally:
        controller.close()


def test_settlement_revisions_survive_real_backup_restore(tmp_path):
    source=tmp_path/'source'; store=Store(source); runs=Runs(store); Executions(store)
    budget=Budgets(store); budget.save(policy())
    run=make_run(store,runs); assert runs.claim(run['id']); runs.fail(run['id'],'Completed provider failure')
    first=budget.settle('model',run['id'],statement(2000000))
    second=budget.settle('model',run['id'],statement(250000,1,'corrected'))
    before=budget.get()
    backup(source,tmp_path/'bundle');restore(tmp_path/'bundle',tmp_path/'restored')
    restored=Budgets(Store(tmp_path/'restored'))
    assert restored.settlement('model',run['id'])==second
    assert restored.settle('model',run['id'],statement(2000000))==first
    assert restored.settlement_history('model',run['id'])==[second,first]
    assert restored.get()==before
