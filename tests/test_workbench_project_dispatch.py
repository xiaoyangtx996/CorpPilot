"""Real controller/thread-pool integration with a controlled, non-paid CLI runner."""
from threading import Event, Lock

from test_workbench_collaboration import setup
from test_workbench_cli_controller import ready, until, result
from workbench import cli_controller, artifacts
from workbench.cli_controller import CLIController
from workbench.reviews import Reviews


def test_once_admitted_parallel_branches_wait_for_owner_approval(tmp_path, monkeypatch):
    store, plans, source, payload = setup(tmp_path)
    payload['tasks'].append({**payload['tasks'][0], 'key': 'c', 'title': 'independent'})
    store.save_agent({'tools': ['read', 'write', 'execute']}, payload['tasks'][0]['agent_id'])
    plan = plans.create(source, payload)
    controller = CLIController(store)
    ready(monkeypatch, controller, concurrency=2)
    request = {'request_id': 'once', 'tasks': [dict(task_id=value, expected_version=1,
               previous_execution_id=None, reconciliation_note='') for value in plan['task_ids'].values()]}
    receipt = controller.enqueue_project(plan['id'], request)
    runs = {key: next(item['execution_id'] for item in receipt['tasks'] if item['task_id'] == task)
            for key, task in plan['task_ids'].items()}
    release = {identity: Event() for identity in runs.values()}
    calls, lock = [], Lock()

    def runner(**kwargs):
        identity = kwargs['execution_id']
        with lock:
            calls.append(kwargs)
        assert release[identity].wait(10)
        return result()

    monkeypatch.setattr(cli_controller, 'run_codex', runner)
    monkeypatch.setattr(cli_controller, 'capture', lambda *args: [{'path': 'proof.txt', 'data': b'approved upstream evidence'}])
    try:
        until(controller, lambda: len(calls) == 2)
        assert {call['execution_id'] for call in calls} == {runs['a'], runs['c']}
        assert controller.executions.get(runs['b'])['state'] == 'queued'
        assert controller.status()['active_requests'] == 2
        release[runs['a']].set()
        until(controller, lambda: controller.executions.get(runs['a'])['state'] == 'awaiting_review')
        for _ in range(3):
            controller.tick()
        assert len(calls) == 2  # Exit zero alone cannot release B.
        Reviews(store).save(runs['a'], dict(request_id='owner-approval', expected_version=1,
            decision='approved', note='Owner inspected the exported evidence',
            artifact_ids=[item['id'] for item in artifacts.list_for(store, runs['a'])]))
        until(controller, lambda: len(calls) == 3)
        assert controller.executions.get(runs['b'])['state'] == 'running'
        assert controller.executions.get(runs['c'])['state'] == 'running'
        downstream = next(call for call in calls if call['execution_id'] == runs['b'])
        assert len(downstream['input_artifacts']) == 1
        assert downstream['input_artifacts'][0]['execution_id'] == runs['a']
        assert downstream['input_artifacts'][0]['data'] == b'approved upstream evidence'
        for event in release.values():
            event.set()
        until(controller, lambda: all(controller.executions.get(identity)['state'] == 'awaiting_review' for identity in runs.values()))
        for _ in range(3):
            controller.tick()
        assert len(calls) == len({call['execution_id'] for call in calls}) == 3
        detail = controller.project_executions.get(receipt['id'])
        assert sum(item['review'] is not None for item in detail['items']) == 1
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM project_execution_batches').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 3
    finally:
        for event in release.values():
            event.set()
        controller.close()
