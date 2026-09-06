"""Execution receipt survives the actual offline backup and restore workflow."""
from test_workbench_executions import setup, request
from workbench.backup import backup, restore
from workbench.runs import Runs
from workbench.executions import Executions
from workbench.store import Store


def test_cli_receipt_backup_roundtrip(tmp_path):
    source = tmp_path / 'source'
    store, _, executions, task = setup(source)
    Runs(store)
    run = executions.create(task['id'], request())
    executions.claim(run['id'])
    usage = {'input_tokens': 7, 'output_tokens': None, 'cached_input_tokens': 0}
    executions.record_usage(run['id'], run['attempt'], run['requirement_version'], usage)
    executions.report(run['id'], run['attempt'], run['requirement_version'], 1, 'Failed after tokens were used')
    expected = executions.get(run['id'])
    backup(source, tmp_path / 'backup')
    restore(tmp_path / 'backup', tmp_path / 'restored')
    reopened = Executions(Store(tmp_path / 'restored'))
    assert reopened.get(run['id']) == expected
    assert reopened.get(run['id'])['usage'] == usage
    assert reopened.get(run['id'])['state'] == 'failed'
