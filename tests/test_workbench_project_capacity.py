"""Batch capacity boundaries through the existing task execution admission path."""
import pytest

from test_workbench_collaboration import setup
from test_workbench_project_executions import fixture
from workbench.project_executions import ProjectExecutions


def test_capacity_99_plus_two_rolls_back_entire_batch_and_retries_same_key(tmp_path):
    store, _, plan, api, payload = fixture(tmp_path)
    task = api.executions.tasks.get(plan['task_ids']['a'])
    fields = {key: task[key] for key in ('agent_id', 'source_message_id', 'title', 'scope', 'acceptance')}
    existing = []
    for number in range(99):
        other = api.executions.tasks.create(task['conversation_id'], {
            **fields, 'request_id': f'capacity-task-{number}'})
        existing.append(api.executions.create(other['id'], {
            'request_id': 'capacity-run', 'expected_version': 1,
            'previous_execution_id': None, 'reconciliation_note': ''}))

    with pytest.raises(ValueError, match='队列已满'):
        api.create(plan['id'], payload)
    assert {run['id'] for run in api.executions.pending()} == {run['id'] for run in existing}
    assert api.list(plan['id']) == []
    assert all(api.executions.list(item['task_id']) == [] for item in payload['tasks'])

    api.executions.cancel(existing[0]['id'])
    receipt = api.create(plan['id'], payload)
    assert len(receipt['tasks']) == 2
    assert len(api.executions.pending()) == 100
    assert api.create(plan['id'], payload) == receipt
    assert api.list(plan['id']) == [receipt]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 101


def test_sixteen_distinct_plan_tasks_admit_once_with_dependencies_still_gated(tmp_path):
    store, plans, source, draft = setup(tmp_path)
    store.save_agent({'tools': ['read', 'execute']}, draft['tasks'][0]['agent_id'])
    draft['tasks'].extend({**draft['tasks'][0], 'key': f'task-{number}'} for number in range(14))
    plan = plans.create(source, draft)
    api = ProjectExecutions(store)
    payload = {'request_id': 'max-batch', 'tasks': [
        {'task_id': identity, 'expected_version': 1,
         'previous_execution_id': None, 'reconciliation_note': ''}
        for identity in plan['task_ids'].values()]}

    receipt = api.create(plan['id'], payload)
    assert len(receipt['tasks']) == 16
    assert len({item['execution_id'] for item in receipt['tasks']}) == 16
    assert {item['task_id'] for item in receipt['tasks']} == set(plan['task_ids'].values())
    assert api.create(plan['id'], payload) == receipt
    assert len(api.executions.pending()) == 16
    blocked = next(item for item in receipt['tasks'] if item['task_id'] == plan['task_ids']['b'])
    assert not api.executions.claim(blocked['execution_id'])
    assert api.executions.get(blocked['execution_id'])['state'] == 'queued'
