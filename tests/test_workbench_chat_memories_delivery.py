"""Real request bodies prove ordinary replies consume only approved fixed memory."""
import hashlib
import json

import pytest

from test_workbench_memories import setup, proposal, decision
from test_workbench_provider import endpoint
from test_workbench_api import running, request as http
from test_workbench_planning import setup as planning_setup
from test_workbench_peer_reviews import fixture as peer_setup
from test_workbench_retrospectives import fixture as retro_setup
from workbench.runs import Runs
from workbench import chat_memories, budgets
from workbench.provider import run_reply
from workbench.context_receipts import ContextReceipts
from workbench.backup import backup, restore
from workbench.store import Store


def prepared(tmp_path):
    store, _, _, task, source, memory = setup(tmp_path)
    for scope, identity in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        candidate = memory.propose(scope, identity, proposal(source, scope, content='F79_APPROVED_' + scope))
        memory.decide(candidate['id'], decision(scope + '-approve'))
    memory.propose('agent', task['agent_id'], proposal(source, 'unapproved', 1, 'F79_PENDING_SECRET'))
    runs = Runs(store)
    return store, runs, task, memory


def create(store, runs, room, actor, key='reply'):
    message = store.send_message(room, {'content': 'Use approved knowledge', 'request_id': key})
    return runs.create(room, {'agent_id': actor, 'source_message_id': message['id'], 'request_id': key})


@pytest.mark.parametrize('kind', ['dm', 'board', 'project'])
def test_approved_memory_reaches_actual_http_without_other_scope(tmp_path, kind):
    store, runs, task, memory = prepared(tmp_path)
    actor = task['agent_id']
    room = task['conversation_id'] if kind == 'board' else store.save_conversation({'type': kind, 'title': kind, 'member_ids': [actor]})['id']
    run = create(store, runs, room, actor); assert runs.claim(run['id'])
    snapshot = runs.snapshot(run['id'])
    with endpoint({'choices': [{'finish_reason': 'stop', 'message': {'content': 'Controlled response'}}]}) as (config, calls):
        run_reply(config, snapshot)
    body = calls[0][2]; text = body['messages'][0]['content']
    assert text == snapshot['instructions'] and 'F79_APPROVED_agent' in text
    assert ('F79_APPROVED_project' in text) == (kind == 'board')
    assert 'F79_PENDING_SECRET' not in json.dumps(body) and 'tools' not in body
    assert len(snapshot['memories']) == (1 if kind == 'dm' else 2)
    assert {m['scope_id'] for m in snapshot['memories']} <= {actor, room}
    receipt = ContextReceipts(store).record_model(run['id'], snapshot, 'fixture')
    assert receipt['instructions']['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    # Another member gets shared knowledge, never this actor's personal memory.
    if kind == 'board':
        other = next(i for i in store.conversation(room)['member_ids'] if i != actor)
        second = create(store, runs, room, other, 'other'); assert runs.claim(second['id'])
        assert 'F79_APPROVED_agent' not in runs.snapshot(second['id'])['instructions']
        assert 'F79_APPROVED_project' in runs.snapshot(second['id'])['instructions']


def test_claim_freezes_old_revision_and_new_run_observes_rollback(tmp_path):
    store, runs, task, memory = prepared(tmp_path)
    first = create(store, runs, task['conversation_id'], task['agent_id'])
    assert runs.claim(first['id']); original = runs.snapshot(first['id'])
    memory.rollback('agent', task['agent_id'], {'request_id': 'rollback', 'expected_version': 1, 'target_version': 0, 'note': 'Owner rollback'})
    assert runs.snapshot(first['id']) == original
    second = create(store, runs, task['conversation_id'], task['agent_id'], 'after-rollback')
    assert runs.claim(second['id']); changed = runs.snapshot(second['id'])
    assert 'F79_APPROVED_agent' not in changed['instructions']
    assert next(m for m in changed['memories'] if m['scope'] == 'agent')['version'] == 2


@pytest.mark.parametrize('change', ['removed', 'archived', 'disabled'])
def test_current_authority_rechecked_but_owner_keeps_history(tmp_path, change):
    store, runs, task, _ = prepared(tmp_path)
    run = create(store, runs, task['conversation_id'], task['agent_id']); assert runs.claim(run['id'])
    with store.connect() as db: original = chat_memories.get(db, runs.get(run['id']))
    if change == 'removed': store.set_member(task['conversation_id'], task['agent_id'], False)
    elif change == 'archived': store.save_conversation({'archived': True}, task['conversation_id'])
    else: store.save_agent({'enabled': False}, task['agent_id'])
    with pytest.raises((ValueError, PermissionError)): runs.snapshot(run['id'])
    with store.connect() as db: assert chat_memories.get(db, runs.get(run['id'])) == original


@pytest.mark.parametrize('kind', ['planning', 'peer', 'retrospective'])
def test_special_model_paths_do_not_acquire_chat_memories(tmp_path, kind):
    if kind == 'planning':
        store, service, cid, p, _ = planning_setup(tmp_path); runs = service.runs; run = service.create(cid, p)
    elif kind == 'peer':
        store, runs, cid, _, service, p = peer_setup(tmp_path); run = service.create(cid, p)
    else:
        store, _, task, _, _, service, p = retro_setup(tmp_path); runs = service.runs; run = service.create('agent', task['agent_id'], p)
    assert runs.claim(run['id']); runs.snapshot(run['id'])
    with store.connect() as db: assert chat_memories.get(db, runs.get(run['id'])) is None


def test_owner_http_metadata_backup_restore_and_no_writes(tmp_path):
    store, runs, task, _ = prepared(tmp_path)
    run = create(store, runs, task['conversation_id'], task['agent_id']); assert runs.claim(run['id'])
    runs.fail(run['id'], 'No external call made')
    path = '/api/workbench/runs/' + run['id'] + '/memories'
    with running(tmp_path) as port:
        with store.connect() as db: before = list(db.iterdump())
        status, value = http(port, 'GET', path); assert status == 200
        assert all(set(m) == {'scope', 'scope_id', 'version', 'chars', 'sha256'} for m in value['memories'])
        assert 'F79_APPROVED' not in json.dumps(value)
        assert http(port, 'GET', path, headers={'Authorization': ''})[0] == 401
        assert http(port, 'GET', '/api/workbench/runs/missing/memories')[0] == 404
        with store.connect() as db: assert list(db.iterdump()) == before
    archive = tmp_path.parent / (tmp_path.name + '-backup')
    restored = tmp_path.parent / (tmp_path.name + '-restored')
    backup(tmp_path, archive); restore(archive, restored)
    with running(restored) as port: assert http(port, 'GET', path) == (200, value)


def test_budget_refusal_rolls_back_memory_and_skill_claim(tmp_path, monkeypatch):
    store, runs, task, _ = prepared(tmp_path)
    run = create(store, runs, task['conversation_id'], task['agent_id'])
    def refused(*args): raise ValueError('Injected reservation rejection')
    monkeypatch.setattr(budgets, 'reserve', refused)
    with pytest.raises(ValueError, match='reservation'): runs.claim(run['id'])
    assert runs.get(run['id'])['state'] == 'queued'
    with store.connect() as db:
        assert chat_memories.get(db, run) is None
        assert db.execute('SELECT count(*) FROM skill_input_snapshots WHERE run_id=?', (run['id'],)).fetchone()[0] == 0
