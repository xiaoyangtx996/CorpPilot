import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_collaboration import setup
from workbench.project_executions import ProjectExecutions
from workbench.cli_controller import CLIController
from workbench.reviews import Reviews
from workbench import artifacts
from workbench.store import Store


def fixture(tmp_path):
    store, plans, source, payload = setup(tmp_path)
    store.save_agent({'tools': ['read', 'write', 'execute']}, payload['tasks'][0]['agent_id'])
    plan = plans.create(source, payload)
    api = ProjectExecutions(store)
    p = {'request_id': 'batch', 'tasks': [dict(task_id=identity, expected_version=1, previous_execution_id=None, reconciliation_note='') for identity in plan['task_ids'].values()]}
    return store, plans, plan, api, p


def test_atomic_dependency_review_gate_and_exact_receipt(tmp_path):
    store, plans, plan, api, p = fixture(tmp_path)
    receipt = api.create(plan['id'], p)
    assert set(receipt) == {'id','collaboration_id','project_conversation_id','request_id','request_payload','tasks','created_at'}
    assert plans.for_project(plan['project_conversation_id']) == plan
    first, second = [x['execution_id'] for x in receipt['tasks']]
    detail = api.get(receipt['id'])
    assert not detail['items'][1]['dependencies']['ready']
    assert api.executions.claim(first)
    assert not api.executions.claim(second)
    api.executions.report(first, 1, 1, 0, 'verified', success=True, artifacts=[{'path':'result.txt','data':b'proof'}])
    assert api.get(receipt['id'])['items'][0]['execution']['state'] == 'awaiting_review'
    assert not api.executions.claim(second)
    Reviews(store).save(first, dict(request_id='approve', expected_version=1, decision='approved', note='Inspected', artifact_ids=sorted(a['id'] for a in artifacts.list_for(store, first))))
    assert api.executions.claim(second)
    assert api.create(plan['id'], p) == receipt
    assert api.list(plan['id']) == [receipt]


def test_concurrency_restart_disable_and_immutable(tmp_path):
    store, _, plan, api, p = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: api.create(plan['id'], p), range(4)))
    assert all(row == receipts[0] for row in receipts)
    store.save_agent({'enabled':False}, plan['approved_plan']['tasks'][0]['agent_id'])
    reopened = ProjectExecutions(Store(tmp_path))
    assert reopened.create(plan['id'], p) == receipts[0]
    with pytest.raises(ValueError): reopened.create(plan['id'], {**p,'tasks':list(reversed(p['tasks']))})
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 2
        for sql in ('DELETE FROM project_execution_batches','UPDATE project_execution_batches SET payload=payload','INSERT OR REPLACE INTO project_execution_batches SELECT * FROM project_execution_batches'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


@pytest.mark.parametrize('bad', ['empty','too-many','duplicate','foreign','version','prior','extra','note'])
def test_invalid_batch_has_no_partial_runs(tmp_path,bad):
    store, _, plan, api, p = fixture(tmp_path)
    if bad=='empty': p['tasks']=[]
    if bad=='too-many': p['tasks']*=9
    if bad=='duplicate': p['tasks'][1]=p['tasks'][0]
    if bad=='foreign': p['tasks'][1]['task_id']='foreign'
    if bad=='version': p['tasks'][1]['expected_version']=2
    if bad=='prior': p['tasks'][1]['previous_execution_id']='foreign'
    if bad=='extra': p['tasks'][1]['extra']=True
    if bad=='note': p['tasks'][1]['reconciliation_note']=None
    with pytest.raises((ValueError,PermissionError)): api.create(plan['id'],p)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM project_execution_batches').fetchone()[0] == 0


@pytest.mark.parametrize('stage',['create','stop'])
def test_actual_transaction_rollback(tmp_path,stage):
    store, _, plan, api, p = fixture(tmp_path)
    if stage=='create':
        with store.connect() as db: db.execute("CREATE TRIGGER injected BEFORE INSERT ON project_execution_batches BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError): api.create(plan['id'],p)
        assert api.executions.pending()==[]
    else:
        receipt=api.create(plan['id'],p)
        second=receipt['tasks'][1]['execution_id']
        with store.connect() as db:
            db.execute(f"CREATE TRIGGER injected BEFORE UPDATE ON task_executions WHEN OLD.id='{second}' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError): api.stop(receipt['id'])
        assert all(row['execution']['state']=='queued' for row in api.get(receipt['id'])['items'])


def test_stop_only_bound_ids_and_running_requires_exit(tmp_path):
    _, _, plan, api, p = fixture(tmp_path)
    receipt=api.create(plan['id'],p)
    first,second=[x['execution_id'] for x in receipt['tasks']]
    api.executions.claim(first)
    assert [x['execution']['state'] for x in api.stop(receipt['id'])['items']]==['stopping','cancelled']
    task=receipt['tasks'][1]['task_id']
    replacement=api.executions.create(task,dict(request_id='replacement',expected_version=1,previous_execution_id=second,reconciliation_note='Cancelled before start; Owner requests new run'))
    api.stop(receipt['id'])
    detail=api.get(receipt['id'])
    assert detail['items'][1]['latest_execution_id']==replacement['id']
    assert detail['items'][1]['execution']['id']==second
    assert api.executions.get(replacement['id'])['state']=='queued'
    api.executions.recover()
    with pytest.raises(ValueError): api.create(plan['id'],{'request_id':'new','tasks':[{**p['tasks'][0],'previous_execution_id':first,'reconciliation_note':'Not sufficient for unknown'}]})


def test_controller_settings_gate_and_closed_exact_replay(tmp_path,monkeypatch):
    store, _, plan, _, p = fixture(tmp_path)
    controller=CLIController(store)
    try:
        with pytest.raises(ValueError): controller.enqueue_project(plan['id'],p)
        monkeypatch.setattr(controller.settings,'resolve',lambda: {})
        receipt=controller.enqueue_project(plan['id'],p)
        controller.closed=True
        assert controller.enqueue_project(plan['id'],p)==receipt
        with pytest.raises(ValueError): controller.enqueue_project(plan['id'],{**p,'request_id':'new'})
        with pytest.raises(ValueError): controller.stop_project(receipt['id'])
    finally: controller.close()


def test_project_origin_none_and_wrong_type(tmp_path):
    store,plans,source,_=setup(tmp_path)
    room=store.save_conversation({'type':'project','title':'Manual','member_ids':[store.agents()[0]['id']]})
    assert plans.for_project(room['id']) is None
    with pytest.raises(ValueError): plans.for_project(source)
