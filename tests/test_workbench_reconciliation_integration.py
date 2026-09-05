"""Owner reconciliation restores admission without inventing execution success."""
from concurrent.futures import Future
from threading import Event

import pytest

from test_workbench_api import running, request as http
from test_workbench_executions import setup, request
from test_workbench_dependencies import sibling
from test_workbench_cli_controller import ready, until, result
from workbench import cli_controller
from workbench.cli_controller import CLIController


def acknowledgement(run):
    return dict(request_id='owner-check', attempt=run['attempt'],
                requirement_version=run['requirement_version'], process_stopped=True,
                external_effects_checked=True, note='Owner checked stopped process and external effects')


def make_unknown(executions, task):
    run = executions.create(task['id'], request())
    assert executions.claim(run['id'])
    return executions.report(run['id'], run['attempt'], run['requirement_version'], None,
                             'Injected unknown result; no real CLI invoked')


def test_reconciled_unknown_preserved_while_previously_queued_work_resumes(tmp_path, monkeypatch):
    store, tasks, executions, task = setup(tmp_path)
    controller = CLIController(store)
    ready(monkeypatch, controller)
    unknown = make_unknown(executions, task)
    queued_task = sibling(tasks, task, 'already-authorized')
    queued = executions.create(queued_task['id'], request())
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: calls.append(kw['execution_id']) or result(1, False))
    try:
        controller.tick()
        assert not calls and executions.get(queued['id'])['state'] == 'queued'
        saved = controller.reconcile_unknown(unknown['id'], acknowledgement(unknown))
        assert executions.get(unknown['id']) == unknown
        until(controller, lambda: executions.get(queued['id'])['state'] == 'failed')
        assert calls == [queued['id']]
        assert controller.reconcile_unknown(unknown['id'], acknowledgement(unknown)) == saved
        # Resolving uncertainty does not create a replacement or a successful artifact.
        assert executions.list(task['id']) == [unknown]
        with pytest.raises(ValueError):
            executions.create(task['id'], request(request_id='new'))
        retry = executions.create(task['id'], request(request_id='new', previous=unknown['id'], note='Explicit new attempt'))
        assert retry['attempt'] == 2 and retry['state'] == 'queued'
        assert executions.report(unknown['id'], 1, 1, 0, 'Late success', success=True) == unknown
    finally:
        controller.close()


def test_controller_owned_execution_cannot_be_manually_reconciled(tmp_path):
    store, _, executions, task = setup(tmp_path)
    controller = CLIController(store)
    run = make_unknown(executions, task)
    future = Future()
    controller.active[run['id']] = (run, Event(), future)
    try:
        with pytest.raises(ValueError, match='控制器持有'):
            controller.reconcile_unknown(run['id'], acknowledgement(run))
        assert controller.reconciliations.get(run['id']) is None
    finally:
        future.set_result(dict(exit_code=None, summary='Unknown remains', success=False))
        controller.close()
    with pytest.raises(ValueError, match='关闭'):
        controller.reconcile_unknown(run['id'], acknowledgement(run))


def test_http_reconciliation_restart_and_exact_identity(tmp_path):
    _, _, executions, task = setup(tmp_path)
    run = make_unknown(executions, task)
    path = '/api/workbench/executions/' + run['id'] + '/reconciliation'
    payload = acknowledgement(run)
    with running(tmp_path) as port:
        assert http(port, 'GET', path) == (200, None)
        assert http(port, 'GET', '/api/workbench/execution-reconciliations/pending')[1] == [run]
        # Reject at headers before accepting a body. Sending a body after an early
        # rejection can yield Windows TCP reset rather than the HTTP error.
        status, denied = http(port, 'POST', path, headers={'Origin': 'https://foreign.example'})
        assert status == 400 and '跨来源' in denied['error']
        assert http(port, 'GET', path) == (200, None)
        assert http(port, 'POST', path, {**payload, 'attempt': 2})[0] == 409
        status, saved = http(port, 'POST', path, payload)
        assert status == 201
        assert http(port, 'GET', '/api/workbench/execution-reconciliations/pending') == (200, [])
        assert http(port, 'GET', '/api/workbench/executions/' + run['id']) == (200, run)
    with running(tmp_path) as port:
        assert http(port, 'GET', path) == (200, saved)
        assert http(port, 'POST', path, payload) == (201, saved)
        assert http(port, 'POST', path, {**payload, 'note': 'different'})[0] == 400
