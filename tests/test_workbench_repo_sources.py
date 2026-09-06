"""Pinned project inputs use real local Git while paid runners stay disabled."""
import copy
import json
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from test_workbench_git_checkout import repo, git
from test_workbench_api import running, request as http
from test_workbench_executions import request
from test_workbench_cli_controller import ready, until
from workbench.store import Store
from workbench.tasks import Tasks, TaskVersionConflict
from workbench.executions import Executions
from workbench.repo_sources import RepositorySources
from workbench import repo_sources, cli_controller, process_tree
from workbench.cli_controller import CLIController
from workbench.cli import prepare_workspace
from workbench import git_checkout, cli, docker_worker


def project(path):
    store=Store(path); agents=store.agents()[:2]
    for agent in agents:store.save_agent({'tools':['read','write','execute']},agent['id'])
    room=store.save_conversation({'type':'project','title':'Repository project','member_ids':[a['id'] for a in agents]})
    message=store.send_message(room['id'],{'content':'Authorized code work','request_id':'source'})
    tasks=Tasks(store); executions=Executions(store)
    task=tasks.create(room['id'],dict(agent_id=agents[0]['id'],source_message_id=message['id'],request_id='task',title='Code',scope='Change only this copy',acceptance='Review changed code'))
    return store,tasks,executions,room,agents,task


def binding(source, commit, agent, **changes):
    return dict(request_id='bind',expected_revision=0,source_path=str(source),commit=commit,integration_agent_id=agent,confirm=True)|changes


def test_binding_replay_freezes_enqueue_and_does_not_follow_project_change(tmp_path,monkeypatch):
    source,old=repo(tmp_path); store,tasks,executions,room,agents,task=project(tmp_path/'state'); service=RepositorySources(store)
    first=service.save(room['id'],binding(source,old,agents[1]['id']))
    run=executions.create(task['id'],request())
    (source/'code.txt').write_text('later source\n'); git(source,'commit','-qam','later'); new=git(source,'rev-parse','HEAD')
    second=service.save(room['id'],binding(source,new,agents[0]['id'],request_id='change',expected_revision=1))
    assert second['revision']==2 and service.execution(run['id'])['repository']==first
    assert executions.claim(run['id'])
    snapshot=executions.snapshot(run['id'],include_artifacts=True)
    assert snapshot['repository']==first
    paths=prepare_workspace(store.data_dir,run['id'],[],first['snapshot'])
    assert (paths['work']/'repository/code.txt').read_text()=='first\n'
    assert git(paths['work']/'repository','branch','--show-current')=='corppilot/run-'+run['id']
    executions.report(run['id'],1,1,1,'Ended')
    monkeypatch.setattr(repo_sources,'inspect_source',lambda *a:pytest.fail('Idempotent replay reinspected source'))
    assert service.save(room['id'],binding(source,old,agents[1]['id']))==first
    assert service.request(room['id'],'bind')==first
    with pytest.raises(ValueError):service.save(room['id'],binding(source,new,agents[1]['id']))
    with pytest.raises(TaskVersionConflict):service.save(room['id'],binding(source,new,agents[1]['id'],request_id='stale'))
    with store.connect() as db:before=list(db.iterdump())
    assert service.get(room['id'])==second and service.execution(run['id'])['repository']==first
    with store.connect() as db:assert list(db.iterdump())==before


def test_detach_affects_only_future_executions(tmp_path):
    source,sha=repo(tmp_path); store,tasks,executions,room,agents,task=project(tmp_path/'state'); service=RepositorySources(store)
    prior=executions.create(task['id'],request()); assert service.execution(prior['id'])['repository'] is None
    executions.cancel(prior['id'])
    service.save(room['id'],binding(source,sha,agents[1]['id']))
    run=executions.create(task['id'],request(request_id='second',previous=prior['id'],note='Earlier queued run cancelled'))
    detached=service.save(room['id'],binding(None,None,None,source_path=None,request_id='detach',expected_revision=1))
    assert detached['snapshot'] is None and service.execution(run['id'])['repository']['revision']==1
    executions.cancel(run['id'])
    later=executions.create(task['id'],request(request_id='third',previous=run['id'],note='Earlier queued run cancelled'))
    assert service.execution(later['id'])['repository'] is None


def test_integration_member_revocation_blocks_existing_queue(tmp_path):
    source,sha=repo(tmp_path); store,_,executions,room,agents,task=project(tmp_path/'state'); service=RepositorySources(store)
    saved=service.save(room['id'],binding(source,sha,agents[1]['id']))
    run=executions.create(task['id'],request())
    store.save_agent({'enabled':False},agents[1]['id'])
    assert not executions.claim(run['id']) and executions.get(run['id'])['state']=='failed'
    assert service.get(room['id'])==saved


def test_no_source_read_before_project_authorization(tmp_path,monkeypatch):
    store,_,_,room,agents,_=project(tmp_path/'state'); service=RepositorySources(store)
    monkeypatch.setattr(repo_sources,'inspect_source',lambda *a:pytest.fail('Unauthorized source was read'))
    store.save_agent({'enabled':False},agents[1]['id'])
    with pytest.raises((ValueError,PermissionError)):service.save(room['id'],binding(tmp_path,'a'*40,agents[1]['id']))
    board=store.save_conversation(dict(type='board',title='Board',member_ids=[agents[0]['id']]))
    with pytest.raises(ValueError):service.save(board['id'],binding(tmp_path,'a'*40,agents[0]['id']))
    assert service.get(room['id']) is None


def test_concurrent_requests_and_immutable_tables(tmp_path):
    source,sha=repo(tmp_path); store,_,executions,room,agents,task=project(tmp_path/'state'); service=RepositorySources(store)
    payload=binding(source,sha,agents[1]['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:service.save(room['id'],payload),range(2)))
    assert results[0]==results[1]
    run=executions.create(task['id'],request())
    for table in ('project_repositories','execution_repositories'):
        for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET revision=revision',f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}'):
            with store.connect() as db,pytest.raises(sqlite3.IntegrityError):db.execute(sql)
    assert service.execution(run['id'])['repository']==results[0]


@pytest.mark.parametrize('change',[{'confirm':False},{'expected_revision':True},{'request_id':'with/slash'},{'source_path':None},{'extra':'x'}])
def test_invalid_bindings_never_inspect(tmp_path,monkeypatch,change):
    store,_,_,room,agents,_=project(tmp_path/'state'); service=RepositorySources(store)
    monkeypatch.setattr(repo_sources,'inspect_source',lambda *a:pytest.fail('Invalid source request was read'))
    with pytest.raises(ValueError):service.save(room['id'],binding(tmp_path,'a'*40,agents[1]['id'])|change)


def test_owner_http_binding_and_execution_readback_survive_restart(tmp_path):
    source,sha=repo(tmp_path); state=tmp_path/'state'
    with running(state) as port:
        store,_,executions,room,agents,task=project(state)
        url='/api/workbench/conversations/'+room['id']+'/repository'; body=binding(source,sha,agents[1]['id'])
        assert http(port,'GET',url)==(200,None)
        assert http(port,'POST',url,body,headers={'Authorization':''})[0]==401
        code,saved=http(port,'POST',url,body); assert code==201
        run=executions.create(task['id'],request()); executions.cancel(run['id'])
        assert http(port,'GET',url+'/requests/bind')==(200,saved)
        run_url='/api/workbench/executions/'+run['id']+'/repository'
        assert http(port,'GET',run_url)[1]['repository']==saved
        assert http(port,'POST',run_url,{})[0]==404
        assert http(port,'GET',run_url,headers={'Authorization':''})[0]==401
    with running(state) as port:
        assert http(port,'GET',url)==(200,saved)
        assert http(port,'POST',url,body)==(201,saved)
        assert http(port,'GET',run_url)[1]['repository']==saved


def test_real_checkout_reaches_cli_in_two_independent_runs(tmp_path,monkeypatch):
    source,sha=repo(tmp_path); store,tasks,_,room,agents,task=project(tmp_path/'state')
    c=CLIController(store); ready(monkeypatch,c)
    config=c.settings.resolve()|{'executable':sys.executable}; monkeypatch.setattr(c.settings,'resolve',lambda:config)
    c.repositories.save(room['id'],binding(source,sha,agents[1]['id']))
    other=tasks.create(room['id'],{key:task[key] for key in ('source_message_id','title','scope','acceptance')}|{'agent_id':agents[1]['id'],'request_id':'other'})
    runs=[c.executions.create(t['id'],request()) for t in (task,other)]; seen=[]
    def fake_cli(argv,cwd,env,stdin,timeout,cancel):
        checkout=cwd/'repository'
        assert (checkout/'code.txt').read_text()=='first\n'
        assert git(checkout,'rev-parse','HEAD')==sha
        assert str(source).encode() not in stdin and sha.encode() in stdin
        assert env['HOME']!=str(Path.home())
        (checkout/'code.txt').write_text(cwd.parent.name)
        (cwd/'artifacts').mkdir(); (cwd/'artifacts/result.txt').write_text('Checked source copy')
        seen.append(cwd)
        return {'exit_code':0,'reason':'exited','stdout':b'{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}\n{"type":"turn.completed"}'}
    monkeypatch.setattr(process_tree,'run_process',fake_cli)
    try:
        until(c,lambda:all(c.executions.get(r['id'])['state']=='awaiting_review' for r in runs),timeout=30)
        assert len(seen)==2 and seen[0]!=seen[1]
        assert (source/'code.txt').read_text()=='first\n' and git(source,'rev-parse','HEAD')==sha
        for run in runs:
            assert c.repositories.execution(run['id'])['repository']['revision']==1
    finally:c.close()


@pytest.mark.parametrize('backend',['local','docker'])
def test_controller_passes_frozen_repository_to_selected_backend(tmp_path,monkeypatch,backend):
    source,sha=repo(tmp_path); store,_,_,room,agents,task=project(tmp_path/'state'); c=CLIController(store); ready(monkeypatch,c)
    config=c.settings.resolve()|{'backend':backend,'docker_executable':sys.executable,'docker_image':'sha256:'+'a'*64,'docker_cpus':1,'docker_memory_mb':1024,'docker_pids_limit':32}
    monkeypatch.setattr(c.settings,'resolve',lambda:config)
    record=c.repositories.save(room['id'],binding(source,sha,agents[1]['id']))
    run=c.executions.create(task['id'],request()); seen=[]
    def runner(**kw):
        assert kw['repository']==record['snapshot']; assert str(source) not in kw['prompt']; seen.append(True)
        return dict(exit_code=1,reason='exited',success=False,summary='Controlled failure',workspace=None)
    monkeypatch.setattr(cli_controller,'run_codex' if backend=='local' else 'run_docker',runner)
    try:
        until(c,lambda:c.executions.get(run['id'])['state']=='failed'); assert seen==[True]
    finally:c.close()


@pytest.mark.parametrize('backend',['local','docker'])
@pytest.mark.parametrize('unknown',[False,True])
def test_preparation_failure_never_launches_or_retries(tmp_path,monkeypatch,backend,unknown):
    source,sha=repo(tmp_path); store,_,_,room,agents,task=project(tmp_path/'state')
    c=CLIController(store); ready(monkeypatch,c)
    config=c.settings.resolve()|dict(backend=backend,executable=sys.executable,docker_executable=sys.executable,
        docker_image='sha256:'+'a'*64,docker_cpus=1,docker_memory_mb=1024,docker_pids_limit=32)
    monkeypatch.setattr(c.settings,'resolve',lambda:config)
    c.repositories.save(room['id'],binding(source,sha,agents[1]['id']))
    run=c.executions.create(task['id'],request()); calls=[]
    def fail(*args):
        calls.append(True)
        raise (git_checkout.PreparationUnknownError('Unconfirmed preparation') if unknown else ValueError('Rejected input'))
    monkeypatch.setattr(git_checkout,'prepare_checkout',fail)
    monkeypatch.setattr(process_tree,'run_process',lambda *a,**kw:pytest.fail('CLI must not start'))
    monkeypatch.setattr(docker_worker,'_command',lambda *a,**kw:pytest.fail('Docker must not start'))
    try:
        until(c,lambda:c.executions.get(run['id'])['state']==('unknown' if unknown else 'failed'))
        for _ in range(3):c.tick()
        assert calls==[True]
        if unknown:assert 'Git' in c.executions.get(run['id'])['summary']
        assert not (store.data_dir/'execution-workspaces'/run['id']/'docker-worker.json').exists()
    finally:c.close()


@pytest.mark.parametrize('backend',['local','docker'])
def test_cancel_after_real_copy_prevents_external_launch(tmp_path,monkeypatch,backend):
    source,sha=repo(tmp_path); cancel=Event(); original=git_checkout.prepare_checkout
    identity=str(uuid.uuid4())
    metadata=git_checkout.inspect_source(source,sha)|{'integration_agent_id':'integrator'}
    def copy_then_cancel(*args):
        result=original(*args); cancel.set(); return result
    monkeypatch.setattr(git_checkout,'prepare_checkout',copy_then_cancel)
    monkeypatch.setattr(process_tree,'run_process',lambda *a,**kw:pytest.fail('CLI must not start'))
    monkeypatch.setattr(docker_worker,'_command',lambda *a,**kw:pytest.fail('Docker must not start'))
    options={'image':'sha256:'+'a'*64} if backend=='docker' else {}
    result=(docker_worker.run_docker if backend=='docker' else cli.run_codex)(
        executable=sys.executable,data_dir=tmp_path/'state',execution_id=identity,prompt='Fixture',model='fixture',
        api_key='not-a-real-key',timeout_seconds=10,cancel=cancel,repository=metadata,**options)
    assert result['reason']=='cancelled' and result['workspace'] is None and not result['success']
    assert (tmp_path/'state/execution-workspaces'/identity/'work/repository/code.txt').read_text()=='first\n'
    assert not (tmp_path/'state/execution-workspaces'/identity/'docker-worker.json').exists()


def test_backup_preserves_binding_but_not_source_or_checkout(tmp_path):
    from workbench.controller import ReplyController
    from workbench.settings import Settings
    from workbench.backup import backup, restore
    source,sha=repo(tmp_path); state=tmp_path/'state'
    store,_,_,room,agents,task=project(state); c=ReplyController(store,Settings(store))
    try:
        service=RepositorySources(store); saved=service.save(room['id'],binding(source,sha,agents[1]['id']))
        executions=Executions(store); run=executions.create(task['id'],request()); executions.cancel(run['id'])
        prepare_workspace(state,run['id'],repository=saved['snapshot'])
    finally:c.close()
    backup(state,tmp_path/'bundle'); restore(tmp_path/'bundle',tmp_path/'restored')
    restored=RepositorySources(Store(tmp_path/'restored'))
    assert restored.get(room['id'])==saved and restored.execution(run['id'])['repository']==saved
    assert not (tmp_path/'restored/execution-workspaces').exists()
