import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workbench_runs import setup
from test_workbench_planning import setup as planning_setup
from test_workbench_retrospectives import fixture as retro_setup
from workbench.model_reconciliations import ModelReconciliations
from workbench.runs import Runs
from workbench.store import Store


def declaration(**changes):
    return dict(request_id='check',attempt=1,requirement_version=1,local_request_stopped=True,provider_effects_checked=True,note='Local request stopped; provider ledger inspected',**changes)


def test_scoped_gate_pending_claim_and_immutable_replay(tmp_path):
    store,runs,cid,p=setup(tmp_path)
    original=runs.create(cid,p)
    waiting=runs.create(cid,{**p,'request_id':'waiting'})
    other_agent=next(a['id'] for a in store.agents() if a['id']!=p['agent_id'])
    unrelated=runs.create(cid,{**p,'request_id':'other','agent_id':other_agent})
    runs.claim(original['id']); runs.fail(original['id'],'provider unknown',state='unknown')
    before=runs.get(original['id'])
    assert runs.create(cid,p)==before
    with pytest.raises(ValueError):runs.create(cid,{**p,'request_id':'replacement'})
    assert not runs.claim(waiting['id'])
    assert [r['id'] for r in runs.pending(1)]==[unrelated['id']]
    assert runs.get(waiting['id'])['state']=='queued'
    checks=ModelReconciliations(store)
    assert checks.pending()[0]['kind']=='reply'
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:checks.save(original['id'],declaration()),range(3)))
    assert all(r==results[0] for r in results)
    assert runs.get(original['id'])==before
    assert checks.pending()==[]
    assert waiting['id'] in {r['id'] for r in runs.pending()}
    assert runs.claim(waiting['id'])
    store.save_agent({'enabled':False},p['agent_id'])
    assert ModelReconciliations(Store(tmp_path)).save(original['id'],declaration())==results[0]
    with pytest.raises(ValueError): checks.save(original['id'],{**declaration(),'note':'different'})
    with store.connect() as db:
        for sql in ('UPDATE model_run_reconciliations SET note=note','DELETE FROM model_run_reconciliations','INSERT OR REPLACE INTO model_run_reconciliations SELECT * FROM model_run_reconciliations'):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)


@pytest.mark.parametrize('patch',[{'attempt':True},{'requirement_version':0},{'local_request_stopped':1},{'provider_effects_checked':False},{'note':''},{'note':'x'*2001},{'extra':True}])
def test_strict_declarations(tmp_path,patch):
    store,runs,cid,p=setup(tmp_path);run=runs.create(cid,p);runs.fail(run['id'],'unknown',state='unknown')
    checks=ModelReconciliations(store)
    with pytest.raises(ValueError):checks.save(run['id'],{**declaration(),**patch})
    assert checks.get(run['id']) is None


def test_only_unknown_and_version_and_limits(tmp_path):
    store,runs,cid,p=setup(tmp_path);run=runs.create(cid,p);checks=ModelReconciliations(store)
    with pytest.raises(ValueError):checks.save(run['id'],declaration())
    runs.fail(run['id'],'unknown',state='unknown')
    with pytest.raises(ValueError):checks.save(run['id'],{**declaration(),'attempt':2})
    for limit in (True,0,101):
        with pytest.raises(ValueError):checks.pending(limit)
    with pytest.raises(KeyError):checks.get('missing')


def test_planning_gate_kind_and_new_source_independent(tmp_path):
    store,planning,cid,p,_=planning_setup(tmp_path)
    first=planning.create(cid,p)
    queued=planning.create(cid,{**p,'request_id':'queued'})
    planning.runs.fail(first['id'],'unknown',state='unknown')
    with pytest.raises(ValueError):planning.create(cid,{**p,'request_id':'new'})
    assert not planning.runs.claim(queued['id'])
    ordinary=planning.runs.create(cid,{k:v for k,v in {**p,'request_id':'ordinary'}.items() if k!='candidate_ids'})
    assert planning.runs.claim(ordinary['id'])
    source=store.send_message(cid,{'content':'Other source','request_id':'other-source'})
    assert planning.create(cid,{**p,'source_message_id':source['id'],'request_id':'new-source'})
    checks=ModelReconciliations(store)
    assert checks.pending()[0]['kind']=='planning'
    checks.save(first['id'],declaration())
    assert planning.runs.claim(queued['id'])


def test_retrospective_scope_privacy_and_shared_claim_gate(tmp_path):
    store,_,task,_,_,retro,p=retro_setup(tmp_path)
    first=retro.create('agent',task['agent_id'],p)
    queued=retro.create('agent',task['agent_id'],{**p,'request_id':'queued'})
    retro.runs.fail(first['id'],'unknown',state='unknown')
    with pytest.raises(ValueError):retro.create('agent',task['agent_id'],{**p,'request_id':'new'})
    assert not retro.runs.claim(queued['id'])
    project=retro.create('project',task['conversation_id'],{**p,'request_id':'project'})
    assert retro.runs.claim(project['id'])
    checks=ModelReconciliations(store)
    pending=checks.pending()[0]
    assert (pending['kind'],pending['scope'],pending['scope_id'])==('retrospective','agent',task['agent_id'])
    assert 'input_snapshot' not in pending and 'verified output' not in str(pending)
    checks.save(first['id'],declaration())
    assert retro.runs.claim(queued['id'])
    assert retro.get(first['id'])['state']=='unknown'
    assert retro.get(first['id'])['candidate_id'] is None


def test_atomic_declaration_insert_failure(tmp_path):
    store,runs,cid,p=setup(tmp_path);run=runs.create(cid,p);runs.fail(run['id'],'unknown',state='unknown')
    checks=ModelReconciliations(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON model_run_reconciliations BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):checks.save(run['id'],declaration())
    assert checks.get(run['id']) is None
    with pytest.raises(ValueError):runs.create(cid,{**p,'request_id':'new'})

def test_retrospectives_same_brief_different_executions_do_not_interlock(tmp_path):
    from workbench.tasks import Tasks
    from workbench.executions import Executions
    from workbench.reviews import Reviews
    from workbench.artifacts import list_for
    store,_,task,_,_,retro,p=retro_setup(tmp_path)
    first=retro.create('project',task['conversation_id'],p)
    retro.runs.fail(first['id'],'unknown',state='unknown')
    second_task=Tasks(store).create(task['conversation_id'],dict(source_message_id=task['source_message_id'],request_id='sibling-task',title='Sibling',scope='Different task',acceptance='Reviewed',agent_id=task['agent_id']))
    executions=Executions(store)
    second=executions.create(second_task['id'],dict(request_id='sibling-execution',expected_version=1,previous_execution_id=None,reconciliation_note=''))
    assert executions.claim(second['id'])
    executions.report(second['id'],1,1,0,'Verified',success=True,artifacts=[{'path':'evidence.txt','data':b'sibling evidence'}])
    ids=sorted(a['id'] for a in list_for(store,second['id']))
    Reviews(store).save(second['id'],dict(request_id='sibling-review',expected_version=1,decision='approved',note='Inspected',artifact_ids=ids))
    different=retro.create('project',task['conversation_id'],{**p,'request_id':'sibling-retro','source_execution_id':second['id'],'artifact_ids':ids})
    assert retro.runs.claim(different['id'])
