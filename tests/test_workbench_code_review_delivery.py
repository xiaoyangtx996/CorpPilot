"""Owner HTTP authorization reaches the real queue with fixed, native Git inputs.

Only external CLI adapters are controlled; this does not call paid models or Docker.
"""
import hashlib
import time

import pytest

from test_workbench_code_delivery import setup
from test_workbench_cli_controller import until
from test_workbench_api import running, request as http
from test_workbench_repo_sources import binding
from test_workbench_git_checkout import git
from workbench import artifacts, cli_controller
from workbench.cli import prepare_workspace
from workbench.cli_settings import CLISettings
from workbench.reviews import Reviews
from workbench.backup import backup, restore
from workbench.store import Store


def approved(tmp_path, monkeypatch, backend='local'):
    values = setup(tmp_path, monkeypatch, backend)
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = values
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == 'awaiting_review', timeout=60)
        rows = artifacts.list_for(store, run['id'])
        Reviews(store).save(run['id'], dict(request_id='approve-source', expected_version=1,
            decision='approved', note='Owner approved exact code input', artifact_ids=[r['id'] for r in rows]))
    except Exception:
        c.close()
        raise
    return values


@pytest.mark.parametrize('backend', ['local', 'docker'])
def test_owner_http_runs_frozen_integrator_and_restores_receipt(tmp_path, monkeypatch, backend):
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = approved(tmp_path, monkeypatch, backend)
    original = {r['id']: artifacts.get(store, r['id']) for r in artifacts.list_for(store, run['id'])}
    config = c.settings.resolve()
    # A later project setting must not redirect the reviewer to new code/identity.
    (source/'code.txt').write_bytes(b'later project source\n')
    git(source, 'commit', '-qam', 'later source')
    later = git(source, 'rev-parse', 'HEAD')
    c.repositories.save(room['id'], binding(source, later, agents[0]['id'], request_id='later', expected_revision=1))
    c.close()
    monkeypatch.setattr(CLISettings, 'resolve', lambda self: config)
    observed = []
    def reviewer(**kw):
        paths = prepare_workspace(store.data_dir, kw['execution_id'], kw['input_artifacts'], kw['repository'])
        review_task = tasks.get(c.executions.get(kw['execution_id'])['task_id'])
        assert review_task['agent_id'] == agents[1]['id']
        assert kw['repository'] == saved['snapshot']
        assert git(paths['work']/'repository', 'rev-parse', 'HEAD') == sha
        assert {item['id'] for item in kw['input_artifacts']} == set(original)
        for item in kw['input_artifacts']:
            assert item['data'] == original[item['id']]['data']
            assert hashlib.sha256(item['data']).hexdigest() == original[item['id']]['sha256']
        assert run['id'] in kw['prompt'] and 'review.md' in kw['prompt']
        (paths['work']/'artifacts').mkdir()
        (paths['work']/'artifacts/review.md').write_text('Review recommendation: Pass. Owner decision still required.', encoding='utf-8')
        observed.append(kw['execution_id'])
        return dict(exit_code=0, reason='exited', success=True, summary='Controlled integrator reviewed fixed inputs',
                    usage=None, workspace=str(paths['work']))
    monkeypatch.setattr(cli_controller, 'run_docker' if backend == 'docker' else 'run_codex', reviewer)
    prefix = f"/api/workbench/executions/{run['id']}"
    with running(store.data_dir) as port:
        assert http(port, 'GET', prefix+'/code-review', headers={'Authorization': ''})[0] == 401
        assert http(port, 'GET', prefix+'/code-review-preview', headers={'Authorization': ''})[0] == 401
        with store.connect() as db: before = list(db.iterdump())
        status, preview = http(port, 'GET', prefix+'/code-review-preview')
        assert status == 200 and not preview['blockers']
        assert http(port, 'GET', prefix+'/code-review') == (200, None)
        with store.connect() as db: assert list(db.iterdump()) == before
        payload = dict(request_id='owner-review-code', fingerprint=preview['fingerprint'], confirm=True)
        assert http(port, 'POST', prefix+'/code-review', payload | {'confirm': False})[0] == 400
        assert http(port, 'POST', prefix+'/code-review', payload, headers={'Authorization': ''})[0] == 401
        status, receipt = http(port, 'POST', prefix+'/code-review', payload)
        assert status == 202, receipt
        assert receipt['source_execution_id'] == run['id']
        review_id = receipt['initial_execution_id']
        # Lost-response recovery and same-key retry return the original fixed receipt.
        assert http(port, 'GET', prefix+'/code-review') == (200, receipt)
        assert http(port, 'POST', prefix+'/code-review', payload) == (202, receipt)
        assert http(port, 'POST', prefix+'/code-review', payload | {'request_id': 'duplicate'})[0] == 400
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            execution = http(port, 'GET', '/api/workbench/executions/'+review_id)[1]
            if execution['state'] not in ('queued', 'running', 'stopping'): break
            time.sleep(.05)
        assert execution['state'] == 'awaiting_review', execution
        assert observed == [review_id]
        assert http(port, 'GET', '/api/workbench/executions/'+review_id+'/review') == (200, None)
        report = next(r for r in artifacts.list_for(store, review_id) if r['path'] == 'review.md')
        assert b'Owner decision still required' in artifacts.get(store, report['id'])['data']
        assert git(source, 'rev-parse', 'HEAD') == later
        assert (source/'code.txt').read_bytes() == b'later project source\n'
    with running(store.data_dir) as port:
        assert http(port, 'GET', prefix+'/code-review') == (200, receipt)
    if backend == 'local':
        backup(store.data_dir, tmp_path/'backup')
        restore(tmp_path/'backup', tmp_path/'restore')
        from workbench.code_reviews import CodeReviews
        restored = Store(tmp_path/'restore')
        assert CodeReviews(restored).get(run['id']) == receipt
        assert artifacts.get(restored, report['id'])['data'] == artifacts.get(store, report['id'])['data']
        assert not (restored.data_dir/'execution-workspaces').exists()


def test_configuration_failure_creates_nothing_and_replay_survives_close(tmp_path, monkeypatch):
    _, _, store, _, _, _, _, c, run, _, _ = approved(tmp_path, monkeypatch)
    try:
        preview = c.code_reviews.preview(run['id'])
        payload = dict(request_id='review', fingerprint=preview['fingerprint'], confirm=True)
        original = c.settings.resolve
        def unavailable(): raise ValueError('Configuration unavailable')
        monkeypatch.setattr(c.settings, 'resolve', unavailable)
        with store.connect() as db: before = list(db.iterdump())
        with pytest.raises(ValueError, match='Configuration unavailable'): c.enqueue_code_review(run['id'], payload)
        with store.connect() as db: assert list(db.iterdump()) == before
        monkeypatch.setattr(c.settings, 'resolve', original)
        receipt = c.enqueue_code_review(run['id'], payload)
        c.executions.cancel(receipt['initial_execution_id'])
        c.close()
        monkeypatch.setattr(c.settings, 'resolve', unavailable)
        assert c.enqueue_code_review(run['id'], payload) == receipt
    finally:
        if not c.closed: c.close()


@pytest.mark.parametrize('outcome,expected', [('failed', 'failed'), ('unknown', 'unknown'), ('missing-report', 'failed')])
def test_review_failure_does_not_deliver_or_retry(tmp_path, monkeypatch, outcome, expected):
    _, _, store, _, _, _, _, c, run, _, _ = approved(tmp_path, monkeypatch)
    calls = []
    def reviewer(**kw):
        calls.append(kw['execution_id'])
        if outcome == 'missing-report':
            prepare_workspace(store.data_dir, kw['execution_id'], kw['input_artifacts'], kw['repository'])
        return dict(exit_code=None if outcome == 'unknown' else 1 if outcome == 'failed' else 0,
            reason='unknown' if outcome == 'unknown' else 'exited', success=outcome == 'missing-report',
            summary='Controlled outcome', usage=None, workspace='C:/not-trusted')
    monkeypatch.setattr(cli_controller, 'run_codex', reviewer)
    try:
        preview = c.code_reviews.preview(run['id'])
        receipt = c.enqueue_code_review(run['id'], dict(request_id='review', fingerprint=preview['fingerprint'], confirm=True))
        identity = receipt['initial_execution_id']
        until(c, lambda: c.executions.get(identity)['state'] == expected, timeout=60)
        for _ in range(3): c.tick()
        assert calls == [identity]
        assert artifacts.list_for(store, identity) == []
        assert Reviews(store).get(identity) is None
    finally: c.close()


def test_review_waits_for_shared_budget_before_runner(tmp_path, monkeypatch):
    from test_workbench_budgets import config
    _, _, _, _, _, _, _, c, run, _, _ = approved(tmp_path, monkeypatch)
    calls = []
    def reviewer(**kw):
        calls.append(kw['execution_id'])
        return dict(exit_code=1, reason='exited', success=False, summary='Controlled failure after admission', usage=None)
    monkeypatch.setattr(cli_controller, 'run_codex', reviewer)
    try:
        c.budgets.save(config(total=0))
        preview = c.code_reviews.preview(run['id'])
        receipt = c.enqueue_code_review(run['id'], dict(request_id='budgeted-review', fingerprint=preview['fingerprint'], confirm=True))
        identity = receipt['initial_execution_id']
        for _ in range(3): c.tick()
        assert c.executions.get(identity)['state'] == 'queued'
        assert calls == [] and c.budgets.list() == []
        c.budgets.save(config(total=10))
        until(c, lambda: c.executions.get(identity)['state'] == 'failed')
        assert calls == [identity]
        assert [r['run_id'] for r in c.budgets.list()] == [identity]
    finally: c.close()
