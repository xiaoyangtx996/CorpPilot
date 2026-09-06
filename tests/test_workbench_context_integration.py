"""Context evidence is saved before execution, never reconstructed by Owner reads."""
import hashlib
import json
import sqlite3
import pytest
from test_workbench_api import running, request
from test_workbench_budget_integration import make_run
from test_workbench_controller import configure, until
from test_workbench_provider import endpoint
from test_workbench_cli_controller import fixture, result, until as cli_until
from workbench import controller as model_module, cli_controller
from workbench.controller import ReplyController
from workbench.context_receipts import ContextReceipts
from workbench.settings import Settings
from workbench.store import Store
from workbench.runs import Runs
from workbench.executions import Executions
from workbench.backup import backup, restore
from test_workbench_executions import setup, request as execution_request


def test_actual_local_model_input_receipt_survives_config_and_restart(tmp_path, monkeypatch):
    output={'choices':[{'finish_reason':'stop','message':{'content':'Local verified result'}}]}
    with endpoint(output) as (config, calls):
        store=Store(tmp_path); settings=Settings(store); configure(settings,config,monkeypatch)
        c=ReplyController(store,settings)
        try:
            run=make_run(store,c.runs)
            until(lambda:c.runs.get(run['id'])['state']=='completed')
            receipt=c.contexts.get('model',run['id'])
            assert receipt['phase']=='prepared' and receipt['run_id']==run['id']
            assert receipt['agent_id']==run['agent_id'] and receipt['attempt']==1
            assert len(calls)==1
            assert receipt['messages'][0]['sha256']==hashlib.sha256(b'Local budget test').hexdigest()
            assert receipt['instructions']['sha256']==hashlib.sha256(calls[0][2]['messages'][0]['content'].encode()).hexdigest()
            serialized=json.dumps(receipt,ensure_ascii=False)
            assert 'Local budget test' not in serialized and config['api_key'] not in serialized
            store.save_agent({'name':'Changed after input','enabled':False},run['agent_id'])
            assert c.contexts.get('model',run['id'])==receipt
        finally:c.close()
    assert ContextReceipts(Store(tmp_path)).get('model',run['id'])==receipt


def test_model_receipt_failure_prevents_external_call(tmp_path, monkeypatch):
    store=Store(tmp_path); c=ReplyController(store,Settings(store))
    try:
        run=make_run(store,c.runs); assert c.runs.claim(run['id'])
        monkeypatch.setattr(c.contexts,'record_model',lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('write unavailable')))
        monkeypatch.setattr(model_module,'run_reply',lambda *a: pytest.fail('Provider must not run before receipt is durable'))
        c._execute(run['id'],{'model':'fixture','api_key':'fixture-secret'})
        assert c.runs.get(run['id'])['state']=='failed'
        assert c.contexts.get('model',run['id']) is None
    finally:c.close()


@pytest.mark.parametrize('backend',['local','docker'])
def test_cli_receipt_exists_before_runner_and_does_not_store_workdir(tmp_path,monkeypatch,backend):
    store,_,c,_,runs=fixture(tmp_path,monkeypatch)
    config=c.settings.resolve()
    if backend=='docker':
        config={**config,'backend':'docker','docker_executable':'C:/Docker/docker.exe','docker_image':'sha256:'+'a'*64,'docker_cpus':1,'docker_memory_mb':1024,'docker_pids_limit':32}
        monkeypatch.setattr(c.settings,'resolve',lambda:config)
    seen=[]
    def runner(**kwargs):
        receipt=c.contexts.get('cli',kwargs['execution_id'])
        assert receipt['phase']=='prepared' and receipt['backend']==backend
        assert receipt['model']==kwargs['model'] and receipt['task']['id']==runs[0]['task_id']
        assert 'not-a-real-key' not in json.dumps(receipt) and 'C:/' not in json.dumps(receipt)
        seen.append(receipt); return result()
    monkeypatch.setattr(cli_controller,'run_codex' if backend=='local' else 'run_docker',runner)
    monkeypatch.setattr(cli_controller,'capture',lambda *a:[])
    try:
        cli_until(c,lambda:c.executions.get(runs[0]['id'])['state']=='awaiting_review')
        assert len(seen)==1 and c.contexts.get('cli',runs[0]['id'])==seen[0]
    finally:c.close()


def test_cli_receipt_failure_never_invokes_tool(tmp_path,monkeypatch):
    store,_,c,_,runs=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(c.contexts,'record_cli',lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('write unavailable')))
    monkeypatch.setattr(cli_controller,'run_codex',lambda **kw:pytest.fail('Tool must not run'))
    try:
        cli_until(c,lambda:c.executions.get(runs[0]['id'])['state']=='failed')
        assert c.contexts.get('cli',runs[0]['id']) is None
        with store.connect() as db: assert db.execute('select count(*) from execution_backends').fetchone()[0]==0
    finally:c.close()


def test_owner_context_get_is_readonly_never_calls_snapshot(tmp_path,monkeypatch):
    with running(tmp_path) as port:
        store=Store(tmp_path); runs=Runs(store); contexts=ContextReceipts(store)
        run=make_run(store,runs); path='/api/workbench/runs/'+run['id']+'/context-summary'
        assert request(port,'GET',path)==(200,None)
        assert request(port,'GET',path,headers={'Authorization':''})[0]==401
        assert request(port,'POST',path,{})[0]==404
        assert request(port,'GET','/api/workbench/runs/missing/context-summary')[0]==404
        assert runs.claim(run['id']); contexts.record_model(run['id'],runs.snapshot(run['id']),'fixture')
        runs.fail(run['id'],'Original fixture finished'); receipt=contexts.get('model',run['id'])
        _,_,executions,task=setup(tmp_path)
        cli_run=executions.create(task['id'],execution_request())
        cli_path='/api/workbench/executions/'+cli_run['id']+'/context-summary'
        assert request(port,'GET',cli_path)==(200,None)
        assert request(port,'GET',cli_path,headers={'Authorization':''})[0]==401
        assert executions.claim(cli_run['id'])
        cli_receipt=contexts.record_cli(cli_run['id'],executions.snapshot(cli_run['id'],include_artifacts=True),'fixture','local')
        executions.report(cli_run['id'],1,1,1,'Ended fixture')
        monkeypatch.setattr(Runs,'snapshot',lambda *a,**kw:pytest.fail('Owner GET reconstructed model input'))
        monkeypatch.setattr(Executions,'snapshot',lambda *a,**kw:pytest.fail('Owner GET reconstructed CLI input'))
        with store.connect() as db: before='\n'.join(db.iterdump())
        for _ in range(3):
            assert request(port,'GET',path)==(200,receipt)
            assert request(port,'GET',cli_path)==(200,cli_receipt)
        with store.connect() as db: assert '\n'.join(db.iterdump())==before
    with running(tmp_path) as port: assert request(port,'GET',path)==(200,receipt)


def test_real_backup_restore_preserves_context_receipt(tmp_path):
    source=tmp_path/'source'; store=Store(source); c=ReplyController(store,Settings(store))
    try:
        run=make_run(store,c.runs); assert c.runs.claim(run['id'])
        receipt=c.contexts.record_model(run['id'],c.runs.snapshot(run['id']),'fixture')
        c.runs.fail(run['id'],'Ended')
    finally:c.close()
    backup(source,tmp_path/'bundle'); restore(tmp_path/'bundle',tmp_path/'restored')
    assert ContextReceipts(Store(tmp_path/'restored')).get('model',run['id'])==receipt
