"""Owner HTTP and controller lifecycle with real saved code and native Git.

Source/reviewer CLI adapters are controlled; no paid model or Docker claims.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from urllib.parse import quote

import pytest

from test_workbench_api import running, request as http
from test_workbench_code_review_delivery import approved
from test_workbench_cli_controller import until
from test_workbench_git_checkout import git
from test_workbench_executions import request as execution_request
from workbench import artifacts, cli_controller
from workbench.cli import prepare_workspace
from workbench.cli_controller import CLIController
from workbench.cli_settings import CLISettings
from workbench.reviews import Reviews
from workbench.runs import Runs
from workbench.backup import backup, restore
from workbench.store import Store


def reviewed(tmp_path, monkeypatch):
    values = approved(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    def reviewer(**kw):
        paths = prepare_workspace(store.data_dir, kw['execution_id'], kw['input_artifacts'], kw['repository'])
        (paths['work']/'artifacts').mkdir()
        (paths['work']/'artifacts/review.md').write_bytes(b'Controlled review: checked fixed patch, recommend acceptance.\n')
        return dict(exit_code=0, reason='exited', success=True, summary='Controlled reviewer', usage=None)
    monkeypatch.setattr(cli_controller, 'run_codex', reviewer)
    try:
        preview = c.code_reviews.preview(run['id'])
        receipt = c.enqueue_code_review(run['id'], dict(request_id='review-code', fingerprint=preview['fingerprint'], confirm=True))
        identity = receipt['initial_execution_id']
        until(c, lambda: c.executions.get(identity)['state'] == 'awaiting_review', timeout=60)
        Reviews(store).save(identity, dict(request_id='approve-report', expected_version=1, decision='approved',
            note='Owner accepts complete reviewer report', artifact_ids=[r['id'] for r in artifacts.list_for(store, identity)]))
    except Exception:
        c.close()
        raise
    return values, receipt


def payload(c, room, run, request_id='integrate code', previous=None, note=''):
    preview = c.code_integrations.preview(room['id'], {'source_execution_ids': [run['id']]})
    assert not preview['blockers'], preview
    return dict(request_id=request_id, source_execution_ids=[run['id']], fingerprint=preview['fingerprint'],
                previous_integration_id=previous, reconciliation_note=note, confirm=True)


def test_owner_http_real_integration_replay_restart_and_backup(tmp_path, monkeypatch):
    values, review = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    c.close()
    (source/'code.txt').write_bytes(b'later source\n'); git(source, 'commit', '-qam', 'later')
    (source/'code.txt').write_bytes(b'Owner dirty source\n')
    before = (git(source, 'rev-parse', 'HEAD'), (source/'.git/index').read_bytes(), (source/'.git/config').read_bytes())
    original = cli_controller.integrate_code
    native = []
    def observed(**kw):
        native.append(kw['destination'])
        return original(**kw)
    monkeypatch.setattr(cli_controller, 'integrate_code', observed)
    monkeypatch.setattr(CLISettings, 'resolve', lambda self: pytest.fail('Native integration must not resolve model credentials'))
    prefix = '/api/workbench/conversations/'+room['id']
    with running(store.data_dir) as port:
        with store.connect() as db: dump = list(db.iterdump())
        preview_body = {'source_execution_ids': [run['id']]}
        assert http(port, 'POST', prefix+'/code-integration-preview', preview_body, headers={'Authorization': ''})[0] == 401
        status, preview = http(port, 'POST', prefix+'/code-integration-preview', preview_body)
        assert status == 200 and not preview['blockers']
        with store.connect() as db: assert list(db.iterdump()) == dump
        body = dict(request_id='owner integration', source_execution_ids=[run['id']], fingerprint=preview['fingerprint'],
                    previous_integration_id=None, reconciliation_note='', confirm=True)
        assert http(port, 'POST', prefix+'/code-integrations', body, headers={'Authorization': ''})[0] == 401
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: http(port, 'POST', prefix+'/code-integrations', body), range(2)))
        assert all(status == 202 for status, _ in responses), responses
        identity = responses[0][1]['id']
        assert {r['id'] for _, r in responses} == {identity}
        path = '/api/workbench/code-integrations/'+identity
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            status, final = http(port, 'GET', path)
            if final['state'] not in ('running', 'stopping'): break
            time.sleep(.05)
        assert final['state'] == 'completed', final
        target = store.data_dir / final['destination_relative']
        assert native == [target]
        assert final['result']['base_commit'] == sha
        assert git(target, 'rev-parse', 'HEAD') == final['result']['commit']
        assert (target/'code.txt').read_bytes() == b'changed code\n'
        assert http(port, 'GET', prefix+'/code-integrations/requests/'+quote(body['request_id'], safe='')) == (200, final)
        assert http(port, 'POST', prefix+'/code-integrations', body) == (202, final)
        assert http(port, 'POST', prefix+'/code-integrations', body | {'fingerprint': '0'*64})[0] == 400
        assert http(port, 'GET', prefix+'/code-integrations') == (200, [final])
        assert http(port, 'POST', path+'/stop', {'unexpected': True})[0] == 400
        assert http(port, 'POST', path+'/stop', {}) == (200, final)
        declaration = dict(request_id='not-unknown', process_stopped=True, effects_checked=True, note='Only unknown operations accept declarations')
        assert http(port, 'POST', path+'/reconciliation', declaration, headers={'Authorization': ''})[0] == 401
        assert http(port, 'POST', path+'/reconciliation', declaration)[0] == 400
    with running(store.data_dir) as port:
        assert http(port, 'GET', path) == (200, final)
        assert http(port, 'POST', prefix+'/code-integrations', body) == (202, final)
    backup(store.data_dir, tmp_path/'backup')
    restore(tmp_path/'backup', tmp_path/'restored')
    with running(tmp_path/'restored') as port:
        assert http(port, 'GET', path) == (200, final)
        assert http(port, 'POST', prefix+'/code-integrations', body) == (202, final)
    assert not (tmp_path/'restored'/final['destination_relative']).exists()
    assert len(native) == 1 and calls == [run['id']]
    assert before == (git(source, 'rev-parse', 'HEAD'), (source/'.git/index').read_bytes(), (source/'.git/config').read_bytes())
    assert (source/'code.txt').read_bytes() == b'Owner dirty source\n'


def test_finish_write_failure_retries_only_same_native_result(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    original, finish = cli_controller.integrate_code, c.code_integrations.finish
    invoked, reported = [], []
    def observed(**kw): invoked.append(kw); return original(**kw)
    def flaky(identity, **kw):
        reported.append(kw)
        if len(reported) == 1: raise OSError('Controlled temporary persistence failure')
        return finish(identity, **kw)
    monkeypatch.setattr(cli_controller, 'integrate_code', observed)
    monkeypatch.setattr(c.code_integrations, 'finish', flaky)
    try:
        body = payload(c, room, run)
        record = c.enqueue_code_integration(room['id'], body)
        until(c, lambda: c.code_integrations.get(record['id'])['state'] == 'completed', timeout=60)
        assert len(invoked) == 1 and len(reported) == 2 and reported[0] == reported[1]
        assert not c.integration_active
    finally: c.close()


def test_stop_native_does_not_hold_launch_lock(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    started = threading.Event()
    def waiting(**kw):
        started.set()
        assert kw['cancel'].wait(10)
        raise ValueError('controlled confirmed cancellation')
    monkeypatch.setattr(cli_controller, 'integrate_code', waiting)
    try:
        record = c.enqueue_code_integration(room['id'], payload(c, room, run))
        assert started.wait(10)
        assert c.stop_code_integration(record['id'])['state'] == 'stopping'
        until(c, lambda: c.code_integrations.get(record['id'])['state'] == 'cancelled')
        assert not c.integration_active
    finally: c.close()


def test_stop_during_input_read_is_cancelled_without_git(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    entered, release = threading.Event(), threading.Event()
    inputs = c.code_integrations.inputs
    def paused(identity):
        entered.set()
        assert release.wait(10)
        return inputs(identity)
    monkeypatch.setattr(c.code_integrations, 'inputs', paused)
    monkeypatch.setattr(cli_controller, 'integrate_code', lambda **kw: pytest.fail('Stopped preparation must not run Git'))
    try:
        record = c.enqueue_code_integration(room['id'], payload(c, room, run))
        assert entered.wait(10)
        c.stop_code_integration(record['id'])
        release.set()
        until(c, lambda: not c.integration_active)
        assert c.code_integrations.get(record['id'])['state'] == 'cancelled'
    finally:
        release.set()
        c.close()


def test_submit_exception_retains_ownership_until_pool_shutdown(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    submit = c.pool.submit
    native = []
    def waiting(**kw):
        native.append(kw)
        kw['cancel'].wait(10)
        raise ValueError('cancelled controlled native')
    def submitted_then_raised(*args):
        submit(*args)
        raise RuntimeError('controlled uncertain thread submit')
    monkeypatch.setattr(cli_controller, 'integrate_code', waiting)
    monkeypatch.setattr(c.pool, 'submit', submitted_then_raised)
    body = payload(c, room, run)
    try:
        record = c.enqueue_code_integration(room['id'], body)
        until(c, lambda: c.code_integrations.get(record['id'])['state'] == 'unknown')
        declaration = dict(request_id='checked', process_stopped=True, effects_checked=True, note='Owner checked stopped process and partial directory')
        with pytest.raises(ValueError): c.reconcile_code_integration(record['id'], declaration)
        assert record['id'] in c.integration_active
    finally: c.close()
    c2 = CLIController(store)
    try:
        assert c2.enqueue_code_integration(room['id'], body)['state'] == 'unknown'
        c2.reconcile_code_integration(record['id'], declaration)
        assert c2.code_integrations.get(record['id'])['state'] == 'unknown'
        assert not c2.code_integrations.unresolved()
        assert len(native) <= 1
    finally: c2.close()


def test_authority_changes_after_native_success_preserve_result_as_failed(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    original = cli_controller.integrate_code
    produced, release = threading.Event(), threading.Event()
    def waiting(**kw):
        result = original(**kw)
        produced.set()
        assert release.wait(20)
        return result
    monkeypatch.setattr(cli_controller, 'integrate_code', waiting)
    try:
        record = c.enqueue_code_integration(room['id'], payload(c, room, run))
        assert produced.wait(30)
        store.save_agent({'enabled': False}, agents[1]['id'])
        c.tick()
        release.set()
        until(c, lambda: c.code_integrations.get(record['id'])['state'] == 'failed')
        final = c.code_integrations.get(record['id'])
        assert final['result']['commit']
        assert git(store.data_dir/final['destination_relative'], 'rev-parse', 'HEAD') == final['result']['commit']
    finally:
        release.set()
        c.close()


def test_restart_claim_is_unknown_and_never_automatically_dispatched(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    body = payload(c, room, run)
    record = c.code_integrations.create(room['id'], body)  # Crash boundary after commit, before submit.
    c.close()
    original = cli_controller.integrate_code
    monkeypatch.setattr(cli_controller, 'integrate_code', lambda **kw: pytest.fail('Restart must never repeat native side effects'))
    c2 = CLIController(store)
    try:
        for _ in range(3): c2.tick()
        assert c2.code_integrations.get(record['id'])['state'] == 'unknown'
        assert c2.enqueue_code_integration(room['id'], body)['state'] == 'unknown'
        assert not (store.data_dir/record['destination_relative']).exists()
        c2.reconcile_code_integration(record['id'], dict(request_id='owner-reconciled', process_stopped=True,
            effects_checked=True, note='Owner checked stopped processes and missing operation directory'))
        monkeypatch.setattr(cli_controller, 'integrate_code', original)
        next_body = payload(c2, room, run, request_id='explicit-new-attempt', previous=record['id'], note='Create a separate integration after checking the previous operation')
        next_record = c2.enqueue_code_integration(room['id'], next_body)
        until(c2, lambda: c2.code_integrations.get(next_record['id'])['state'] == 'completed', timeout=60)
        assert next_record['destination_relative'] != record['destination_relative']
        assert c2.code_integrations.get(record['id'])['state'] == 'unknown'
        assert c2.enqueue_code_integration(room['id'], body)['id'] == record['id']
    finally: c2.close()


def test_restore_gate_and_resource_denial_before_claim(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    body = payload(c, room, run)
    monkeypatch.setattr(cli_controller, 'integrate_code', lambda **kw: pytest.fail('Denied request must not run'))
    try:
        (store.data_dir/'restore-quarantine.json').write_text('{}')
        with pytest.raises(ValueError): c.enqueue_code_integration(room['id'], body)
        assert c.code_integrations.list(room['id']) == []
        (store.data_dir/'restore-quarantine.json').unlink()
        monkeypatch.setattr(cli_controller.resource_admission, 'denial', lambda *args: 'controlled resource denial')
        with pytest.raises(ValueError): c.enqueue_code_integration(room['id'], body)
        assert c.code_integrations.list(room['id']) == []
    finally: c.close()


def test_native_and_cli_share_configured_slot(tmp_path, monkeypatch):
    values, _ = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    get, resolve = c.settings.get(), c.settings.resolve()
    monkeypatch.setattr(c.settings, 'get', lambda: get | {'max_concurrency': 1})
    monkeypatch.setattr(c.settings, 'resolve', lambda: resolve | {'max_concurrency': 1})
    native_started, cli_started = threading.Event(), threading.Event()
    reviewer = cli_controller.run_codex
    def native_wait(**kw):
        native_started.set()
        assert kw['cancel'].wait(10)
        raise ValueError('controlled cancellation')
    def cli_observed(**kw):
        cli_started.set()
        return reviewer(**kw)
    monkeypatch.setattr(cli_controller, 'integrate_code', native_wait)
    monkeypatch.setattr(cli_controller, 'run_codex', cli_observed)
    try:
        record = c.enqueue_code_integration(room['id'], payload(c, room, run))
        assert native_started.wait(10)
        other = tasks.create(room['id'], {k: task[k] for k in ('source_message_id', 'title', 'scope', 'acceptance', 'agent_id')}
                             | {'request_id': 'other-independent-task'})
        queued = c.executions.create(other['id'], execution_request())
        c.tick()
        assert c.executions.get(queued['id'])['state'] == 'queued' and not cli_started.is_set()
        c.stop_code_integration(record['id'])
        until(c, cli_started.is_set)
        until(c, lambda: c.executions.get(queued['id'])['state'] == 'awaiting_review', timeout=60)
    finally: c.close()


def test_latest_review_retry_requires_new_approval_and_fresh_preview(tmp_path, monkeypatch):
    values, review = reviewed(tmp_path, monkeypatch)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    try:
        stale = payload(c, room, run)
        retried = c.enqueue(review['task_id'], execution_request(request_id='review-retry',
            previous=review['initial_execution_id'], note='Owner requests a second review of the same fixed code'))
        assert c.code_integrations.preview(room['id'], {'source_execution_ids': [run['id']]})['blockers']
        until(c, lambda: c.executions.get(retried['id'])['state'] == 'awaiting_review', timeout=60)
        assert c.code_integrations.preview(room['id'], {'source_execution_ids': [run['id']]})['blockers']
        Reviews(store).save(retried['id'], dict(request_id='approve-retry', expected_version=1, decision='approved',
            note='Owner accepts second report', artifact_ids=[r['id'] for r in artifacts.list_for(store, retried['id'])]))
        with pytest.raises(ValueError): c.enqueue_code_integration(room['id'], stale)
        current = payload(c, room, run)
        record = c.enqueue_code_integration(room['id'], current)
        assert record['authorization_snapshot']['sources'][0]['review_execution']['id'] == retried['id']
        until(c, lambda: c.code_integrations.get(record['id'])['state'] == 'completed', timeout=60)
        assert c.code_reviews.get(run['id'])['initial_execution_id'] == review['initial_execution_id']
    finally: c.close()
