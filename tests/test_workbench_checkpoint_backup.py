"""Offline recovery retains checkpoint identity and exact approved artifact input."""
from test_workbench_checkpoint_api import interrupted
from workbench.checkpoints import Checkpoints
from workbench.project_executions import ProjectExecutions
from workbench.runs import Runs
from workbench.store import Store
from workbench.backup import backup, restore


def test_checkpoint_binding_survives_backup_restore(tmp_path):
    source = tmp_path / 'source'
    store, batches, original, first, _ = interrupted(source)
    Runs(store)
    checkpoints = Checkpoints(store, batches)
    preview = checkpoints.preview(original['id'])
    payload = {'request_id': 'backup-resume', 'checkpoint_fingerprint': preview['fingerprint'],
               'reconciliation_note': 'Inspected original effects before recovering', 'confirm': True}
    receipt = checkpoints.recover(original['id'], payload)
    backup(source, tmp_path / 'backup')
    restore(tmp_path / 'backup', tmp_path / 'restored')
    restored_store = Store(tmp_path / 'restored')
    restored_batches = ProjectExecutions(restored_store)
    restored = Checkpoints(restored_store, restored_batches)
    assert restored.recover(original['id'], payload) == receipt
    new_id = receipt['batch']['tasks'][0]['execution_id']
    assert restored_batches.executions.claim(new_id)
    snapshot = restored_batches.executions.snapshot(new_id, include_artifacts=True)
    assert snapshot['dependency_inputs'][0]['upstream_execution_id'] == first
    assert [item['data'] for item in snapshot['input_artifacts']] == [b'approved checkpoint']
    assert batches.executions.get(new_id)['state'] == 'queued'
