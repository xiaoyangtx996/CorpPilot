"""Recovering a failed goal batch never changes the original goal's stop target."""
import pytest

from test_workbench_goal_executions import goal, complete, counts


def test_original_goal_stop_does_not_cancel_recovery_and_closed_replays(goal, monkeypatch):
    store, service, source, payload, proposal = goal
    original = service.create(source, payload)
    complete(service, original, proposal)
    service.tick()
    original = service.get(original['id'])
    batch = original['launch']['batch']
    prior = batch['tasks'][0]['execution_id']
    executions = service.cli.executions
    assert executions.claim(prior)
    executions.report(prior, 1, 1, 1, 'Failed original; effects inspected')
    preview = service.cli.checkpoints.preview(batch['id'])
    request = {'request_id': 'goal-recovery', 'checkpoint_fingerprint': preview['fingerprint'],
               'reconciliation_note': 'Original process stopped and effects checked', 'confirm': True}
    recovered = service.cli.recover_checkpoint(batch['id'], request)
    identity = recovered['batch']['tasks'][0]['execution_id']
    assert executions.claim(identity)
    before = counts(store)
    service.stop_goal(original['id'], {'confirm': True})
    service.tick()
    assert executions.get(identity)['state'] == 'running'
    assert counts(store) == before
    assert service.get(original['id'])['launch']['batch']['id'] == batch['id']
    with monkeypatch.context() as patch:
        patch.setattr(service.cli, 'closed', True)
        assert service.cli.recover_checkpoint(batch['id'], request) == recovered
        with pytest.raises(ValueError):
            service.cli.recover_checkpoint(batch['id'], {**request, 'request_id': 'different'})
    assert counts(store) == before
    service.cli.stop_project(recovered['batch_id'])
    assert executions.get(identity)['state'] == 'stopping'
