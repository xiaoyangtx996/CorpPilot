"""Owner HTTP proves missing tool grants never dispatch either configured runner."""
import pytest

from test_workbench_api import request, running
from test_workbench_executions import setup, request as payload
from test_workbench_execution_api import configure, until
from test_workbench_cli_controller import result
from workbench import cli_controller
from workbench.cli_settings import CLISettings
from test_workbench_cli_controller import fixture, until as tick_until
from threading import Event


@pytest.mark.parametrize('backend', ['local', 'docker'])
@pytest.mark.parametrize('missing', ['read', 'write', 'execute'])
def test_http_missing_grant_rejects_without_dispatch(tmp_path, monkeypatch, backend, missing):
    store, _, _, task = setup(tmp_path)
    configure(monkeypatch)
    resolve = CLISettings.resolve
    monkeypatch.setattr(CLISettings, 'resolve', lambda self: resolve(self) | {
        'backend': backend, 'docker_executable': 'C:/fixture/docker.exe',
        'docker_image': 'sha256:' + 'a' * 64, 'docker_cpus': 1,
        'docker_memory_mb': 512, 'docker_pids_limit': 64})
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: calls.append('local') or result())
    monkeypatch.setattr(cli_controller, 'run_docker', lambda **kw: calls.append('docker') or result())
    store.save_agent({'tools': [v for v in ['read', 'write', 'execute'] if v != missing]}, task['agent_id'])
    route = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        status, body = request(port, 'POST', route, payload())
        assert status == 403 and missing in body['error']
        assert request(port, 'GET', route) == (200, [])
        assert calls == []
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM budget_reservations').fetchone()[0] == 0


@pytest.mark.parametrize('missing', ['read', 'write'])
def test_http_original_receipt_survives_tool_revocation_and_restart(tmp_path, monkeypatch, missing):
    store, _, _, task = setup(tmp_path)
    config = configure(monkeypatch)
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: calls.append(kw) or result())
    route = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        status, created = request(port, 'POST', route, payload())
        assert status == 202
        detail = '/api/workbench/executions/' + created['id']
        original = until(port, detail, {'awaiting_review'})
        store.save_agent({'tools': [v for v in ['read', 'write', 'execute'] if v != missing]}, task['agent_id'])
        assert request(port, 'POST', route, payload()) == (202, original)
        assert request(port, 'GET', detail) == (200, original)
        assert request(port, 'POST', route, payload(request_id='new', note='Checked', previous=created['id']))[0] == 403
    config['enabled'] = False
    with running(tmp_path) as port:
        assert request(port, 'POST', route, payload()) == (202, original)
        assert request(port, 'GET', detail) == (200, original)
        assert len(calls) == 1


@pytest.mark.parametrize('backend', ['local', 'docker'])
@pytest.mark.parametrize('missing', ['read', 'write'])
def test_running_revocation_requests_cancel_and_retains_observed_exit(tmp_path, monkeypatch, backend, missing):
    store, _, controller, tasks, runs = fixture(tmp_path, monkeypatch)
    config = controller.settings.resolve() | {
        'backend': backend, 'docker_executable': 'C:/fixture/docker.exe',
        'docker_image': 'sha256:' + 'a' * 64, 'docker_cpus': 1,
        'docker_memory_mb': 512, 'docker_pids_limit': 64}
    monkeypatch.setattr(controller.settings, 'resolve', lambda: config)
    entered, cancelled = Event(), Event()

    def runner(**kwargs):
        entered.set()
        assert kwargs['cancel'].wait(4)
        cancelled.set()
        return result(19, False, 'cancelled')

    monkeypatch.setattr(cli_controller, 'run_codex', runner if backend == 'local' else lambda **kw: pytest.fail('wrong backend'))
    monkeypatch.setattr(cli_controller, 'run_docker', runner if backend == 'docker' else lambda **kw: pytest.fail('wrong backend'))
    try:
        tick_until(controller, entered.is_set)
        store.save_agent({'tools': [v for v in ['read', 'write', 'execute'] if v != missing]}, tasks[0]['agent_id'])
        # Revocation sets the runner's cancel event; unlike Owner cancel it need not write stopping.
        tick_until(controller, lambda: controller.executions.get(runs[0]['id'])['state'] == 'failed')
        assert cancelled.is_set()
        assert controller.executions.get(runs[0]['id'])['exit_code'] == 19
    finally:
        controller.close()
