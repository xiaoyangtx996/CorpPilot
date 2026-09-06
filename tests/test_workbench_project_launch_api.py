"""One Owner HTTP action creates and admits a project, with recoverable receipts."""
import copy
from concurrent.futures import ThreadPoolExecutor

from test_workbench_api import running, request
from test_workbench_collaboration import setup, counts
from test_workbench_project_execution_api import configure
from test_workbench_controller import until
from test_workbench_cli_controller import result
from workbench import cli_controller
from workbench.cli_controller import CLIController
from workbench.executions import Executions


def launch_input(tmp_path):
    store, plans, source, draft = setup(tmp_path)
    for actor in {task['agent_id'] for task in draft['tasks']}:
        store.save_agent({'tools': ['read', 'write', 'execute']}, actor)
    return store, plans, source, {'plan': draft, 'confirm_execution': True}


def test_launch_http_one_confirmation_concurrent_replay_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    store, _, source, payload = launch_input(tmp_path)
    path = f'/api/workbench/conversations/{source}/project-launches'
    with running(tmp_path) as port:
        baseline = counts(store)
        assert request(port, 'POST', path, payload, headers={'Authorization': ''})[0] == 401
        assert request(port, 'GET', path, headers={'Authorization': ''})[0] == 401
        assert request(port, 'POST', path, payload)[0] == 400
        assert counts(store) == baseline
        configure(port, tmp_path, monkeypatch)
        for bad in ({'plan': payload['plan']}, {**payload, 'confirm_execution': False},
                    {**payload, 'confirm_execution': 1}, {**payload, 'extra': True}):
            assert request(port, 'POST', path, bad)[0] == 400
        assert counts(store) == baseline
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: request(port, 'POST', path, payload), range(8)))
        assert all(status == 202 for status, _ in responses)
        assert len({row['id'] for _, row in responses}) == 1
        receipt = responses[0][1]
        assert all(row == receipt for _, row in responses)
        assert receipt['request_payload'] == payload
        plan, batch = receipt['collaboration'], receipt['batch']
        assert batch['collaboration_id'] == plan['id']
        assert {r['task_id'] for r in batch['tasks']} == set(plan['task_ids'].values())
        assert len(batch['tasks']) == 2
        messages = store.messages(plan['project_conversation_id'])
        assert len(messages) == 1 and messages[0]['content'] == 'EXPLICIT SHARED'
        assert 'PRIVATE ORIGINAL' not in str(messages)
        detail = f"/api/workbench/project-launches/{receipt['id']}"
        assert request(port, 'GET', detail) == (200, receipt)
        assert request(port, 'GET', detail, headers={'Authorization': ''})[0] == 401
        assert request(port, 'GET', path) == (200, [receipt])
        changed = copy.deepcopy(payload)
        changed['plan']['shared_brief'] = 'Different authority'
        assert request(port, 'POST', path, changed)[0] == 400
        stop = f"/api/workbench/project-executions/{batch['id']}/stop"
        assert {x['execution']['state'] for x in request(port, 'POST', stop, {'confirm': True})[1]['items']} == {'cancelled'}
        request(port, 'PATCH', '/api/workbench/cli-settings', {'enabled': False})
        store.save_conversation({'archived': True}, source)
        store.save_agent({'enabled': False}, payload['plan']['coordinator_id'])
        assert request(port, 'POST', path, payload) == (202, receipt)
    with running(tmp_path) as port:
        assert request(port, 'POST', path, payload) == (202, receipt)
        assert request(port, 'GET', detail) == (200, receipt)
        assert request(port, 'GET', path) == (200, [receipt])
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM project_execution_batches').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM collaboration_receipts').fetchone()[0] == 1


def test_launch_http_second_execution_failure_rolls_back_entire_project(tmp_path, monkeypatch):
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    store, plans, source, payload = launch_input(tmp_path)
    path = f'/api/workbench/conversations/{source}/project-launches'
    with running(tmp_path) as port:
        configure(port, tmp_path, monkeypatch)
        baseline = counts(store)
        with store.connect() as db:
            db.execute("""CREATE TRIGGER qa_second_execution BEFORE INSERT ON task_executions
                WHEN (SELECT count(*) FROM task_executions)>0
                BEGIN SELECT RAISE(ABORT,'injected second execution failure'); END""")
        assert request(port, 'POST', path, payload)[0] == 500
        assert counts(store) == baseline
        assert request(port, 'GET', path) == (200, [])
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM project_execution_batches').fetchone()[0] == 0
            db.execute('DROP TRIGGER qa_second_execution')
        # A prior create-only approval must not become execution authority by reusing its key.
        plans.create(source, payload['plan'])
        assert request(port, 'POST', path, payload)[0] == 400
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0


def test_launch_http_dispatches_once_and_retains_intermediate_owner_gate(tmp_path, monkeypatch):
    store, _, source, payload = launch_input(tmp_path)
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: calls.append(kw) or result())
    monkeypatch.setattr(cli_controller, 'capture', lambda *args: [{'path': 'proof.txt', 'data': b'controlled runner evidence'}])
    with running(tmp_path) as port:
        configure(port, tmp_path, monkeypatch)
        path = f'/api/workbench/conversations/{source}/project-launches'
        status, receipt = request(port, 'POST', path, payload)
        assert status == 202
        plan, batch = receipt['collaboration'], receipt['batch']
        bindings = {item['task_id']: item['execution_id'] for item in batch['tasks']}
        upstream, downstream = (bindings[plan['task_ids'][key]] for key in ('a', 'b'))
        executions = Executions(store)
        until(lambda: executions.get(upstream)['state'] == 'awaiting_review')
        assert executions.get(downstream)['state'] == 'queued'
        assert len(calls) == 1 and calls[0]['execution_id'] == upstream
        assert 'EXPLICIT SHARED' in calls[0]['prompt'] and 'PRIVATE ORIGINAL' not in calls[0]['prompt']
        assert request(port, 'POST', path, payload) == (202, receipt)
        # Stop by fixed batch binding; downstream has never entered the runner.
        status, stopped = request(port, 'POST', f"/api/workbench/project-executions/{batch['id']}/stop", {'confirm': True})
        assert status == 200
        assert {item['execution']['state'] for item in stopped['items']} == {'awaiting_review', 'cancelled'}
        assert len(calls) == 1
