"""Explicit fixed-batch handoffs are not Owner reviews or retry permissions."""
import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_project_launch import fixture, counts
from test_workbench_executions import request, revise
from test_workbench_cli_controller import ready, until, result
from workbench import artifacts, cli_controller, dependencies
from workbench.cli import prepare_workspace
from workbench.cli_controller import CLIController
from workbench.project_launches import ProjectLaunches
from workbench.reviews import Reviews
from workbench.store import Store


def launch_chain(tmp_path, consent=True):
    store, _, source, launches, payload = fixture(tmp_path)
    payload['plan']['tasks'].append({**payload['plan']['tasks'][1], 'key': 'c', 'title': 'third', 'depends_on': ['b']})
    if consent is not None:
        payload['confirm_handoff'] = consent
    receipt = launches.create(source, payload)
    runs = [item['execution_id'] for item in receipt['batch']['tasks']]
    return store, launches, source, payload, receipt, runs


def finish(executions, identity, data=b'captured work'):
    assert executions.claim(identity)
    run = executions.get(identity)
    return executions.report(identity, run['attempt'], run['requirement_version'], 0, 'Captured result', success=True,
                             artifacts=[] if data is None else [{'path': 'result.txt', 'data': data}])


def review(store, identity, decision='approved'):
    return Reviews(store).save(identity, {'request_id': 'owner-' + decision, 'expected_version': 1,
        'decision': decision, 'note': 'Explicitly inspected captured result',
        'artifact_ids': [a['id'] for a in artifacts.list_for(store, identity)]})


def test_fixed_three_step_handoff_without_fabricated_reviews(tmp_path):
    store, launches, source, payload, receipt, (a, b, c) = launch_chain(tmp_path)
    executions = launches.batches.executions
    assert not executions.claim(b)
    finish(executions, a, b'first exact bytes')
    assert launches.batches.get(receipt['batch']['id'])['items'][1]['dependencies']['ready']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: executions.claim(b), range(4))) == 1
    snapshot = executions.snapshot(b, include_artifacts=True)
    assert [i['data'] for i in snapshot['input_artifacts']] == [b'first exact bytes']
    assert snapshot['dependency_inputs'] == [{'dependency_task_id': executions.get(a)['task_id'], 'upstream_execution_id': a}]
    executions.report(b, 1, 1, 0, 'second', success=True, artifacts=[{'path': 'second.txt', 'data': b'second bytes'}])
    finish(executions, c)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM execution_reviews').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM execution_handoff_permissions').fetchone()[0] == 2
        assert dependencies.bound_inputs(db, c)[0]['upstream_execution_id'] == b
        # Memory/retrospective calls do not supply a downstream execution identity.
        with pytest.raises(ValueError):
            artifacts.input_snapshots(db, dependencies.bound_inputs(db, b))
        with pytest.raises(ValueError):
            artifacts.input_snapshots(db, [{'upstream_execution_id': a}])
    review(store, c)
    assert Reviews(store).get(c)['decision'] == 'approved'
    assert Reviews(store).get(a) is None and Reviews(store).get(b) is None
    restored = ProjectLaunches(Store(tmp_path))
    assert restored.create(source, payload) == receipt
    assert restored.batches.executions.claim(c) is False


@pytest.mark.parametrize('consent', [None, False])
def test_default_and_false_keep_intermediate_owner_review(tmp_path, consent):
    store, launches, _, _, _, (a, b, _) = launch_chain(tmp_path, consent)
    finish(launches.batches.executions, a)
    assert not launches.batches.executions.claim(b)
    review(store, a)
    assert launches.batches.executions.claim(b)
    assert launches.batches.executions.snapshot(b, include_artifacts=True)['input_artifacts']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM execution_handoff_permissions').fetchone()[0] == 0


@pytest.mark.parametrize('value', [None, 1, 0, 'true', [], {}])
def test_strict_consent_and_immutable_authority(tmp_path, value):
    store, _, source, launches, payload = fixture(tmp_path)
    baseline = counts(store)
    with pytest.raises(ValueError):
        launches.create(source, {**payload, 'confirm_handoff': value})
    assert counts(store) == baseline
    original = launches.create(source, payload)
    with pytest.raises(ValueError):
        launches.create(source, {**payload, 'confirm_handoff': True})
    assert launches.get(original['id']) == original


@pytest.mark.parametrize('change', ['version', 'new-approved-attempt', 'rejected', 'empty', 'failed', 'unknown', 'cancelled'])
def test_changed_or_unusable_upstream_never_crosses_handoff(tmp_path, change):
    store, launches, _, _, _, (a, b, _) = launch_chain(tmp_path)
    ex = launches.batches.executions
    if change in ('failed', 'unknown', 'cancelled'):
        assert ex.claim(a)
        if change == 'cancelled':
            ex.cancel(a)
        ex.report(a, 1, 1, None if change == 'unknown' else 1, 'not deliverable', success=False)
    else:
        finish(ex, a, None if change == 'empty' else b'result')
        if change == 'version':
            revise(ex.tasks, ex.tasks.get(ex.get(a)['task_id']))
        if change == 'new-approved-attempt':
            replacement = ex.create(ex.get(a)['task_id'], request(request_id='replacement', previous=a, note='Checked prior effects'))
            finish(ex, replacement['id'])
            review(store, replacement['id'])
        if change == 'rejected':
            review(store, a, 'rejected')
    assert not ex.claim(b)
    assert ex.get(b)['state'] == 'queued'


def test_rejection_invalidates_running_descendant_and_final_review(tmp_path):
    store, launches, _, _, _, (a, b, c) = launch_chain(tmp_path)
    ex = launches.batches.executions
    finish(ex, a)
    finish(ex, b)
    assert ex.claim(c)
    review(store, a, 'rejected')
    with pytest.raises(ValueError):
        ex.snapshot(c, include_artifacts=True)
    assert ex.report(c, 1, 1, 0, 'Late result', success=True)['state'] == 'failed'
    with pytest.raises(ValueError):
        review(store, b)
    assert Reviews(store).get(b) is None


def test_retry_and_other_run_cannot_borrow_handoff_permission(tmp_path):
    store, launches, _, _, receipt, (a, b, _) = launch_chain(tmp_path)
    ex = launches.batches.executions
    finish(ex, a)
    assert ex.claim(b)
    with store.connect() as db:
        inputs = dependencies.bound_inputs(db, b)
        with pytest.raises(ValueError):
            artifacts.input_snapshots(db, inputs, downstream_execution_id=a)
    ex.cancel(b)
    ex.report(b, 1, 1, 130, 'Stopped', success=False)
    retry = ex.create(ex.get(b)['task_id'], request(request_id='new-b', previous=b, note='Checked effects'))
    assert not ex.claim(retry['id'])
    # The original immutable launch is not authority for replacement attempts.
    assert launches.batches.get(receipt['batch']['id'])['items'][1]['execution']['id'] == b


def test_corrupt_artifact_bytes_not_returned_under_handoff(tmp_path):
    store, launches, _, _, _, (a, b, _) = launch_chain(tmp_path)
    ex = launches.batches.executions
    finish(ex, a)
    assert ex.claim(b)
    with store.connect() as db:
        db.execute('DROP TRIGGER artifacts_no_update')
        db.execute("UPDATE execution_artifacts SET sha256='corrupt' WHERE execution_id=?", (a,))
    with pytest.raises(ValueError):
        ex.snapshot(b, include_artifacts=True)


def test_handoff_permission_failure_rolls_back_launch_and_is_immutable(tmp_path):
    store, _, source, launches, payload = fixture(tmp_path)
    payload['confirm_handoff'] = True
    baseline = counts(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER fault BEFORE INSERT ON execution_handoff_permissions BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        launches.create(source, payload)
    assert counts(store) == baseline
    with store.connect() as db:
        db.execute('DROP TRIGGER fault')
    launches.create(source, payload)
    with store.connect() as db:
        for sql in ('DELETE FROM execution_handoff_permissions', 'UPDATE execution_handoff_permissions SET launch_id=launch_id',
                    'INSERT OR REPLACE INTO execution_handoff_permissions SELECT * FROM execution_handoff_permissions'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


def test_upgrade_keeps_old_launch_unprivileged_and_accepts_new_explicit_authority(tmp_path):
    store, launches, source, payload, receipt, (a, b, _) = launch_chain(tmp_path, None)
    before = counts(store)
    with store.connect() as db:
        db.execute('DROP TABLE execution_handoff_permissions')
    restored = ProjectLaunches(Store(tmp_path))
    assert restored.get(receipt['id']) == receipt
    assert counts(store) == before
    finish(restored.batches.executions, a)
    assert not restored.batches.executions.claim(b)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM execution_handoff_permissions').fetchone()[0] == 0
    new_payload = copy.deepcopy(payload)
    new_payload['plan']['request_id'] = 'new-explicit-authority'
    new_payload['confirm_handoff'] = True
    new_receipt = restored.create(source, new_payload)
    assert new_receipt['id'] != receipt['id']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM execution_handoff_permissions').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Actual Windows artifact capture')
def test_controller_runs_complete_chain_with_real_captured_inputs(tmp_path, monkeypatch):
    store, _, source, _, payload = fixture(tmp_path)
    payload['confirm_handoff'] = True
    payload['plan']['tasks'].append({**payload['plan']['tasks'][1], 'key': 'c', 'title': 'third', 'depends_on': ['b']})
    controller = CLIController(store)
    ready(monkeypatch, controller)
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        paths = prepare_workspace(store.data_dir, kwargs['execution_id'], kwargs['input_artifacts'])
        directory = paths['work'] / 'artifacts'
        directory.mkdir(exist_ok=True)
        (directory / 'result.txt').write_bytes(b'captured-' + str(len(calls)).encode())
        return result()
    monkeypatch.setattr(cli_controller, 'run_codex', runner)
    try:
        receipt = controller.launch_project(source, payload)
        ids = [i['execution_id'] for i in receipt['batch']['tasks']]
        until(controller, lambda: all(controller.executions.get(i)['state'] == 'awaiting_review' for i in ids))
        assert [c['execution_id'] for c in calls] == ids
        assert [len(c['input_artifacts']) for c in calls] == [0, 1, 1]
        assert calls[1]['input_artifacts'][0]['data'] == b'captured-1'
        assert calls[2]['input_artifacts'][0]['data'] == b'captured-2'
        assert all('PRIVATE ORIGINAL' not in c['prompt'] for c in calls)
        assert controller.launch_project(source, copy.deepcopy(payload)) == receipt
        for _ in range(3):
            controller.tick()
        assert len(calls) == 3
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM execution_reviews').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM execution_artifacts').fetchone()[0] == 3
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        controller.close()
