"""Real Owner HTTP boundary; dispatch is paused and no CLI/model is launched."""
from test_workbench_api import running, request
from test_workbench_project_execution_api import project, configure
from workbench.cli_controller import CLIController
from workbench.project_executions import ProjectExecutions
from workbench.reviews import Reviews
from workbench import artifacts


def interrupted(tmp_path):
    store, plan, payload = project(tmp_path)
    batches = ProjectExecutions(store)
    original = batches.create(plan['id'], payload)
    first, second = [item['execution_id'] for item in original['tasks']]
    assert batches.executions.claim(first)
    batches.executions.report(first, 1, 1, 0, 'Verified output', success=True,
                              artifacts=[{'path': 'result.txt', 'data': b'approved checkpoint'}])
    Reviews(store).save(first, {'request_id': 'approve', 'expected_version': 1, 'decision': 'approved',
        'note': 'Inspected checkpoint', 'artifact_ids': sorted(a['id'] for a in artifacts.list_for(store, first))})
    assert batches.executions.claim(second)
    batches.executions.report(second, 1, 1, 1, 'Failed after inspection')
    return store, batches, original, first, second


def test_checkpoint_http_authorization_replay_stop_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    store, batches, original, first, second = interrupted(tmp_path)
    base = '/api/workbench/project-executions/' + original['id']
    path = base + '/checkpoint-recoveries'
    with running(tmp_path) as port:
        with store.connect() as db: before = list(db.iterdump())
        assert request(port, 'GET', base + '/checkpoint', headers={'Authorization': ''})[0] == 401
        status, preview = request(port, 'GET', base + '/checkpoint')
        assert status == 200 and not preview['blockers']
        assert request(port, 'GET', path) == (200, [])
        with store.connect() as db: assert list(db.iterdump()) == before
        payload = {'request_id': 'resume-once', 'checkpoint_fingerprint': preview['fingerprint'],
                   'reconciliation_note': 'Original processes stopped; external effects checked', 'confirm': True}
        assert request(port, 'POST', path, payload)[0] == 400
        configure(port, tmp_path, monkeypatch)
        assert request(port, 'POST', path, payload, headers={'Authorization': ''})[0] == 401
        assert request(port, 'POST', path, payload, headers={'Origin': 'https://foreign.example'})[0] == 400
        assert request(port, 'POST', path, {**payload, 'confirm': False})[0] == 400
        assert request(port, 'POST', path, {**payload, 'checkpoint_fingerprint': '0' * 64})[0] in (400, 409)
        status, receipt = request(port, 'POST', path, payload)
        assert status == 202 and receipt['source_batch_id'] == original['id']
        assert len(receipt['batch']['tasks']) == 1
        new_id = receipt['batch']['tasks'][0]['execution_id']
        assert new_id not in (first, second)
        assert batches.executions.get(new_id)['previous_execution_id'] == second
        assert batches.executions.get(new_id)['attempt'] == 2
        detail_path = '/api/workbench/checkpoint-recoveries/' + receipt['id']
        assert request(port, 'GET', detail_path)[1]['batch']['id'] == receipt['batch_id']
        assert request(port, 'GET', path) == (200, [receipt])
        assert request(port, 'PATCH', '/api/workbench/cli-settings', {'enabled': False})[0] == 200
        assert request(port, 'POST', path, payload) == (202, receipt)
        assert request(port, 'POST', path, {**payload, 'reconciliation_note': 'Changed'})[0] == 400
        assert request(port, 'POST', base + '/stop', {'confirm': True})[0] == 200
        assert batches.executions.get(new_id)['state'] == 'queued'
        assert request(port, 'POST', '/api/workbench/project-executions/' + receipt['batch_id'] + '/stop', {'confirm': True})[0] == 200
        assert batches.executions.get(new_id)['state'] == 'cancelled'
        assert batches.executions.get(first)['state'] == 'awaiting_review'
    with running(tmp_path) as port:
        assert request(port, 'POST', path, payload) == (202, receipt)
        assert request(port, 'GET', path) == (200, [receipt])
        assert request(port, 'GET', detail_path)[1]['batch']['items'][0]['execution']['id'] == new_id
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 3
