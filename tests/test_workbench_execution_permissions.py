"""CLI dispatch needs reading and artifact writing without rewriting historical authority."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_workbench_executions import setup, request
from workbench import artifacts
from workbench.executions import Executions
from workbench.reviews import Reviews
from workbench.store import Store


def revoke(store, task, permission):
    store.save_agent({'tools': [p for p in ('read', 'write', 'execute') if p != permission]}, task['agent_id'])


@pytest.mark.parametrize('permission', ['read', 'write', 'execute'])
def test_missing_permission_rejects_new_dispatch_without_partial_state(tmp_path, permission):
    store, _, executions, task = setup(tmp_path)
    revoke(store, task, permission)
    before = store.agent(task['agent_id'])
    with pytest.raises(PermissionError, match=permission):
        executions.create(task['id'], request())
    assert executions.list(task['id']) == []
    assert store.agent(task['agent_id']) == before


@pytest.mark.parametrize('permission', ['read', 'write', 'execute'])
def test_queued_replay_is_read_only_but_claim_rechecks_permissions(tmp_path, permission):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task['id'], request())
    revoke(store, task, permission)
    assert executions.create(task['id'], request()) == run
    with pytest.raises(ValueError, match='不同任务执行'):
        executions.create(task['id'], request(note='different payload'))
    assert not executions.claim(run['id'])
    assert executions.get(run['id'])['state'] == 'failed'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM skill_input_snapshots').fetchone()[0] == 0


@pytest.mark.parametrize('permission', ['read', 'write', 'execute'])
@pytest.mark.parametrize('include_artifacts', [False, True])
def test_running_snapshot_rechecks_before_cli_input(tmp_path, permission, include_artifacts):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task['id'], request())
    assert executions.claim(run['id'])
    revoke(store, task, permission)
    with pytest.raises(PermissionError, match=permission):
        executions.snapshot(run['id'], include_artifacts=include_artifacts)
    assert executions.get(run['id'])['state'] == 'running'


@pytest.mark.parametrize('permission', ['read', 'write'])
@pytest.mark.parametrize('revoke_before_report', [False, True])
def test_new_read_write_gate_does_not_block_report_or_owner_artifact_review(tmp_path, permission, revoke_before_report):
    store, _, executions, task = setup(tmp_path)
    reviews = Reviews(store)
    run = executions.create(task['id'], request())
    assert executions.claim(run['id'])
    if revoke_before_report:
        revoke(store, task, permission)
    result = executions.report(run['id'], 1, 1, 0, 'Observed successful exit', success=True,
                               artifacts=[{'path': 'report.txt', 'data': b'approved deliverable'}])
    assert result['state'] == 'awaiting_review'
    if not revoke_before_report:
        revoke(store, task, permission)
    listed = artifacts.list_for(store, run['id'])
    payload = {'request_id': 'review', 'expected_version': 1, 'decision': 'approved',
               'note': 'Owner inspected original artifact', 'artifact_ids': [item['id'] for item in listed]}
    decision = reviews.save(run['id'], payload)
    assert decision['decision'] == 'approved'
    # Even full later revocation cannot erase a recorded response or repeat its side effects.
    store.save_agent({'tools': []}, task['agent_id'])
    assert reviews.save(run['id'], payload) == decision
    assert reviews.get(run['id']) == decision
    assert executions.report(run['id'], 1, 1, 9, 'late callback') == result
    reopened = Executions(Store(tmp_path))
    assert reopened.get(run['id']) == result
    assert reopened.create(task['id'], request()) == result
    assert artifacts.get(store, listed[0]['id'])['data'] == b'approved deliverable'


def test_existing_execute_revocation_report_rule_is_unchanged(tmp_path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task['id'], request())
    assert executions.claim(run['id'])
    revoke(store, task, 'execute')
    assert executions.report(run['id'], 1, 1, 0, 'Observed exit', success=True)['state'] == 'failed'
