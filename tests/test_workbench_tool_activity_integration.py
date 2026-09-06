"""Owner observations do not execute tools or infer external side effects."""
import json
import sqlite3

import pytest

from test_workbench_api import running, request
from test_workbench_cli_controller import fixture, until
from test_workbench_executions import setup, request as execution_request
from workbench import cli_controller
from workbench.cli import parse_result
from workbench.tool_activities import ToolActivities, parse_tools
from workbench.store import Store
from workbench.executions import Executions
from workbench.controller import ReplyController
from workbench.settings import Settings
from workbench.backup import backup, restore


def observed(reason='exited', code=0):
    events = [
        {'type':'item.started','item':{'id':'tool-1','type':'command_execution',
         'command':'echo synthetic-private-input','aggregated_output':'','status':'in_progress'}},
        {'type':'item.completed','item':{'id':'tool-1','type':'command_execution',
         'command':'echo synthetic-private-input','aggregated_output':'synthetic-private-output',
         'status':'completed','exit_code':0}},
        {'type':'item.completed','item':{'type':'agent_message','text':'Done'}},
        {'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':3,'cached_input_tokens':0}},
    ]
    return {'reason':reason,'exit_code':code,'stdout':'\n'.join(json.dumps(e) for e in events).encode()}


@pytest.mark.parametrize('backend',['local','docker'])
@pytest.mark.parametrize('reason,code,state',[('exited',0,'awaiting_review'),('exited',1,'failed'),('unknown',None,'unknown')])
def test_runner_observations_durable_before_capture_and_failure(tmp_path,monkeypatch,backend,reason,code,state):
    _,_,c,_,runs=fixture(tmp_path,monkeypatch)
    if backend=='docker':
        config=c.settings.resolve()|{'backend':'docker','docker_executable':'C:/Docker/docker.exe',
            'docker_image':'sha256:'+'a'*64,'docker_cpus':1,'docker_memory_mb':1024,'docker_pids_limit':32}
        monkeypatch.setattr(c.settings,'resolve',lambda:config)
    calls=[]; captures=[]
    payload=parse_tools(observed(reason,code))
    def runner(**kwargs):
        calls.append(kwargs['execution_id'])
        return parse_result(observed(reason,code),'not-a-real-key')|{'workspace':'C:/qa/work'}
    def capture(*args):
        assert c.tool_activities.get(runs[0]['id'])['payload']==payload
        captures.append(True); return []
    monkeypatch.setattr(cli_controller,'run_codex' if backend=='local' else 'run_docker',runner)
    monkeypatch.setattr(cli_controller,'capture',capture)
    try:
        until(c,lambda:c.executions.get(runs[0]['id'])['state']==state)
        for _ in range(3):c.tick()
        receipt=c.tool_activities.get(runs[0]['id'])
        assert receipt['payload']==payload and receipt['agent_id']==runs[0]['agent_id']
        assert len(calls)==1 and len(captures)==int(state=='awaiting_review')
        assert c.executions.get(runs[0]['id'])['usage']['input_tokens']==7
        assert 'synthetic-private' not in json.dumps(receipt)
    finally:c.close()


def test_failed_receipt_write_keeps_same_future_and_never_reexecutes(tmp_path,monkeypatch):
    _,_,c,_,runs=fixture(tmp_path,monkeypatch)
    run=runs[0]; calls=[]; failed=[True]
    original=c.tool_activities.record
    def record(*args):
        if failed[0]:raise sqlite3.OperationalError('temporary write failure')
        return original(*args)
    monkeypatch.setattr(c.tool_activities,'record',record)
    def runner(**kwargs):
        calls.append(kwargs['execution_id'])
        return parse_result(observed(),'not-a-real-key')|{'workspace':'C:/qa/work'}
    monkeypatch.setattr(cli_controller,'run_codex',runner)
    monkeypatch.setattr(cli_controller,'capture',lambda *a:pytest.fail('Receipt was not durable'))
    try:
        until(c,lambda:run['id'] in c.active and c.active[run['id']][2].done())
        future=c.active[run['id']][2]
        for _ in range(4):c.tick()
        assert c.active[run['id']][2] is future and c.tool_activities.get(run['id']) is None
        assert c.executions.get(run['id'])['state']=='running'
        failed[0]=False
        until(c,lambda:run['id'] not in c.active)
        assert len(calls)==1 and c.executions.get(run['id'])['state']=='failed'
        assert c.tool_activities.get(run['id'])['payload']==parse_tools(observed())
        assert c.executions.get(run['id'])['usage']['input_tokens']==7
    finally:c.close()


def test_capture_failure_keeps_observation(tmp_path,monkeypatch):
    _,_,c,_,runs=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(cli_controller,'run_codex',lambda **kw:parse_result(observed(),'synthetic-key')|{'workspace':'C:/qa/work'})
    monkeypatch.setattr(cli_controller,'capture',lambda *a:(_ for _ in ()).throw(OSError('capture failed')))
    try:
        until(c,lambda:c.executions.get(runs[0]['id'])['state']=='failed')
        assert c.tool_activities.get(runs[0]['id'])['payload']==parse_tools(observed())
    finally:c.close()


def test_owner_read_is_authenticated_readonly_and_survives_restart(tmp_path,monkeypatch):
    with running(tmp_path) as port:
        store,_,executions,task=setup(tmp_path)
        run=executions.create(task['id'],execution_request()); activities=ToolActivities(store)
        path='/api/workbench/executions/'+run['id']+'/tool-activities'
        assert request(port,'GET',path)==(200,None)
        assert request(port,'GET',path,headers={'Authorization':''})[0]==401
        assert request(port,'POST',path,{})[0]==404
        assert request(port,'GET','/api/workbench/executions/missing/tool-activities')[0]==404
        assert executions.claim(run['id'])
        receipt=activities.record(run['id'],1,1,parse_tools(observed()))
        executions.report(run['id'],1,1,1,'Ended')
        monkeypatch.setattr(Executions,'snapshot',lambda *a,**kw:pytest.fail('GET reconstructed tool events'))
        with store.connect() as db:before='\n'.join(db.iterdump())
        for _ in range(3):assert request(port,'GET',path)==(200,receipt)
        with store.connect() as db:assert '\n'.join(db.iterdump())==before
    with running(tmp_path) as port:assert request(port,'GET',path)==(200,receipt)


def test_backup_restore_preserves_tool_observation(tmp_path):
    source=tmp_path/'source'; store=Store(source); c=ReplyController(store,Settings(store))
    try:
        _,_,executions,task=setup(source)
        run=executions.create(task['id'],execution_request()); assert executions.claim(run['id'])
        receipt=c.cli.tool_activities.record(run['id'],1,1,parse_tools(observed()))
        executions.report(run['id'],1,1,1,'Ended')
    finally:c.close()
    backup(source,tmp_path/'bundle'); restore(tmp_path/'bundle',tmp_path/'restored')
    assert ToolActivities(Store(tmp_path/'restored')).get(run['id'])==receipt
