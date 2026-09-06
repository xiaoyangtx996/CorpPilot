"""Owner HTTP acknowledgement and one explicitly authorized local provider call."""
from test_workbench_api import running, request
from test_workbench_runs import setup
from test_workbench_controller import configure, until
from test_workbench_provider import endpoint
from workbench.settings import Settings


def declaration():
    return dict(request_id='owner-check', attempt=1, requirement_version=1,
                local_request_stopped=True, provider_effects_checked=True,
                note='Fixture: local request ended; provider outcome and possible charges checked')


def test_http_owner_checks_replay_and_restart_preserve_original_unknown(tmp_path):
    store, runs, room, payload = setup(tmp_path)
    run = runs.create(room, payload)
    runs.claim(run['id'])
    original = runs.fail(run['id'], 'Fixture result unavailable', state='unknown')
    path = '/api/workbench/runs/' + run['id']
    create = '/api/workbench/conversations/' + room + '/runs'
    pending = '/api/workbench/model-run-reconciliations/pending'
    check = declaration()
    with running(tmp_path) as port:
        assert request(port, 'GET', pending, headers={'Authorization': ''})[0] == 401
        assert request(port, 'POST', path + '/reconciliation', check, headers={'Authorization': ''})[0] == 401
        rows = request(port, 'GET', pending)[1]
        assert len(rows) == 1 and rows[0]['id'] == run['id'] and rows[0]['kind'] == 'reply'
        assert request(port, 'GET', path + '/reconciliation') == (200, None)
        assert request(port, 'POST', create, {**payload, 'request_id': 'replacement'})[0] == 400
        for invalid in ({}, {**check, 'local_request_stopped': 1}, {**check, 'provider_effects_checked': False}):
            assert request(port, 'POST', path + '/reconciliation', invalid)[0] == 400
        assert request(port, 'POST', path + '/reconciliation', {**check, 'attempt': 2})[0] == 409
        assert request(port, 'GET', path + '/reconciliation') == (200, None)
        # Historical checks remain possible without the original active identity.
        store.save_agent({'enabled': False}, payload['agent_id'])
        store.save_conversation({'archived': True}, room)
        status, receipt = request(port, 'POST', path + '/reconciliation', check)
        assert status == 201 and receipt['run_id'] == run['id']
        assert request(port, 'GET', pending) == (200, [])
        assert request(port, 'POST', path + '/reconciliation', check) == (201, receipt)
        assert request(port, 'POST', path + '/reconciliation', {**check, 'note': 'different'})[0] == 400
        assert request(port, 'POST', create, payload) == (202, original)
        assert request(port, 'GET', path) == (200, original)
    with running(tmp_path) as port:
        assert request(port, 'GET', path + '/reconciliation') == (200, receipt)
        assert request(port, 'POST', path + '/reconciliation', check) == (201, receipt)
        assert request(port, 'GET', path) == (200, original)
        assert request(port, 'GET', pending) == (200, [])
    assert len(store.messages(room)) == 1


def test_declaration_does_not_call_model_but_explicit_new_key_calls_once(tmp_path, monkeypatch):
    store, runs, room, payload = setup(tmp_path)
    old = runs.create(room, payload)
    runs.claim(old['id'])
    original = runs.fail(old['id'], 'Fixture unavailable result', state='unknown')
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Explicit replacement result'}}]}
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            path = '/api/workbench/runs/' + old['id']
            create = '/api/workbench/conversations/' + room + '/runs'
            replacement = {**payload, 'request_id': 'explicit-new-request'}
            assert request(port, 'POST', create, replacement)[0] == 400
            status, receipt = request(port, 'POST', path + '/reconciliation', declaration())
            assert status == 201
            assert request(port, 'POST', path + '/reconciliation', declaration()) == (201, receipt)
            assert request(port, 'POST', create, payload) == (202, original)
            assert calls == []
            status, new = request(port, 'POST', create, replacement)
            assert status == 202 and new['id'] != old['id']
            assert request(port, 'POST', create, replacement)[1]['id'] == new['id']
            until(lambda: runs.get(new['id'])['state'] == 'completed')
            assert len(calls) == 1
            assert runs.get(old['id']) == original
            assert len(store.messages(room)) == 2
