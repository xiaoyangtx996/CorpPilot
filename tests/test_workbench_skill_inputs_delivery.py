"""Real model HTTP and controlled CLI prove actual Skill consumption and isolation."""
import hashlib
import json

import pytest

from test_workbench_runs import setup as reply_setup
from test_workbench_planning import setup as planning_setup
from test_workbench_peer_reviews import fixture as peer_setup
from test_workbench_retrospectives import fixture as retro_setup
from test_workbench_cli_controller import fixture as cli_setup, until, result
from test_workbench_provider import endpoint
from test_workbench_api import running, request as http
from workbench import skill_inputs, cli_controller
from workbench.provider import run_reply
from workbench.context_receipts import ContextReceipts
from workbench.runs import Runs
from workbench.store import Store
from workbench.backup import backup, restore
from runtime.traffic_monitor import TrafficMonitor


def bound(store, actor):
    store.save_agent({'skills':['coding']},actor)
    return skill_inputs.load_selected(['coding'])[0]


@pytest.mark.parametrize('kind',['reply','planning','peer','retrospective'])
def test_all_model_paths_send_only_actor_bound_skill_over_http(tmp_path,kind):
    if kind=='reply':
        store,runs,cid,p=reply_setup(tmp_path); actor=p['agent_id']; skill=bound(store,actor); run=runs.create(cid,p)
    elif kind=='planning':
        store,service,cid,p,_=planning_setup(tmp_path); runs=service.runs; actor=p['agent_id']; skill=bound(store,actor);run=service.create(cid,p)
    elif kind=='peer':
        store,runs,cid,_,service,p=peer_setup(tmp_path);actor=p['agent_id'];skill=bound(store,actor);run=service.create(cid,p)
    else:
        store,_,task,_,_,service,p=retro_setup(tmp_path);runs=service.runs;actor=task['agent_id'];skill=bound(store,actor);run=service.create('agent',actor,p)
    other=next(a for a in store.agents() if a['id']!=actor)
    store.save_agent({'skills':['demo-generator']},other['id'])
    assert runs.claim(run['id']);snapshot=runs.snapshot(run['id'])
    assert snapshot['skills']==[skill]
    assert json.dumps(skill['content'],ensure_ascii=False)[1:-1] in snapshot['instructions']
    with endpoint({'choices':[{'finish_reason':'stop','message':{'content':'Controlled provider result'}}]}) as (config,calls):
        run_reply(config,snapshot)
    assert len(calls)==1;body=calls[0][2]
    assert body['messages'][0]['content']==snapshot['instructions']
    assert skill['version'] in body['messages'][0]['content']
    assert 'demo-generator' not in body['messages'][0]['content']
    assert 'tools' not in body
    receipt=ContextReceipts(store).record_model(run['id'],snapshot,'fixture')
    assert receipt['instructions']['sha256']==hashlib.sha256(snapshot['instructions'].encode()).hexdigest()


@pytest.mark.parametrize('backend',['local','docker'])
def test_cli_prompt_contains_fixed_skill_for_both_adapters(tmp_path,monkeypatch,backend):
    store,_,controller,tasks,runs=cli_setup(tmp_path,monkeypatch)
    skill=bound(store,tasks[0]['agent_id']);calls=[]
    if backend=='docker':
        config=controller.settings.resolve()|dict(backend='docker',docker_executable='C:/fake/docker.exe',docker_image='sha256:'+'a'*64,docker_cpus=1,docker_memory_mb=1024,docker_pids_limit=128)
        monkeypatch.setattr(controller.settings,'resolve',lambda:config)
    def runner(**kw):calls.append(kw);return result()
    monkeypatch.setattr(cli_controller,'run_'+('docker' if backend=='docker' else 'codex'),runner)
    monkeypatch.setattr(cli_controller,'capture',lambda *args:[])
    try:
        until(controller,lambda:controller.executions.get(runs[0]['id'])['state']=='awaiting_review')
        assert len(calls)==1
        prepared=json.loads(calls[0]['prompt'].split('\n',1)[1])
        assert skill['version'] in prepared['role'] and json.dumps(skill['content'],ensure_ascii=False)[1:-1] in prepared['role']
        with store.connect() as db:assert skill_inputs.get(db,'cli',controller.executions._run(db,runs[0]['id']))['skills']==[skill]
    finally:controller.close()


def test_legacy_invalid_binding_fails_before_budget_rpm_or_provider(tmp_path):
    store,runs,cid,p=reply_setup(tmp_path)
    with store.connect() as db:db.execute('UPDATE agents SET skills=? WHERE id=?',(json.dumps(['old-label']),p['agent_id']))
    # Full UI payload may retain the exact legacy list while renaming.
    store.save_agent({'name':'Renamed legacy','skills':['old-label']},p['agent_id'])
    with pytest.raises(ValueError):store.save_agent({'skills':['another-missing']},p['agent_id'])
    run=runs.create(cid,p);monitor=TrafficMonitor(tmp_path/'isolated-usage.jsonl')
    assert not monitor.reserve_call(1,lambda:runs.claim(run['id']))
    assert runs.get(run['id'])['state']=='failed'
    assert monitor.reserve_call(1,lambda:True)
    with store.connect() as db:
        assert skill_inputs.get(db,'model',runs._run(db,run['id'])) is None
        assert db.execute('SELECT count(*) FROM budget_reservations').fetchone()[0]==0


def test_unbind_rejects_pending_input_and_empty_snapshot_stays_empty(tmp_path):
    store,runs,cid,p=reply_setup(tmp_path);bound(store,p['agent_id']);run=runs.create(cid,p);assert runs.claim(run['id'])
    original=runs.snapshot(run['id'])
    store.save_agent({'skills':[]},p['agent_id'])
    with pytest.raises(PermissionError,match='Skill'):runs.snapshot(run['id'])
    runs.fail(run['id'],'Owner removed skill before provider call')
    empty=runs.create(cid,{**p,'request_id':'empty'});assert runs.claim(empty['id'])
    store.save_agent({'skills':['coding']},p['agent_id'])
    assert runs.snapshot(empty['id'])['skills']==[]
    assert original['skills'][0]['version'] not in runs.snapshot(empty['id'])['instructions']


def test_owner_catalog_and_historical_snapshot_get_are_read_only(tmp_path):
    store,runs,cid,p=reply_setup(tmp_path);bound(store,p['agent_id']);run=runs.create(cid,p);assert runs.claim(run['id'])
    with store.connect() as db:expected=skill_inputs.get(db,'model',runs._run(db,run['id']))
    runs.fail(run['id'],'Fixture completed input verification')
    with running(tmp_path) as port:
        with store.connect() as db:before=list(db.iterdump())
        path='/api/workbench/runs/'+run['id']+'/skills'
        assert http(port,'GET',path,headers={'Authorization':''})[0]==401
        assert http(port,'GET','/api/workbench/skills')==(200,skill_inputs.catalog())
        assert http(port,'GET',path)==(200,expected)
        assert http(port,'GET','/api/workbench/runs/missing/skills')[0]==404
        with store.connect() as db:assert list(db.iterdump())==before
    assert Runs(Store(tmp_path)).get(run['id'])['state']=='failed'
    archive=tmp_path.parent/(tmp_path.name+'-backup')
    restored=tmp_path.parent/(tmp_path.name+'-restored')
    backup(tmp_path,archive);restore(archive,restored)
    recovered=Store(restored);recovered_runs=Runs(recovered)
    with recovered.connect() as db:
        assert skill_inputs.get(db,'model',recovered_runs._run(db,run['id']))==expected
