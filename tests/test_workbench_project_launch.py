import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workbench_collaboration import setup
from workbench.project_launches import ProjectLaunches
from workbench.cli_controller import CLIController
from workbench.store import Store


def fixture(tmp_path):
    store,plans,cid,plan=setup(tmp_path)
    store.save_agent({'tools':['read','execute']},plan['tasks'][0]['agent_id'])
    return store,plans,cid,ProjectLaunches(store),{'plan':plan,'confirm_execution':True}


def counts(store):
    with store.connect() as db:
        return {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('conversations','members','messages','tasks','task_revisions','task_dependencies','collaboration_receipts','task_executions','project_execution_batches','project_launches')}


def test_atomic_launch_receipts_current_dependencies_and_stop(tmp_path):
    store,plans,cid,launch,p=fixture(tmp_path)
    receipt=launch.create(cid,p)
    assert receipt['request_payload']==p
    assert launch.get(receipt['id'])==receipt and launch.list(cid)==[receipt]
    assert plans.get(receipt['collaboration']['id'])==receipt['collaboration']
    assert receipt['batch']['collaboration_id']==receipt['collaboration']['id']
    detail=launch.batches.get(receipt['batch']['id'])
    assert len(detail['items'])==2
    assert all(item['execution']['state']=='queued' and item['execution']['attempt']==1 and item['execution']['requirement_version']==1 for item in detail['items'])
    assert not detail['items'][1]['dependencies']['ready']
    assert launch.batches.executions.claim(detail['items'][0]['execution']['id'])
    assert not launch.batches.executions.claim(detail['items'][1]['execution']['id'])
    launch.batches.stop(receipt['batch']['id'])
    assert [i['execution']['state'] for i in launch.batches.get(receipt['batch']['id'])['items']]==['stopping','cancelled']
    assert launch.create(cid,p)==receipt


def test_concurrent_exact_replay_and_authorization_change(tmp_path):
    store,_,cid,launch,p=fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=5) as pool: receipts=list(pool.map(lambda _:launch.create(cid,p),range(5)))
    assert all(r==receipts[0] for r in receipts)
    store.save_agent({'enabled':False},p['plan']['tasks'][0]['agent_id'])
    store.save_conversation({'archived':True},cid)
    assert ProjectLaunches(Store(tmp_path)).create(cid,p)==receipts[0]
    changed=copy.deepcopy(p);changed['plan']['shared_brief']='Changed'
    with pytest.raises(ValueError):launch.create(cid,changed)
    assert counts(store)['task_executions']==2
    with store.connect() as db:
        for sql in ('DELETE FROM project_launches','UPDATE project_launches SET snapshot=snapshot','INSERT OR REPLACE INTO project_launches SELECT * FROM project_launches'):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)


@pytest.mark.parametrize('failure',['permission','cycle','source','batch-insert','receipt-insert'])
def test_all_or_nothing_including_actual_writes(tmp_path,failure):
    store,_,cid,launch,p=fixture(tmp_path)
    if failure=='permission':store.save_agent({'tools':['read']},p['plan']['tasks'][0]['agent_id'])
    if failure=='cycle':p['plan']['tasks'][0]['depends_on']=['b']
    if failure=='source':p['plan']['source_message_id']='foreign'
    if failure in ('batch-insert','receipt-insert'):
        table='project_execution_batches' if failure=='batch-insert' else 'project_launches'
        with store.connect() as db:db.execute(f"CREATE TRIGGER injected BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'fixture'); END")
    before=counts(store)
    with pytest.raises((ValueError,PermissionError,sqlite3.IntegrityError)):launch.create(cid,p)
    assert counts(store)==before


@pytest.mark.parametrize('confirm',[False,1,None])
def test_strict_confirmation_and_extra_fields(tmp_path,confirm):
    store,_,cid,launch,p=fixture(tmp_path)
    before=counts(store)
    with pytest.raises(ValueError):launch.create(cid,{**p,'confirm_execution':confirm})
    with pytest.raises(ValueError):launch.create(cid,{**p,'extra':True})
    with pytest.raises(ValueError):launch.create(cid,{'plan':p['plan']})
    assert counts(store)==before


def test_only_created_project_is_not_silently_launched(tmp_path):
    store,plans,cid,launch,p=fixture(tmp_path)
    existing=plans.create(cid,p['plan']);before=counts(store)
    with pytest.raises(ValueError):launch.create(cid,p)
    assert counts(store)==before
    assert plans.create(cid,p['plan'])==existing


def test_capacity_failure_rolls_back_entire_new_project(tmp_path):
    store,plans,cid,launch,p=fixture(tmp_path)
    owner=p['plan']['coordinator_id']
    store.save_agent({'tools':['read','execute']},owner)
    # Real public admissions establish 99 independent queued tasks, leaving one slot.
    for index in range(99):
        task=plans.tasks.create(cid,dict(request_id=f'fill-{index}',source_message_id=p['plan']['source_message_id'],title='capacity',scope='scope',acceptance='accept',agent_id=owner))
        launch.batches.executions.create(task['id'],dict(request_id='fill',expected_version=1,previous_execution_id=None,reconciliation_note=''))
    before=counts(store)
    with pytest.raises(ValueError,match='队列已满'):launch.create(cid,p)
    assert counts(store)==before


def test_controller_configuration_and_closed_replay(tmp_path,monkeypatch):
    store,_,cid,_,p=fixture(tmp_path)
    controller=CLIController(store)
    try:
        with pytest.raises(ValueError):controller.launch_project(cid,p)
        assert counts(store)['project_launches']==0
        monkeypatch.setattr(controller.settings,'resolve',lambda:{})
        receipt=controller.launch_project(cid,p)
        controller.close()
        assert controller.launch_project(cid,p)==receipt
        with pytest.raises(ValueError):controller.launch_project(cid,{**p,'plan':{**p['plan'],'request_id':'new'}})
    finally:
        if not controller.closed:controller.close()
