"""Real Owner HTTP framing; dispatch paused so no fixture executable is launched."""
from test_workbench_api import running, request
from test_workbench_collaboration import setup
from workbench.cli_controller import CLIController
from workbench.executions import Executions


def project(tmp_path):
    store, plans, source, draft = setup(tmp_path)
    for actor in {task['agent_id'] for task in draft['tasks']}:
        store.save_agent({'tools': ['read', 'write', 'execute']}, actor)
    plan = plans.create(source, draft)
    payload = {'request_id': 'owner-batch', 'tasks': [
        {'task_id': identity, 'expected_version': 1, 'previous_execution_id': None, 'reconciliation_note': ''}
        for identity in plan['task_ids'].values()]}
    return store, plan, payload


def configure(port, tmp_path, monkeypatch):
    executable = tmp_path / 'never-executed.exe'
    executable.touch()
    monkeypatch.setenv('F43_TEST_KEY', 'fixture-only')
    assert request(port, 'PATCH', '/api/workbench/cli-settings', {
        'enabled': True, 'executable': str(executable), 'model': 'fixture', 'api_key_env': 'F43_TEST_KEY'})[0] == 200


def test_batch_http_auth_config_origin_stop_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    store, plan, payload = project(tmp_path)
    path = '/api/workbench/collaboration-plans/' + plan['id'] + '/executions'
    with running(tmp_path) as port:
        assert request(port, 'GET', '/api/workbench/conversations/' + plan['project_conversation_id'] + '/origin-plan') == (200, plan)
        assert request(port, 'POST', path, payload, headers={'Authorization': ''})[0] == 401
        assert request(port, 'POST', path, payload)[0] == 400  # Disabled CLI never admits a new batch.
        assert request(port, 'GET', path)[1] == []
        configure(port, tmp_path, monkeypatch)
        status, receipt = request(port, 'POST', path, payload)
        assert status == 202 and len(receipt['tasks']) == 2
        assert receipt['request_payload'] == payload
        assert request(port, 'POST', path, payload) == (202, receipt)
        detail_path = '/api/workbench/project-executions/' + receipt['id']
        status, detail = request(port, 'GET', detail_path)
        assert status == 200 and len(detail['items']) == 2
        assert {item['execution']['state'] for item in detail['items']} == {'queued'}
        assert any(not item['dependencies']['ready'] for item in detail['items'])
        assert all(item['review'] is None for item in detail['items'])
        for invalid in ({}, {'confirm': False}, {'confirm': 1}, {'confirm': True, 'extra': 1}):
            assert request(port, 'POST', detail_path + '/stop', invalid)[0] == 400
        assert {item['execution']['state'] for item in request(port, 'GET', detail_path)[1]['items']} == {'queued'}
        stopped = request(port, 'POST', detail_path + '/stop', {'confirm': True})
        assert stopped[0] == 200
        assert {item['execution']['state'] for item in stopped[1]['items']} == {'cancelled'}
        assert request(port, 'PATCH', '/api/workbench/cli-settings', {'enabled': False})[0] == 200
        assert request(port, 'POST', path, payload) == (202, receipt)
        assert request(port, 'POST', path, {**payload, 'tasks': payload['tasks'][:1]})[0] == 400
    with running(tmp_path) as port:
        assert request(port, 'GET', path)[1] == [receipt]
        assert request(port, 'POST', path, payload) == (202, receipt)
        assert {item['execution']['state'] for item in request(port, 'GET', detail_path)[1]['items']} == {'cancelled'}
        assert request(port, 'POST', detail_path + '/stop', {'confirm': True})[0] == 200
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM project_execution_batches').fetchone()[0] == 1


def test_batch_rollback_and_stop_preserves_running_uncertainty(tmp_path, monkeypatch):
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    store, plan, payload = project(tmp_path)
    path = '/api/workbench/collaboration-plans/' + plan['id'] + '/executions'
    with running(tmp_path) as port:
        configure(port, tmp_path, monkeypatch)
        invalid = {'request_id': 'bad-version', 'tasks': [payload['tasks'][0], {**payload['tasks'][1], 'expected_version': 2}]}
        assert request(port, 'POST', path, invalid)[0] == 409
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0
        _, receipt = request(port, 'POST', path, payload)
        execution = next(row['execution_id'] for row in receipt['tasks'] if row['task_id'] == plan['task_ids']['a'])
        assert Executions(store).claim(execution)  # State transition only; no process exists in this fixture.
        detail_path = '/api/workbench/project-executions/' + receipt['id']
        stopped = request(port, 'POST', detail_path + '/stop', {'confirm': True})[1]
        assert {item['execution']['state'] for item in stopped['items']} == {'stopping', 'cancelled'}
        assert all(item['execution']['exit_code'] is None for item in stopped['items'])
    with running(tmp_path) as port:
        restored = request(port, 'GET', detail_path)[1]
        assert {item['execution']['state'] for item in restored['items']} == {'unknown', 'cancelled'}
        assert request(port, 'POST', path, payload) == (202, receipt)
        stopped = request(port, 'POST', detail_path + '/stop', {'confirm': True})[1]
        assert {item['execution']['state'] for item in stopped['items']} == {'unknown', 'cancelled'}
