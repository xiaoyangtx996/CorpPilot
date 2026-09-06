import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench.store import Store
from workbench.planning import Planning


def setup(tmp_path):
    store=Store(tmp_path); api=Planning(store); agents=store.agents()[:3]
    cid=store.save_conversation(dict(type='dm',title='source',member_ids=[agents[0]['id']]))['id']
    store.send_message(cid,dict(content='PRIVATE PRIOR',request_id='prior'))
    source=store.send_message(cid,dict(content='TARGET ONLY',request_id='source'))
    payload=dict(agent_id=agents[0]['id'],source_message_id=source['id'],request_id='plan',candidate_ids=[agents[1]['id']])
    proposal=dict(title='Project',shared_brief='Shared goal',tasks=[dict(key='a',title='Task',scope='Scope',acceptance='Done',agent_id=agents[1]['id'],depends_on=[])])
    return store,api,cid,payload,proposal


def test_single_completion_privacy_and_shared_queue(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    run=api.create(cid,payload); rid=run['id']
    assert api.runs.list(cid)==[] and api.runs.pending()[0]['id']==rid
    assert api.runs.claim(rid)
    snapshot=api.runs.snapshot(rid)
    assert [m['content'] for m in snapshot['messages']]==['TARGET ONLY']
    assert set(run['candidate_snapshot'][0])=={'id','name','template_id','skills'}
    assert 'PRIVATE PRIOR' not in str(snapshot)
    result=api.runs.finish(rid,json.dumps(proposal),'model',1,2)
    assert result['state']=='completed' and result['reply_message_id'] is None
    assert api.get(rid)['proposal']==proposal
    assert api.runs.finish(rid,json.dumps(dict(proposal,title='ignored')),'model',1,2)==result
    assert len(store.messages(cid))==2 and len(store.conversations())==1
    assert api.list(cid)[0]['proposal']==proposal
    with store.connect() as db:
        for sql in ["UPDATE planning_requests SET proposal='{}'", 'DELETE FROM planning_requests', 'INSERT OR REPLACE INTO planning_requests SELECT * FROM planning_requests']:
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_concurrent_replay_type_conflict_restart(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda _:api.create(cid,payload),range(8)))
    assert all(row==rows[0] for row in rows)
    with pytest.raises(ValueError): api.runs.create(cid,{k:payload[k] for k in ('agent_id','source_message_id','request_id')})
    with pytest.raises(ValueError): api.create(cid,dict(payload,candidate_ids=[payload['agent_id']]))
    api.runs.create(cid,{**{k:payload[k] for k in ('agent_id','source_message_id')},'request_id':'chat'})
    with pytest.raises(ValueError): api.create(cid,dict(payload,request_id='chat'))
    store.save_agent({'enabled':False},payload['agent_id'])
    assert Planning(Store(tmp_path)).create(cid,payload)==rows[0]


@pytest.mark.parametrize('case',['invalid_json','fence','extra','cycle','foreign','duplicate_json','missing','nonobject','trailing','nan'])
def test_output_rejects_without_effect(tmp_path,case):
    store,api,cid,payload,proposal=setup(tmp_path); run=api.create(cid,payload); rid=run['id']; api.runs.claim(rid)
    if case=='extra': proposal['execute']=True
    if case=='cycle': proposal['tasks'][0]['depends_on']=['a']
    if case=='foreign': proposal['tasks'][0]['agent_id']=payload['agent_id']
    if case=='missing': del proposal['tasks'][0]['acceptance']
    content=json.dumps(proposal)
    if case=='invalid_json': content='{'
    if case=='fence': content='```json\n'+content+'\n```'
    if case=='duplicate_json': content=content[:-1]+',"title":"other"}'
    if case=='nonobject': content='[]'
    if case=='trailing': content += ' trailing'
    if case=='nan': content=content.replace('"Project"','NaN')
    with pytest.raises(ValueError,match='提案'): api.runs.finish(rid,content,'model',1,2)
    assert api.get(rid)['proposal'] is None and len(store.messages(cid))==2
    api.runs.fail(rid,'模型协作提案无效'); assert api.get(rid)['state']=='failed'


def test_cancel_recovery_and_revocations(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    rid=api.create(cid,payload)['id']; api.runs.cancel(rid)
    assert api.get(rid)['state']=='cancelled' and not api.runs.claim(rid)
    rid=api.create(cid,dict(payload,request_id='second'))['id']; api.runs.claim(rid)
    store.save_agent({'enabled':False},payload['candidate_ids'][0])
    with pytest.raises(ValueError): api.runs.snapshot(rid)
    with pytest.raises(ValueError): api.runs.finish(rid,json.dumps(proposal),'model',None,None)
    api.runs.recover(); assert api.get(rid)['state']=='unknown'
    assert api.runs.pending()==[]


def test_bad_candidate_rolls_back_and_coordinator_revoke(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    for ids in [[],['missing'],payload['candidate_ids']*2]:
        with pytest.raises(ValueError): api.create(cid,dict(payload,candidate_ids=ids))
    assert api.runs.pending()==[]
    rid=api.create(cid,payload)['id']; api.runs.claim(rid)
    store.save_conversation({'archived':True},cid)
    with pytest.raises(ValueError): api.runs.finish(rid,json.dumps(proposal),'model',None,None)
    assert api.get(rid)['proposal'] is None


def test_frozen_catalog_order_and_size_bound(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    ids=[payload['candidate_ids'][0],payload['agent_id']]
    run=api.create(cid,dict(payload,candidate_ids=ids))
    assert [row['id'] for row in run['candidate_snapshot']]==ids
    store.save_agent({'name':'Renamed','skills':['coding']},ids[0])
    assert api.get(run['id'])['candidate_snapshot']==run['candidate_snapshot']
    api.runs.claim(run['id'])
    assert 'Renamed' not in api.runs.snapshot(run['id'])['instructions']
    # Legacy label-only records can still exceed the catalog byte cap; new edits validate loaded IDs.
    big=[]
    for i in range(3):
        agent=store.save_agent(dict(name=f'Large{i}',template_id=store.templates()[0]['id']))
        with store.connect() as db:
            db.execute('UPDATE agents SET skills=? WHERE id=?',(json.dumps([f'{n}'+ '中'*195 for n in range(64)]),agent['id']))
        big.append(agent['id'])
    before=api.list(cid)
    with pytest.raises(ValueError,match='64KiB'):
        api.create(cid,dict(payload,request_id='large',candidate_ids=big))
    assert api.list(cid)==before


def test_metadata_insert_failure_rolls_back_run(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER inject_plan_insert BEFORE INSERT ON planning_requests
            BEGIN SELECT RAISE(ABORT, 'injected metadata failure'); END""")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        api.create(cid,payload)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM planning_requests').fetchone()[0]==0
    assert api.runs.pending()==[]


def test_completion_write_failure_rolls_back_proposal_then_recover(tmp_path):
    store,api,cid,payload,proposal=setup(tmp_path)
    run=api.create(cid,payload); rid=run['id']; api.runs.claim(rid)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER inject_run_complete BEFORE UPDATE ON runs
            WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT, 'injected completed failure'); END""")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        api.runs.finish(rid,json.dumps(proposal),'model',1,2)
    assert api.get(rid)['proposal'] is None and api.get(rid)['state']=='running'
    restored=Planning(Store(tmp_path)); restored.runs.recover()
    assert restored.get(rid)['state']=='unknown'
    assert restored.get(rid)['proposal'] is None and restored.runs.pending()==[]
    assert len(store.messages(cid))==2


@pytest.mark.parametrize('case',['two_node_cycle','seventeen_tasks','too_many_candidates'])
def test_graph_and_count_boundaries(tmp_path,case):
    store,api,cid,payload,proposal=setup(tmp_path)
    if case=='too_many_candidates':
        with pytest.raises(ValueError,match='1–100'):
            api.create(cid,dict(payload,candidate_ids=[f'candidate-{i}' for i in range(101)]))
        assert api.runs.pending()==[]
        return
    rid=api.create(cid,payload)['id']; api.runs.claim(rid)
    first=proposal['tasks'][0]
    if case=='two_node_cycle':
        proposal['tasks']=[dict(first,depends_on=['b']),dict(first,key='b',depends_on=['a'])]
    else:
        proposal['tasks']=[dict(first,key=f't{i}') for i in range(17)]
    with pytest.raises(ValueError,match='提案'):
        api.runs.finish(rid,json.dumps(proposal),'model',1,2)
    assert api.get(rid)['proposal'] is None and len(store.messages(cid))==2
