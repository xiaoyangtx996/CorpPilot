import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workbench_runs import setup
from workbench.peer_reviews import PeerReviews
from workbench.planning import Planning
from workbench.model_reconciliations import ModelReconciliations
from workbench.store import Store


def fixture(tmp_path):
    store,runs,cid,p=setup(tmp_path)
    ordinary=runs.create(cid,p);runs.claim(ordinary['id']);runs.finish(ordinary['id'],'SHARED REPLY','fixture',2,3)
    original=runs.get(ordinary['id'])
    target=next(a['id'] for a in store.agents()[:2] if a['id']!=p['agent_id'])
    peer=PeerReviews(store)
    payload=dict(request_id='peer',source_message_id=original['reply_message_id'],agent_id=target,confirm=True)
    return store,runs,cid,original,peer,payload


def test_private_context_single_publish_and_explicit_next_hop(tmp_path):
    store,runs,cid,original,peer,p=fixture(tmp_path)
    store.send_message(cid,dict(request_id='unrelated',content='UNRELATED GROUP HISTORY'))
    run=peer.create(cid,p)
    assert peer.list(cid)==[run]
    assert run['id'] not in {r['id'] for r in runs.list(cid)}
    runs.claim(run['id']);context=runs.snapshot(run['id'])
    assert [m['content'] for m in context['messages']]==['SHARED REPLY']
    assert 'UNRELATED GROUP HISTORY' not in str(context)
    assert context['agent']['id']==p['agent_id']
    assert 'source_snapshot' not in peer.get(run['id'])
    runs.finish(run['id'],'PEER FEEDBACK','fixture',1,1)
    done=peer.get(run['id']);assert done['state']=='completed'
    assert len(store.messages(cid))==4
    runs.finish(run['id'],'duplicate','fixture',1,1)
    assert len(store.messages(cid))==4 and runs.pending()==[]
    next_run=peer.create(cid,{**p,'request_id':'explicit-next','source_message_id':done['reply_message_id'],'agent_id':original['agent_id']})
    assert runs.claim(next_run['id'])
    assert runs.snapshot(next_run['id'])['messages'][0]['content']=='PEER FEEDBACK'


@pytest.mark.parametrize('bad',['self','owner','fake','foreign','dm','archived','source-disabled','target-disabled','source-removed','target-removed','confirm','extra'])
def test_authorization_guards(tmp_path,bad):
    store,runs,cid,original,peer,p=fixture(tmp_path)
    if bad=='self':p['agent_id']=original['agent_id']
    elif bad=='owner':p['source_message_id']=original['source_message_id']
    elif bad=='fake':p['source_message_id']=store.send_message(cid,dict(request_id='fake',content='Agent unbound'),actor_id=original['agent_id'])['id']
    elif bad=='foreign':cid=store.save_conversation(dict(type='board',title='Other',member_ids=[p['agent_id'],original['agent_id']]))['id']
    elif bad=='dm':
        with store.connect() as db:db.execute("UPDATE conversations SET type='dm' WHERE id=?",(cid,))
    elif bad=='archived':store.save_conversation({'archived':True},cid)
    elif bad.endswith('disabled'):store.save_agent({'enabled':False},original['agent_id'] if bad.startswith('source') else p['agent_id'])
    elif bad.endswith('removed'):store.set_member(cid,original['agent_id'] if bad.startswith('source') else p['agent_id'],False)
    elif bad=='confirm':p['confirm']=1
    else:p['extra']=True
    with pytest.raises((ValueError,PermissionError)):peer.create(cid,p)
    assert peer.list(cid)==[]


def test_ordinary_source_strict_and_cross_type_keys(tmp_path):
    _,runs,cid,original,peer,p=fixture(tmp_path)
    with pytest.raises(ValueError):runs.create(cid,{k:v for k,v in p.items() if k!='confirm'})
    first=peer.create(cid,p)
    with pytest.raises(ValueError):runs.create(cid,{k:v for k,v in p.items() if k!='confirm'})
    with pytest.raises(ValueError):Planning(peer.store).create(cid,{k:v for k,v in p.items() if k!='confirm'}|{'candidate_ids':[p['agent_id']]})
    with pytest.raises(ValueError):peer.create(cid,{**p,'request_id':original['request_id']})
    assert peer.get(first['id'])['request_payload']==p


def test_concurrent_replay_and_restart_after_revocation(tmp_path):
    store,runs,cid,_,peer,p=fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:peer.create(cid,p),range(3)))
    assert all(r==results[0] for r in results)
    runs.cancel(results[0]['id']);expected=peer.get(results[0]['id'])
    store.save_agent({'enabled':False},p['agent_id'])
    assert PeerReviews(Store(tmp_path)).create(cid,p)==expected
    with pytest.raises(ValueError):peer.create(cid,{**p,'source_message_id':'different'})
    with store.connect() as db:
        for sql in ('DELETE FROM peer_review_requests','UPDATE peer_review_requests SET payload=payload','INSERT OR REPLACE INTO peer_review_requests SELECT * FROM peer_review_requests'):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)


@pytest.mark.parametrize('phase',['snapshot','finish'])
@pytest.mark.parametrize('actor',['source','target'])
def test_source_revocation_rechecked(tmp_path,phase,actor):
    store,runs,cid,original,peer,p=fixture(tmp_path);run=peer.create(cid,p);runs.claim(run['id'])
    if phase=='finish':runs.snapshot(run['id'])
    store.save_agent({'enabled':False},original['agent_id'] if actor=='source' else p['agent_id'])
    with pytest.raises((ValueError,PermissionError)):
        if phase=='snapshot':runs.snapshot(run['id'])
        else:runs.finish(run['id'],'not published','fixture',1,1)
    assert runs.get(run['id'])['reply_message_id'] is None


def test_unknown_scope_pending_claim_and_declaration(tmp_path):
    store,runs,cid,original,peer,p=fixture(tmp_path)
    first=peer.create(cid,p);queued=peer.create(cid,{**p,'request_id':'waiting'})
    runs.claim(first['id']);runs.recover()
    assert peer.create(cid,p)['state']=='unknown'
    with pytest.raises(ValueError):peer.create(cid,{**p,'request_id':'new'})
    assert not runs.claim(queued['id']) and runs.pending()==[]
    ordinary=runs.create(cid,dict(request_id='ordinary-new',source_message_id=original['source_message_id'],agent_id=p['agent_id']))
    assert [r['id'] for r in runs.pending()]==[ordinary['id']]
    checks=ModelReconciliations(store);assert checks.pending()[0]['kind']=='peer_review'
    checks.save(first['id'],dict(request_id='check',attempt=1,requirement_version=1,local_request_stopped=True,provider_effects_checked=True,note='Checked fixture effects'))
    assert runs.claim(queued['id']) and runs.get(first['id'])['state']=='unknown'


@pytest.mark.parametrize('phase',['create','finish'])
def test_real_transaction_rollback(tmp_path,phase):
    store,runs,cid,_,peer,p=fixture(tmp_path)
    if phase=='create':
        with store.connect() as db:db.execute("CREATE TRIGGER injected BEFORE INSERT ON peer_review_requests BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError):peer.create(cid,p)
        assert runs.pending()==[]
    else:
        run=peer.create(cid,p);runs.claim(run['id'])
        before=store.messages(cid)
        with store.connect() as db:db.execute("CREATE TRIGGER injected BEFORE UPDATE ON runs WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError):runs.finish(run['id'],'rollback','fixture',1,1)
        assert store.messages(cid)==before and runs.get(run['id'])['state']=='running'

def test_target_role_frozen_at_creation_and_source_role_excluded(tmp_path):
    store,runs,cid,original,peer,p=fixture(tmp_path)
    with store.connect() as db:
        source_template=db.execute('SELECT template_id FROM agents WHERE id=?',(original['agent_id'],)).fetchone()[0]
        target_template=db.execute('SELECT template_id FROM agents WHERE id=?',(p['agent_id'],)).fetchone()[0]
        assert source_template != target_template
        db.execute('UPDATE templates SET instructions=? WHERE id=?',('SOURCE_PRIVATE_ROLE_MARKER',source_template))
        db.execute('UPDATE templates SET instructions=? WHERE id=?',('TARGET_APPROVED_ROLE_MARKER',target_template))
    run=peer.create(cid,p)
    with store.connect() as db:
        db.execute('UPDATE templates SET instructions=? WHERE id=?',('TARGET_LATER_CHANGED_MARKER',target_template))
    assert runs.claim(run['id'])
    snapshot=runs.snapshot(run['id'])
    assert 'TARGET_APPROVED_ROLE_MARKER' in snapshot['instructions']
    assert 'TARGET_LATER_CHANGED_MARKER' not in str(snapshot)
    assert 'SOURCE_PRIVATE_ROLE_MARKER' not in str(snapshot)
    assert [message['content'] for message in snapshot['messages']]==['SHARED REPLY']
