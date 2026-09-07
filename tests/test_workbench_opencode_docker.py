"""OpenCode Docker protocol/lifecycle contracts; these are not real container tests."""
import json
from pathlib import Path
import sys
import uuid

import pytest

from test_workbench_docker_worker import run
from test_workbench_cli_controller import fixture, until
from workbench import cli_controller, cli_settings, docker_worker as worker
from workbench.cli_settings import CLISettings
from workbench.store import Store


@pytest.mark.parametrize('scenario', ['success','create_lost','cancel','timeout','kill','inspect_unknown',
                                    'remove_failed','foreign','wrong_image','paused','client_early','exception_start'])
def test_native_docker_result_keeps_original_container_lifecycle(tmp_path, monkeypatch, scenario):
    receipt,calls = run(tmp_path,monkeypatch,scenario,engine='opencode')
    commands = [a[a.index('container')+1] for a,_,_ in calls if 'container' in a]
    assert commands.count('create')==1 and commands.count('start')<=1
    assert all('TEST_SECRET' not in str(argv)+str(env) for argv,env,_ in calls)
    record = json.loads(Path(receipt['worker_record']).read_text('utf-8'))
    assert record['engine']=='opencode' and 'TEST_SECRET' not in json.dumps(record)
    assert worker._read_record(Path(receipt['worker_record']))['engine']=='opencode'
    if scenario in ('success','create_lost','remove_failed'):
        assert receipt['success'] and receipt['exit_code']==0
        assert receipt['tool_activities']['source']=='opencode_jsonl'
        assert receipt['usage']['input_tokens']==1918
    elif scenario in ('inspect_unknown','foreign','wrong_image','paused'):
        assert not receipt['success'] and receipt['reason']=='unknown' and receipt['exit_code'] is None
        assert 'rm' not in commands
    else:
        assert not receipt['success'] and receipt['exit_code']==137 and 'stop' in commands
        if scenario=='kill': assert 'kill' in commands


@pytest.mark.parametrize('label', [None,'0.153.3',True,{},'1.18.30'])
def test_incompatible_image_does_not_create_container(tmp_path,monkeypatch,label):
    with pytest.raises(worker.InputPreparationError,match='CLI 能力'):
        run(tmp_path,monkeypatch,engine='opencode',label=label)
    record = next((tmp_path/'execution-workspaces').glob('*/docker-worker.json'))
    assert json.loads(record.read_text())['phase']=='create_intent'


def test_codex_output_never_satisfies_opencode_protocol(tmp_path,monkeypatch):
    receipt,_ = run(tmp_path,monkeypatch,engine='opencode',stdout_override=b'{"type":"turn.completed"}\n')
    assert not receipt['success'] and receipt['reason']=='protocol_error' and receipt['exit_code']==0


def test_invalid_engine_or_zen_model_never_reaches_docker(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'_command',lambda *a,**kw: pytest.fail('invalid configuration invoked daemon'))
    for engine,model in [('other','model'),('opencode','other/model')]:
        with pytest.raises(ValueError):
            worker.run_docker(sys.executable,tmp_path,str(uuid.uuid4()),'task',model,'key',2,
                              image='sha256:'+'b'*64,engine=engine)
    assert not (tmp_path/'execution-workspaces').exists()


@pytest.mark.parametrize('label', ['1.18.29',None,'wrong',True])
def test_settings_enable_and_probe_checks_engine_image_contract(tmp_path,monkeypatch,label):
    settings=CLISettings(Store(tmp_path/'data'))
    config=settings.save({'engine':'opencode','backend':'docker','docker_executable':sys.executable,
                          'docker_image':'sha256:'+'b'*64,'model':'opencode/big-pickle','api_key_env':'F93_KEY','enabled':True})
    assert config['configured'] and CLISettings(settings.store).get()==config
    calls=[]
    def probe(argv,cwd,env,stdin,timeout,**kwargs):
        calls.append(argv)
        assert stdin==b'' and not any(value=='HOST_SECRET' for value in env.values())
        assert not any(x in argv for x in ('create','start','run','pull','build'))
        data={'ServerVersion':'29.7.2','OSType':'linux'} if 'info' in argv else {'Id':'sha256:'+'b'*64,'Os':'linux','Labels':{worker.OPENCODE_LABEL:label}}
        return {'reason':'exited','exit_code':0,'stdout':json.dumps(data).encode(),'stderr':b''}
    monkeypatch.setenv('F93_KEY','HOST_SECRET')
    monkeypatch.setattr(cli_settings,'run_process',probe)
    assert settings.resolve()['engine']=='opencode'
    assert settings.probe()['available']==(label=='1.18.29')
    assert len(calls)==2


def test_controller_passes_engine_to_docker_without_local_fallback(tmp_path,monkeypatch):
    from test_workbench_opencode_cli import step, process
    from test_workbench_opencode_integration import native_event
    from workbench.tool_activities import ToolActivities
    _,_,controller,_,runs=fixture(tmp_path,monkeypatch)
    config=controller.settings.resolve()
    monkeypatch.setattr(controller.settings,'resolve',lambda:{**config,'engine':'opencode','backend':'docker',
                        'model':'opencode/big-pickle','docker_image':'sha256:'+'b'*64,'docker_executable':sys.executable,
                        'docker_cpus':1,'docker_memory_mb':1024,'docker_pids_limit':128})
    calls=[]
    monkeypatch.setattr(cli_controller,'run_codex',lambda **kw:pytest.fail('local fallback'))
    monkeypatch.setattr(cli_controller,'run_opencode',lambda **kw:pytest.fail('local fallback'))
    inspections=0
    def daemon(executable,paths,args,**kwargs):
        nonlocal inspections
        calls.append(args)
        receipt={'reason':'exited','exit_code':0,'stdout':b'','stderr':b''}
        if args[:2]==['image','inspect']:
            return {**receipt,'stdout':json.dumps({'Id':'sha256:'+'b'*64,'Os':'linux','Config':{'Labels':{worker.OPENCODE_LABEL:worker.OPENCODE_VERSION}}}).encode()}
        if args[1]=='inspect':
            inspections+=1
            record=json.loads((paths['root']/'docker-worker.json').read_text())
            return {**receipt,'stdout':json.dumps([{'Id':'a'*64,'Image':'sha256:'+'b'*64,'Config':{'Labels':{worker.RUN_LABEL:runs[0]['id'],worker.TOKEN_LABEL:record['token']}},'State':{'Running':False,'Paused':False,'Restarting':False,'ExitCode':0,'Status':'created' if inspections==1 else 'exited'}}]).encode()}
        if args[1]=='start':
            payload=json.loads(kwargs['stdin'])
            assert payload['engine']=='opencode' and payload['model']=='opencode/big-pickle'
            events=step()
            tool=native_event(session='session-one'); tool['part']['messageID']='message-1'
            events.insert(1,tool)
            (paths['work']/'artifacts').mkdir()
            (paths['work']/'artifacts/proof.txt').write_text('Controlled Docker command evidence')
            return process(events)
        return receipt
    monkeypatch.setattr(worker,'_command',daemon)
    try:
        until(controller,lambda:controller.executions.get(runs[0]['id'])['state']=='awaiting_review')
        assert sum(args[:2]==['container','start'] for args in calls)==1
        tools=ToolActivities(controller.store).get(runs[0]['id'])
        assert tools['payload']['source']=='opencode_jsonl' and len(tools['payload']['events'])==1
        assert controller.executions.get(runs[0]['id'])['usage']['input_tokens']==1918
        with controller.store.connect() as db:
            assert db.execute('SELECT backend FROM execution_backends WHERE execution_id=?',(runs[0]['id'],)).fetchone()[0]=='docker'
    finally:
        controller.close()
