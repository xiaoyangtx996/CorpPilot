"""Task checkpoints preserve approved evidence and require new explicit authority."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_workbench_project_executions import fixture
from workbench.checkpoints import Checkpoints
from workbench.reviews import Reviews
from workbench.reconciliations import Reconciliations
from workbench import artifacts


def ready(tmp_path, external=False):
    store,_,plan,batches,p = fixture(tmp_path)
    if external:
        upstream = batches.create(plan['id'],{'request_id':'upstream','tasks':p['tasks'][:1]})
        source = batches.create(plan['id'],{'request_id':'source','tasks':p['tasks'][1:]})
        first = upstream['tasks'][0]['execution_id']
        second = source['tasks'][0]['execution_id']
    else:
        source = batches.create(plan['id'],p)
        first,second = [row['execution_id'] for row in source['tasks']]
    assert batches.executions.claim(first)
    batches.executions.report(first,1,1,0,'approved checkpoint',success=True,artifacts=[{'path':'proof.txt','data':b'proof'}])
    Reviews(store).save(first,{'request_id':'approve','expected_version':1,'decision':'approved','note':'checked',
                             'artifact_ids':sorted(row['id'] for row in artifacts.list_for(store,first))})
    assert batches.executions.claim(second)
    batches.executions.report(second,1,1,1,'failed')
    return store,batches,Checkpoints(store,batches),source,first,second


def consent(api,source,key='recover'):
    preview = api.preview(source['id'])
    assert preview['blockers'] == []
    return {'request_id':key,'checkpoint_fingerprint':preview['fingerprint'],'reconciliation_note':'Original stopped and effects checked','confirm':True}


@pytest.mark.parametrize('external',[False,True])
def test_approved_checkpoint_reused_no_writes_preview_and_one_retry(tmp_path,external):
    store,batches,api,source,first,second = ready(tmp_path,external)
    with store.connect() as db: before=list(db.iterdump())
    p = consent(api,source)
    assert api.preview(source['id']) == api.preview(source['id'])
    with store.connect() as db: assert list(db.iterdump()) == before
    receipt = api.recover(source['id'],p)
    new = receipt['batch']['tasks'][0]['execution_id']
    assert len(receipt['batch']['tasks']) == 1
    assert new != second
    assert batches.executions.claim(new)
    with store.connect() as db:
        assert db.execute('SELECT upstream_execution_id FROM execution_inputs WHERE execution_id=?',(new,)).fetchone()[0] == first
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 3
    assert api.recover(source['id'],p) == receipt
    assert api.list(source['id']) == [receipt]
    assert Checkpoints(store,batches).get(receipt['id'])['batch']['id'] == receipt['batch_id']
    batches.stop(receipt['batch_id'])
    assert batches.executions.get(new)['state'] == 'stopping'
    assert batches.executions.get(first)['state'] == 'awaiting_review'


def test_concurrent_recovery_exact_replay_and_atomic_rollback(tmp_path):
    store,batches,api,source,_,_ = ready(tmp_path)
    p=consent(api,source)
    with store.connect() as db:
        db.execute("CREATE TRIGGER inject BEFORE INSERT ON checkpoint_dependency_pins BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError): api.recover(source['id'],p)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM checkpoint_recoveries').fetchone()[0] == 0
        db.execute('DROP TRIGGER inject')
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:api.recover(source['id'],p),range(3)))
    assert results[0] == results[1] == results[2]
    with pytest.raises(ValueError): api.recover(source['id'],{**p,'reconciliation_note':'different'})
    assert api.recover(source['id'],p) == results[0]


def test_replacement_approved_input_does_not_rebind_pin(tmp_path):
    store,batches,api,source,first,_=ready(tmp_path)
    recovered=api.recover(source['id'],consent(api,source))
    first_task=batches.executions.get(first)['task_id']
    replacement=batches.executions.create(first_task,{'request_id':'replacement','expected_version':1,'previous_execution_id':first,'reconciliation_note':'explicit separate rerun'})
    assert batches.executions.claim(replacement['id'])
    batches.executions.report(replacement['id'],2,1,0,'new output',success=True,artifacts=[{'path':'new.txt','data':b'new'}])
    Reviews(store).save(replacement['id'],{'request_id':'new-review','expected_version':1,'decision':'approved','note':'checked','artifact_ids':[row['id'] for row in artifacts.list_for(store,replacement['id'])]})
    new=recovered['batch']['tasks'][0]['execution_id']
    assert not batches.executions.claim(new)
    assert '检查点' in api.get(recovered['id'])['batch']['items'][0]['dependencies']['blocked_reason']


@pytest.mark.parametrize('state',['running','queued','unknown','awaiting_review'])
def test_unreviewed_active_unknown_blocked_and_checked_unknown_allowed(tmp_path,state):
    store,batches,api,source,_,second=ready(tmp_path)
    with store.connect() as db: db.execute('UPDATE task_executions SET state=? WHERE id=?',(state,second))
    assert api.preview(source['id'])['blockers']
    if state=='unknown':
        Reconciliations(store).save(second,{'request_id':'checked','attempt':1,'requirement_version':1,'process_stopped':True,'external_effects_checked':True,'note':'verified outside effects'})
        recovered=api.recover(source['id'],consent(api,source))
        assert batches.executions.get(second)['state']=='unknown'
        assert batches.executions.get(recovered['batch']['tasks'][0]['execution_id'])['state']=='queued'


def test_stale_fingerprint_and_revised_source_do_not_create(tmp_path):
    store,batches,api,source,_,second=ready(tmp_path)
    p=consent(api,source)
    with store.connect() as db: db.execute("UPDATE task_executions SET state='cancelled' WHERE id=?",(second,))
    with pytest.raises(ValueError,match='已变化'): api.recover(source['id'],p)
    task=batches.executions.tasks.get(batches.executions.get(second)['task_id'])
    batches.executions.tasks.revise(task['id'],{'expected_version':1,**{key:task[key] for key in ('title','scope','acceptance','agent_id')},'scope':'new scope'})
    assert api.preview(source['id'])['blockers']
    with store.connect() as db: assert db.execute('SELECT count(*) FROM checkpoint_recoveries').fetchone()[0]==0


def test_retry_chain_requires_new_owner_approval_and_pins_are_immutable(tmp_path):
    store,_,plan,batches,p=fixture(tmp_path)
    source=batches.create(plan['id'],p)
    first,second=[row['execution_id'] for row in source['tasks']]
    batches.executions.claim(first)
    batches.executions.report(first,1,1,1,'failed')
    batches.executions.cancel(second)
    api=Checkpoints(store,batches)
    receipt=api.recover(source['id'],consent(api,source))
    mapping={row['task_id']:row['execution_id'] for row in receipt['batch']['tasks']}
    a=mapping[source['tasks'][0]['task_id']]
    b=mapping[source['tasks'][1]['task_id']]
    assert batches.executions.claim(a)
    assert not batches.executions.claim(b)
    batches.executions.report(a,2,1,0,'done',success=True,artifacts=[{'path':'new.txt','data':b'new'}])
    assert not batches.executions.claim(b)
    Reviews(store).save(a,{'request_id':'approve-new','expected_version':1,'decision':'approved','note':'checked','artifact_ids':[row['id'] for row in artifacts.list_for(store,a)]})
    assert batches.executions.claim(b)
    with store.connect() as db:
        for table in ('checkpoint_recoveries','checkpoint_dependency_pins'):
            for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET rowid=rowid',f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}'):
                with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_owner_rejected_result_can_be_retried_without_rewriting_review(tmp_path):
    store,batches,api,source,_,second=ready(tmp_path)
    with store.connect() as db: db.execute("UPDATE task_executions SET state='awaiting_review' WHERE id=?",(second,))
    Reviews(store).save(second,{'request_id':'reject','expected_version':1,'decision':'rejected','note':'not acceptable','artifact_ids':[]})
    receipt=api.recover(source['id'],consent(api,source))
    assert len(receipt['batch']['tasks'])==1
    assert Reviews(store).get(second)['decision']=='rejected'


def test_changed_bound_input_blocks_checkpoint(tmp_path):
    store,_,api,source,_,second=ready(tmp_path)
    with store.connect() as db:
        db.execute('DROP TRIGGER inputs_no_update')
        db.execute('UPDATE execution_inputs SET upstream_execution_id=? WHERE execution_id=?',(second,second))
    assert any('前置成果已变化' in text for text in api.preview(source['id'])['blockers'])


@pytest.mark.parametrize('change_after_recovery',[False,True])
def test_deep_external_ancestor_revision_blocks_recovery_or_claim(tmp_path,change_after_recovery):
    store,_,plan,batches,p=fixture(tmp_path)
    tasks=batches.executions.tasks
    a_task=tasks.get(p['tasks'][0]['task_id'])
    root=tasks.create(a_task['conversation_id'],{**{k:a_task[k] for k in ('source_message_id','title','scope','acceptance','agent_id')},'request_id':'root'})
    a=tasks.set_dependencies(a_task['id'],{'expected_version':1,'task_ids':[root['id']]})
    root_run=batches.executions.create(root['id'],{'request_id':'root-exec','expected_version':1,'previous_execution_id':None,'reconciliation_note':''})
    def approve(run_id,version):
        assert batches.executions.claim(run_id)
        batches.executions.report(run_id,1,version,0,'proof',success=True,artifacts=[{'path':'proof.txt','data':b'proof'}])
        Reviews(store).save(run_id,{'request_id':'review-'+run_id,'expected_version':version,'decision':'approved','note':'checked','artifact_ids':[r['id'] for r in artifacts.list_for(store,run_id)]})
    approve(root_run['id'],1)
    upstream=batches.create(plan['id'],{'request_id':'upstream','tasks':[{**p['tasks'][0],'expected_version':2}]})
    approve(upstream['tasks'][0]['execution_id'],2)
    source=batches.create(plan['id'],{'request_id':'source','tasks':p['tasks'][1:]})
    b=source['tasks'][0]['execution_id']
    assert batches.executions.claim(b)
    batches.executions.report(b,1,1,1,'failed')
    api=Checkpoints(store,batches)
    consent_payload=consent(api,source)
    assert len(api.preview(source['id'])['snapshot']['nodes'])==3
    receipt=api.recover(source['id'],consent_payload) if change_after_recovery else None
    tasks.revise(root['id'],{'expected_version':1,**{k:root[k] for k in ('title','scope','acceptance','agent_id')},'scope':'changed root'})
    if receipt:
        assert not batches.executions.claim(receipt['batch']['tasks'][0]['execution_id'])
    else:
        with pytest.raises(ValueError): api.recover(source['id'],consent_payload)
        assert api.preview(source['id'])['blockers']
