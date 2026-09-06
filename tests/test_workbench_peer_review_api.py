"""Owner-authorized peer exchange over real HTTP and model child processes."""
from concurrent.futures import ThreadPoolExecutor

from test_workbench_api import running, request
from test_workbench_controller import configure, until
from test_workbench_model_reconciliation_api import declaration
from test_workbench_provider import endpoint
from test_workbench_runs import setup
from workbench.settings import Settings


OUTPUT = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Shared review result'}}]}


def source_reply(tmp_path):
    store, runs, room, original = setup(tmp_path)
    source = runs.create(room, original)
    assert runs.claim(source['id'])
    source = runs.finish(source['id'], 'Selected peer source', 'fixture', 2, 3)
    target = next(a for a in store.conversation(room)['member_ids'] if a != original['agent_id'])
    payload = dict(agent_id=target, source_message_id=source['reply_message_id'],
                   request_id='owner-peer-review', confirm=True)
    return store, runs, room, source, payload


def test_owner_http_peer_exchange_single_call_private_context_and_restart(tmp_path, monkeypatch):
    store, runs, room, original = setup(tmp_path)
    target = next(a for a in store.conversation(room)['member_ids'] if a != original['agent_id'])
    store.send_message(room, {'content': 'Unselected group history marker', 'request_id': 'unselected'})
    private = store.save_conversation({'type': 'dm', 'title': 'Private source', 'member_ids': [original['agent_id']]})
    store.send_message(private['id'], {'content': 'Private identity marker', 'request_id': 'secret'})
    with endpoint(OUTPUT) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            create_reply = f'/api/workbench/conversations/{room}/runs'
            status, first = request(port, 'POST', create_reply, original)
            assert status == 202
            until(lambda: runs.get(first['id'])['state'] == 'completed')
            first = runs.get(first['id'])
            create = f'/api/workbench/conversations/{room}/peer-reviews'
            payload = dict(agent_id=target, source_message_id=first['reply_message_id'],
                           request_id='http-peer', confirm=True)
            assert request(port, 'POST', create, payload, headers={'Authorization': ''})[0] == 401
            assert request(port, 'GET', create, headers={'Authorization': ''})[0] == 401
            for invalid in ({**payload, 'confirm': False}, {**payload, 'confirm': 1},
                            {k: v for k, v in payload.items() if k != 'confirm'},
                            {**payload, 'instructions': 'Unapproved instructions'},
                            {**payload, 'source_message_id': original['source_message_id']},
                            {**payload, 'agent_id': original['agent_id']}):
                assert request(port, 'POST', create, invalid)[0] == 400
            assert request(port, 'GET', create) == (200, []) and len(calls) == 1
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(lambda _: request(port, 'POST', create, payload), range(8)))
            assert all(status == 202 for status, _ in responses)
            assert len({row['id'] for _, row in responses}) == 1
            peer = responses[0][1]
            until(lambda: runs.get(peer['id'])['state'] == 'completed')
            assert len(calls) == 2  # A's reply, then B's explicitly authorized review.
            messages = calls[1][2]['messages']
            assert [m['role'] for m in messages] == ['system', 'user']
            assert messages[1]['content'] == f"[群成员 {original['agent_id']}]\nShared review result"
            assert all('Private identity marker' not in m['content'] and
                       'Unselected group history marker' not in m['content'] and
                       'Owner question' not in m['content'] for m in messages)
            status, receipt = request(port, 'GET', '/api/workbench/peer-reviews/' + peer['id'])
            assert status == 200 and receipt['source_message_id'] == first['reply_message_id']
            assert receipt['request_payload'] == payload
            assert receipt['reply_message_id'] == runs.get(peer['id'])['reply_message_id']
            assert request(port, 'GET', create)[1] == [receipt]
            assert all(r['id'] != peer['id'] for r in request(port, 'GET', create_reply)[1])
            assert request(port, 'POST', create_reply, {k: payload[k] for k in ('agent_id', 'source_message_id', 'request_id')})[0] == 400
            Settings(store).save({'enabled': False})
            store.save_agent({'enabled': False}, target)
            store.save_conversation({'archived': True}, room)
            assert request(port, 'POST', create, payload) == (202, receipt)
            assert request(port, 'POST', create, {**payload, 'request_id': 'new-after-revocation'})[0] == 400
            assert len(calls) == 2
        with running(tmp_path) as port:
            assert request(port, 'GET', '/api/workbench/peer-reviews/' + peer['id']) == (200, receipt)
            assert request(port, 'POST', create, payload) == (202, receipt)
            assert len(calls) == 2
    assert len([m for m in store.messages(room) if m['sender_kind'] == 'agent']) == 2


def test_unknown_peer_requires_check_then_explicit_new_key(tmp_path, monkeypatch):
    from workbench.peer_reviews import PeerReviews
    store, runs, room, source, payload = source_reply(tmp_path)
    old = PeerReviews(store).create(room, payload)
    assert runs.claim(old['id'])
    original = runs.fail(old['id'], 'State-only unknown fixture', state='unknown')
    with endpoint(OUTPUT) as (config, calls), running(tmp_path) as port:
        configure(Settings(store), config, monkeypatch)
        create = f'/api/workbench/conversations/{room}/peer-reviews'
        waiting = request(port, 'GET', '/api/workbench/model-run-reconciliations/pending')[1]
        assert len(waiting) == 1 and waiting[0]['id'] == old['id'] and waiting[0]['kind'] == 'peer_review'
        replacement = {**payload, 'request_id': 'explicit-new-peer'}
        assert request(port, 'POST', create, replacement)[0] == 400
        check = '/api/workbench/runs/' + old['id'] + '/reconciliation'
        assert request(port, 'POST', check, declaration())[0] == 201
        assert request(port, 'POST', create, payload)[1]['id'] == old['id']
        assert calls == []
        status, new = request(port, 'POST', create, replacement)
        assert status == 202 and new['id'] != old['id']
        until(lambda: runs.get(new['id'])['state'] == 'completed')
        assert request(port, 'POST', create, replacement)[1]['id'] == new['id']
        assert len(calls) == 1 and runs.get(old['id']) == original
        assert len(store.messages(room)) == 3


def test_peer_revocation_during_real_provider_request_does_not_publish(tmp_path, monkeypatch):
    store, runs, room, source, payload = source_reply(tmp_path)
    with endpoint(OUTPUT, delay=0.6) as (config, calls), running(tmp_path) as port:
        configure(Settings(store), config, monkeypatch)
        path = f'/api/workbench/conversations/{room}/peer-reviews'
        status, peer = request(port, 'POST', path, payload)
        assert status == 202
        until(lambda: len(calls) == 1)
        store.set_member(room, payload['agent_id'], False)
        until(lambda: runs.get(peer['id'])['state'] == 'failed')
        assert runs.get(peer['id'])['reply_message_id'] is None
        assert request(port, 'POST', path, payload)[1]['id'] == peer['id']
        assert len(calls) == 1 and len(store.messages(room)) == 2
        assert runs.get(source['id']) == source
