"""Fixed integrator admission over immutable artifacts, without paid calls."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3

import pytest

from test_workbench_repo_sources import project, binding
from test_workbench_executions import request
from workbench import artifacts, repo_sources, dependencies
from workbench.code_reviews import CodeReviews
from workbench.reviews import Reviews
from workbench.tasks import Tasks


def setup(tmp_path, monkeypatch, approved=True, manifest_changes=None):
    store, tasks, executions, room, agents, task = project(tmp_path / 'state')
    summary = dict(source_path=str(tmp_path / 'source'), commit='a' * 40, tree='b' * 40, files=1, total_bytes=6)
    monkeypatch.setattr(repo_sources, 'inspect_source', lambda *a: summary)
    repositories = repo_sources.RepositorySources(store)
    repository = repositories.save(room['id'], binding(tmp_path / 'source', summary['commit'], agents[1]['id']))
    run = executions.create(task['id'], request()); assert executions.claim(run['id'])
    patch = b''
    manifest = dict(version=1, execution_id=run['id'], conversation_id=room['id'], repository_revision=1,
        integration_agent_id=agents[1]['id'], base_commit=summary['commit'], base_tree=summary['tree'], observed_head=summary['commit'], target_tree=summary['tree'],
        selection='base_head_index_tracked_and_nonignored_new', content='raw_worktree_bytes', changes=[], patch_sha256=hashlib.sha256(patch).hexdigest(), patch_bytes=0)
    manifest.update(manifest_changes or {})
    items = [dict(path='corppilot-code/change.patch', data=patch), dict(path='corppilot-code/manifest.json', data=json.dumps(manifest).encode())]
    executions.report(run['id'], 1, 1, 0, 'Captured code', success=True, artifacts=items)
    if approved:
        Reviews(store).save(run['id'], dict(request_id='approval', expected_version=1, decision='approved', note='Allow review input', artifact_ids=[r['id'] for r in artifacts.list_for(store, run['id'])]))
    return store, tasks, executions, room, agents, task, run, repository, CodeReviews(store)


def send(service, source, **changes):
    return service.create(source, dict(request_id='review', fingerprint=service.preview(source)['fingerprint'], confirm=True) | changes)


def test_atomic_create_old_repository_inputs_and_no_false_approval(tmp_path, monkeypatch):
    store, tasks, executions, room, agents, source_task, source, fixed, service = setup(tmp_path, monkeypatch)
    repo_sources.RepositorySources(store).save(room['id'], dict(request_id='detach', expected_revision=1, source_path=None, commit=None, integration_agent_id=None, confirm=True))
    preview = service.preview(source['id']); assert not preview['blockers']
    receipt = send(service, source['id']); run_id = receipt['initial_execution_id']
    task = tasks.get(receipt['task_id']); assert task['agent_id'] == agents[1]['id']
    assert task['requirement_version'] == 1 and 'review.md' in task['acceptance']
    assert repo_sources.RepositorySources(store).execution(run_id)['repository'] == fixed
    assert executions.claim(run_id)
    snapshot = executions.snapshot(run_id, include_artifacts=True)
    assert {a['id'] for a in snapshot['input_artifacts']} == {a['id'] for a in artifacts.list_for(store, source['id'])}
    assert Reviews(store).get(run_id) is None
    assert service.get(source['id']) == receipt
    assert service.preview(run_id)['blockers']


def test_preview_readonly_and_missing_approval(tmp_path, monkeypatch):
    store, _, _, _, _, _, source, _, service = setup(tmp_path, monkeypatch, False)
    with store.connect() as db: before = list(db.iterdump())
    assert service.get(source['id']) is None
    assert service.preview(source['id'])['blockers']
    with pytest.raises(ValueError): send(service, source['id'])
    with store.connect() as db: assert list(db.iterdump()) == before
    with pytest.raises(KeyError): service.preview('missing')


def test_same_request_replay_after_disable_and_restart_different_request_rejected(tmp_path, monkeypatch):
    store, _, _, _, agents, _, source, _, service = setup(tmp_path, monkeypatch)
    receipt = send(service, source['id']); payload = receipt['request_payload']
    store.save_agent({'enabled': False}, agents[1]['id'])
    restarted = CodeReviews(store)
    assert restarted.create(source['id'], payload) == receipt
    for change in ({'request_id': 'another'}, {'fingerprint': 'f' * 64}):
        with pytest.raises(ValueError): restarted.create(source['id'], payload | change)


@pytest.mark.parametrize('change', ['disabled', 'removed', 'tools', 'archived', 'source_revised', 'review_revised'])
def test_permissions_and_versions_checked_again_before_claim(tmp_path, monkeypatch, change):
    store, tasks, executions, room, agents, source_task, source, _, service = setup(tmp_path, monkeypatch)
    receipt = send(service, source['id'])
    if change == 'disabled': store.save_agent({'enabled': False}, agents[1]['id'])
    elif change == 'tools': store.save_agent({'tools': ['read']}, agents[1]['id'])
    elif change == 'removed': store.set_member(room['id'], agents[1]['id'], False)
    elif change == 'archived': store.save_conversation({'archived': True}, room['id'])
    else:
        identity = source_task['id'] if change == 'source_revised' else receipt['task_id']
        task = tasks.get(identity)
        tasks.revise(identity, dict(expected_version=1, **{k: task[k] for k in ('title', 'scope', 'acceptance', 'agent_id')}) | {'title': 'Changed requirement'})
    assert not executions.claim(receipt['initial_execution_id'])
    assert executions.get(receipt['initial_execution_id'])['state'] in ('failed', 'superseded')


def test_retry_keeps_old_repository_and_source_pin(tmp_path, monkeypatch):
    store, tasks, executions, room, agents, source_task, source, fixed, service = setup(tmp_path, monkeypatch)
    receipt = send(service, source['id']); first = receipt['initial_execution_id']
    assert executions.claim(first); executions.report(first, 1, 1, 1, 'Review failed')
    repo_sources.RepositorySources(store).save(room['id'], dict(request_id='detach', expected_revision=1, source_path=None, commit=None, integration_agent_id=None, confirm=True))
    retry = executions.create(receipt['task_id'], dict(request_id='retry', expected_version=1, previous_execution_id=first, reconciliation_note='Reviewed failure; retry authorized'))
    assert repo_sources.RepositorySources(store).execution(retry['id'])['repository'] == fixed
    replacement = executions.create(source_task['id'], dict(request_id='source-retry', expected_version=1, previous_execution_id=source['id'], reconciliation_note='Owner redoes source'))
    assert replacement['id'] != source['id']
    with store.connect() as db:
        with pytest.raises(dependencies.DependencyBlocked): dependencies.ready_inputs(db, receipt['task_id'], 1, retry['id'])
    assert not executions.claim(retry['id'])


def test_concurrent_creation_one_task_and_atomic_rollback(tmp_path, monkeypatch):
    store, _, _, _, _, _, source, _, service = setup(tmp_path, monkeypatch)
    payload = dict(request_id='same', fingerprint=service.preview(source['id'])['fingerprint'], confirm=True)
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: service.create(source['id'], payload), range(2)))
    assert results[0] == results[1]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM code_review_requests').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 2
        for query in ('DELETE FROM code_review_requests', "UPDATE code_review_requests SET request_id='changed'", 'INSERT OR REPLACE INTO code_review_requests SELECT * FROM code_review_requests'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(query)


def test_creation_failure_rolls_back_task_dependency_and_receipt(tmp_path, monkeypatch):
    store, _, _, _, _, _, source, _, service = setup(tmp_path, monkeypatch)
    with store.connect() as db: before = list(db.iterdump())
    monkeypatch.setattr(service.executions, '_create', lambda *a: (_ for _ in ()).throw(ValueError('injected')))
    with pytest.raises(ValueError): send(service, source['id'])
    with store.connect() as db: assert list(db.iterdump()) == before


@pytest.mark.parametrize('changes', [{'confirm': 1}, {'request_id': ''}, {'fingerprint': 'no'}, {'extra': True}])
def test_invalid_authorization_rejected(tmp_path, monkeypatch, changes):
    _, _, _, _, _, _, source, _, service = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError): send(service, source['id'], **changes)


@pytest.mark.parametrize('change', [{'execution_id': 'different'}, {'patch_sha256': '0' * 64}, {'repository_revision': True}, {'content': 'unverified'}])
def test_approved_artifact_bytes_still_require_valid_code_binding(tmp_path, monkeypatch, change):
    _, _, _, _, _, _, source, _, service = setup(tmp_path, monkeypatch, manifest_changes=change)
    assert service.preview(source['id'])['blockers']
    with pytest.raises(ValueError): send(service, source['id'])


def test_stale_fingerprint_and_unknown_review_retry(tmp_path, monkeypatch):
    store, _, executions, _, agents, _, source, _, service = setup(tmp_path, monkeypatch)
    preview = service.preview(source['id'])
    with pytest.raises(ValueError): service.create(source['id'], dict(request_id='wrong', fingerprint='f' * 64, confirm=True))
    assert service.get(source['id']) is None
    receipt = service.create(source['id'], dict(request_id='correct', fingerprint=preview['fingerprint'], confirm=True))
    identity = receipt['initial_execution_id']; assert executions.claim(identity)
    executions.report(identity, 1, 1, None, 'Unknown process effect')
    with pytest.raises(ValueError): executions.create(receipt['task_id'], dict(request_id='retry', expected_version=1, previous_execution_id=identity, reconciliation_note='Not a real reconciliation'))
