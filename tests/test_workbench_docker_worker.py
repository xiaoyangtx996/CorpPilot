import json
from pathlib import Path
import sys
import threading
import uuid
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from workbench import docker_worker as worker


def run(tmp_path,monkeypatch,scenario='success'):
    calls=[]; identity=str(uuid.uuid4()); token=None; container='a'*64; inspections=0
    stdout=b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n{"type":"turn.completed"}\n'
    def fake(argv,cwd,env,stdin,timeout,cancel,output_limit_bytes):
        nonlocal token,inspections
        calls.append((argv,env,stdin))
        assert '--host' in argv and worker.HOST in argv
        assert 'DOCKER_HOST' not in env and 'DOCKER_CONTEXT' not in env
        if 'image' in argv: return dict(reason='exited',exit_code=0,stdout=json.dumps({'Id':'sha256:'+'b'*64,'Os':'linux'}).encode(),stderr=b'')
        command=argv[argv.index('container')+1]
        result=dict(reason='exited',exit_code=0,stdout=b'',stderr=b'')
        if command=='create':
            token=next(v.split('=',1)[1] for v in argv if v.startswith(worker.TOKEN_LABEL+'='))
            record=json.loads((cwd/'docker-worker.json').read_text())
            assert record['phase']=='create_intent'
            if scenario=='create_lost': result.update(reason='timeout',exit_code=1)
        if command=='inspect':
            inspections+=1
            if scenario=='inspect_unknown' and inspections>=2: return dict(result,exit_code=1)
            running=scenario in ('cancel','timeout','kill','client_early','exception_start') and inspections in ((2,3) if scenario=='kill' else (2,))
            state='running' if running else 'created' if inspections==1 else 'exited'
            labels={worker.RUN_LABEL:identity,worker.TOKEN_LABEL:token}
            if scenario=='foreign': labels[worker.TOKEN_LABEL]='other'
            result['stdout']=json.dumps([{'Id':container,'Config':{'Labels':labels},'Image':'sha256:'+('c' if scenario=='wrong_image' else 'b')*64,'State':{'Paused':scenario=='paused','Restarting':False,'Running':running,'ExitCode':0 if inspections==1 or running or scenario in ('success','create_lost','remove_failed') else 137,'Status':state}}]).encode()
        if command=='start':
            if scenario=='exception_start': raise OSError('lost client')
            assert json.loads(stdin)=={'prompt':'task','model':'model','api_key':'TEST_SECRET'}
            result['stdout']=stdout
            if scenario in ('cancel','timeout','kill'): result.update(reason='cancelled' if scenario=='cancel' else 'timeout',exit_code=1)
        if command=='rm' and scenario=='remove_failed': result['exit_code']=1
        return result
    monkeypatch.setenv('DOCKER_HOST','tcp://remote.invalid:2375')
    monkeypatch.setattr(worker,'run_process',fake)
    result=worker.run_docker(sys.executable,tmp_path,identity,'task','model','TEST_SECRET',2,image='sha256:'+'b'*64)
    return result,calls


def test_success_mounts_limits_and_stdin_secrets(tmp_path,monkeypatch):
    result,calls=run(tmp_path,monkeypatch)
    assert result['success'] and result['exit_code']==0 and result['cleanup']=='removed'
    argv=next(a for a,_,_ in calls if 'create' in a)
    for flag in ['--read-only','--cap-drop','--cpus','--memory','--memory-swap','--pids-limit','--user','--security-opt','--init']:
        assert flag in argv
    assert argv[argv.index('--user')+1]=='1000:1000'
    mounts=[argv[i+1] for i,v in enumerate(argv) if v=='--mount']
    assert len(mounts)==2 and mounts[1].endswith('target=/work/inputs,readonly')
    assert all('docker.sock' not in v for v in argv)
    assert all('TEST_SECRET' not in str(a)+str(e) for a,e,_ in calls)
    assert 'TEST_SECRET' not in Path(result['worker_record']).read_text()


@pytest.mark.parametrize('scenario',['create_lost','cancel','timeout','kill','inspect_unknown','remove_failed','foreign','wrong_image','paused','client_early','exception_start'])
def test_lifecycle_faults(tmp_path,monkeypatch,scenario):
    result,calls=run(tmp_path,monkeypatch,scenario)
    commands=[a[a.index('container')+1] for a,_,_ in calls if 'container' in a]
    assert commands.count('create')==1 and commands.count('start')<=1
    if scenario=='create_lost': assert result['success']
    elif scenario=='remove_failed': assert result['success'] and result['cleanup']=='remove_failed' and '清理失败' in result['summary']
    elif scenario in ('inspect_unknown','foreign','wrong_image','paused'):
        assert result['exit_code'] is None and result['reason']=='unknown' and 'rm' not in commands
        if scenario in ('foreign','wrong_image','paused'): assert 'start' not in commands and 'stop' not in commands
    else:
        assert not result['success'] and result['exit_code']==137 and 'stop' in commands
        if scenario=='kill': assert 'kill' in commands
        if scenario in ('client_early','exception_start'): assert result['reason']=='unknown'


def test_precancel_and_invalid_image_do_not_create(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'run_process',lambda *a,**kw:pytest.fail('must not invoke Docker'))
    event=threading.Event();event.set()
    result=worker.run_docker(sys.executable,tmp_path,str(uuid.uuid4()),'task','model','secret',2,cancel=event,image='sha256:'+'b'*64)
    assert result['workspace'] is None
    with pytest.raises(ValueError):worker.run_docker(sys.executable,tmp_path,str(uuid.uuid4()),'task','model','secret',2,image='latest')


def test_entrypoint_large_unicode_private_stdin(monkeypatch):
    import importlib.util
    import io
    from types import SimpleNamespace
    entry=Path(__file__).resolve().parents[1]/'docker/worker/entrypoint.py'
    spec=importlib.util.spec_from_file_location('worker_entry',entry); module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    request={'prompt':'😀'*64000,'model':'model','api_key':'TEST_SECRET'}
    raw=json.dumps(request,ensure_ascii=False).encode()
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(module.os,'makedirs',lambda *a,**k:None)
    def fake(argv,**kwargs):
        assert argv[argv.index('--sandbox')+1]=='danger-full-access'
        assert 'approval_policy="never"' in argv
        assert 'TEST_SECRET' not in str(argv)
        assert kwargs['input']==request['prompt'].encode()
        assert kwargs['env']['CODEX_API_KEY']=='TEST_SECRET'
        assert not any('shell_environment_policy.set.CODEX_API_KEY' in a for a in argv)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess,'run',fake)
    assert module.main()==0
    local=(entry.parents[2]/'scripts/workbench/cli.py').read_text(encoding='utf-8-sig')
    assert 'danger-full-access' not in local


@pytest.mark.parametrize('mismatch',[False,True])
def test_recovery_stop_only_verified_identity(tmp_path,monkeypatch,mismatch):
    identity=str(uuid.uuid4());token='d'*32;container='a'*64
    record={'version':1,'executable':sys.executable,'image':'sha256:'+'b'*64,'phase':'stopped','execution_id':identity,'token':token,'name':f'corppilot-{token[:12]}-{identity}','image_id':'sha256:'+'b'*64,'container_id':container}
    path=tmp_path/'execution-workspaces'/identity/'docker-worker.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(record));calls=[]
    def fake(executable,paths,args,**kwargs):
        calls.append(args)
        if args[1]=='inspect':
            running=not any(command[1]=='stop' for command in calls)
            row={'Id':container,'Image':record['image_id'],'Config':{'Labels':{worker.RUN_LABEL:identity,worker.TOKEN_LABEL:'foreign' if mismatch else token}},'State':{'Running':running,'Paused':False,'Restarting':False,'ExitCode':0,'Status':'running' if running else 'exited'}}
            return dict(reason='exited',exit_code=0,stdout=json.dumps([row]).encode())
        if args[1]=='ls': return dict(reason='exited',exit_code=0,stdout=json.dumps({'ID':container,'Names':record['name']}).encode())
        return dict(reason='exited',exit_code=0,stdout=b'')
    monkeypatch.setattr(worker,'_command',fake)
    result=worker.stop_worker(sys.executable,path)
    assert not any(args[1] in ('start','rm','create') for args in calls)
    if mismatch: assert not result['verified'] and not any(args[1]=='stop' for args in calls)
    else: assert result['verified'] and not result['state']['running']


def record_fixture(tmp_path):
    identity=str(uuid.uuid4());token='d'*32
    record={'version':1,'execution_id':identity,'token':token,'name':f'corppilot-{token[:12]}-{identity}',
            'image':'sha256:'+'b'*64,'image_id':'sha256:'+'b'*64,'executable':sys.executable,
            'container_id':'a'*64,'phase':'removed','exit_code':0}
    path=tmp_path/'execution-workspaces'/identity/'docker-worker.json'
    path.parent.mkdir(parents=True);path.write_text(json.dumps(record))
    return identity,path,record


@pytest.mark.parametrize('case',['removed','remove_response_lost','missing','corrupt','missing_nonempty','failed','malformed','oversized','still_exists','wrong_exe'])
def test_daemon_absence_proof_never_from_local_file(tmp_path,monkeypatch,case):
    identity,path,record=record_fixture(tmp_path);calls=[]
    if case=='remove_response_lost': record['phase']='stopped';path.write_text(json.dumps(record))
    if case in ('missing','missing_nonempty'): path.unlink()
    if case=='corrupt': path.write_text('{')
    def fake(executable,paths,args,**kwargs):
        calls.append(args)
        if args[1]=='inspect': return dict(reason='exited',exit_code=1,stdout=b'')
        assert args[1]=='ls'
        if case in ('missing','corrupt','missing_nonempty'):
            assert f'label={worker.RUN_LABEL}={identity}' in args
        elif '--filter' in args:
            assert f'label={worker.RUN_LABEL}={identity}' in args
        raw=b''
        if case in ('still_exists','missing_nonempty'):raw=json.dumps({'ID':record['container_id'],'Names':record['name']}).encode()
        if case=='malformed':raw=b'{"ID":"abbreviated","Names":"worker"}'
        if case=='oversized':raw=b' '*65537
        return dict(reason='exited',exit_code=1 if case=='failed' else 0,stdout=raw)
    monkeypatch.setattr(worker,'_command',fake)
    executable=str(path.parent/'other.exe') if case=='wrong_exe' else sys.executable
    result=worker.inspect_worker(executable,path,identity)
    if case in ('removed','remove_response_lost','missing','corrupt'):
        assert result['verified'] and result['state']==dict(running=False,status='absent',exit_code=None,container_id=None,absent=True)
    else: assert not result['verified']
    assert record['token'] not in str(result)
    if case=='wrong_exe':assert calls==[]
    assert all(args[1] not in ('stop','kill','start','rm','create') for args in calls)


@pytest.mark.parametrize('state',[dict(Running=False,Status='running',ExitCode=0),dict(Running=True,Status='exited',ExitCode=0),dict(Running=False,Status='exited',ExitCode=256),dict(Running=False,Status='exited',ExitCode=-1),dict(Running=False,Status='created',ExitCode=1)])
def test_inspect_rejects_inconsistent_state(tmp_path,monkeypatch,state):
    identity,path,record=record_fixture(tmp_path)
    row={'Id':record['container_id'],'Image':record['image_id'],'Config':{'Labels':{worker.RUN_LABEL:identity,worker.TOKEN_LABEL:record['token']}},'State':{**state,'Paused':False,'Restarting':False}}
    monkeypatch.setattr(worker,'_command',lambda *a,**k:dict(reason='exited',exit_code=0,stdout=json.dumps([row]).encode()))
    assert worker._inspect(sys.executable,{'root':path.parent},record) is None


@pytest.mark.parametrize('case',['different_id','windows','invalid_json','client_exception'])
def test_image_preparation_failure_never_creates_or_marks_instance_unknown(tmp_path,monkeypatch,case):
    calls=[]
    def fake(executable,paths,args,**kwargs):
        calls.append(args);assert args[:2]==['image','inspect']
        if case=='client_exception':raise OSError('injected')
        raw=json.dumps({'Id':'sha256:'+('c' if case=='different_id' else 'b')*64,'Os':'windows' if case=='windows' else 'linux'}).encode()
        if case=='invalid_json':raw=b'{'
        return dict(reason='exited',exit_code=0,stdout=raw)
    monkeypatch.setattr(worker,'_command',fake)
    with pytest.raises(worker.InputPreparationError):worker.run_docker(sys.executable,tmp_path,str(uuid.uuid4()),'task','model','secret',2,image='sha256:'+'b'*64)
    assert len(calls)==1


def test_record_path_and_identity_cannot_redirect_recovery(tmp_path,monkeypatch):
    identity,path,record=record_fixture(tmp_path)
    monkeypatch.setattr(worker,'_command',lambda *a,**k:pytest.fail('invalid path must not invoke daemon'))
    assert not worker.inspect_worker(sys.executable,path,str(uuid.uuid4()))['verified']
    assert not worker.inspect_worker(sys.executable,tmp_path/'docker-worker.json',identity)['verified']
    record['token']=3;path.write_text(json.dumps(record))
    assert not worker.inspect_worker(sys.executable,path)['verified']


def test_renamed_worker_with_lost_cid_is_not_absent(tmp_path,monkeypatch):
    identity,path,record=record_fixture(tmp_path)
    record['container_id']=None;record['phase']='create_intent';path.write_text(json.dumps(record))
    def fake(executable,paths,args,**kwargs):
        if args[1]=='inspect':return dict(reason='exited',exit_code=1,stdout=b'')
        return dict(reason='exited',exit_code=0,stdout=json.dumps({'ID':'c'*64,'Names':'renamed-worker'}).encode())
    monkeypatch.setattr(worker,'_command',fake)
    assert not worker.inspect_worker(sys.executable,path,identity)['verified']
