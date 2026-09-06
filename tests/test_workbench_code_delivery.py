"""Real code patches use the existing immutable artifact and approval pipeline."""
import hashlib
from http.client import HTTPConnection
import json
import sqlite3
import sys

import pytest

from test_workbench_repo_sources import project, binding
from test_workbench_git_checkout import repo, git
from test_workbench_executions import request
from test_workbench_cli_controller import ready, until
from test_workbench_dependencies import link
from test_workbench_api import running, request as http, TOKENS
from workbench import cli_controller, artifacts
from workbench.cli_controller import CLIController
from workbench.cli import prepare_workspace
from workbench.git_checkout import PreparationUnknownError
from workbench.reviews import Reviews
from workbench.backup import backup, restore
from workbench.store import Store


def setup(tmp_path, monkeypatch, backend='local'):
    source, sha = repo(tmp_path)
    store, tasks, _, room, agents, task = project(tmp_path/'state')
    c = CLIController(store); ready(monkeypatch, c)
    config = c.settings.resolve() | dict(backend=backend, executable=sys.executable, docker_executable=sys.executable,
        docker_image='sha256:'+'a'*64, docker_cpus=1, docker_memory_mb=1024, docker_pids_limit=32)
    monkeypatch.setattr(c.settings, 'resolve', lambda: config)
    saved = c.repositories.save(room['id'], binding(source, sha, agents[1]['id']))
    run = c.executions.create(task['id'], request()); calls = []
    def runner(**kw):
        paths = prepare_workspace(store.data_dir, kw['execution_id'], kw['input_artifacts'], kw['repository'])
        (paths['work']/'repository/code.txt').write_bytes(b'changed code\n')
        (paths['work']/'repository/new.txt').write_bytes(b'new source\n')
        (paths['work']/'artifacts').mkdir(); (paths['work']/'artifacts/report.txt').write_bytes(b'Runner report')
        assert 'corppilot-code' in kw['prompt']
        calls.append(kw['execution_id'])
        return dict(exit_code=0, reason='exited', success=True, summary='Controlled CLI completed', workspace='C:/not-trusted',
            usage=dict(input_tokens=4, output_tokens=2, cached_input_tokens=0))
    monkeypatch.setattr(cli_controller, 'run_docker' if backend == 'docker' else 'run_codex', runner)
    return source, sha, store, tasks, room, agents, task, c, run, saved, calls


@pytest.mark.parametrize('backend', ['local', 'docker'])
def test_actual_code_artifacts_approval_download_and_authorized_inputs(tmp_path, monkeypatch, backend):
    source, sha, store, tasks, room, agents, task, c, run, saved, calls = setup(tmp_path, monkeypatch, backend)
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == 'awaiting_review', timeout=60)
        assert calls == [run['id']]
        rows = artifacts.list_for(store, run['id']); by_path = {r['path']: artifacts.get(store, r['id']) for r in rows}
        assert set(by_path) == {'report.txt', 'corppilot-code/change.patch', 'corppilot-code/manifest.json'}
        patch = by_path['corppilot-code/change.patch']['data']; manifest = json.loads(by_path['corppilot-code/manifest.json']['data'])
        assert manifest['execution_id'] == run['id'] and manifest['base_commit'] == sha
        assert manifest['base_tree'] == saved['snapshot']['tree'] and manifest['repository_revision'] == 1
        assert manifest['conversation_id'] == room['id'] and manifest['integration_agent_id'] == agents[1]['id']
        assert manifest['patch_bytes'] == len(patch) and manifest['patch_sha256'] == hashlib.sha256(patch).hexdigest()
        assert {change['path'] for change in manifest['changes']} == {'code.txt', 'new.txt'}
        assert (source/'code.txt').read_bytes() == b'first\n' and git(source, 'rev-parse', 'HEAD') == sha
        assert not (source/'new.txt').exists()
        # Complete original manifest and patch must be approved together.
        reviews = Reviews(store); ids = sorted(row['id'] for row in rows)
        payload = dict(request_id='review', expected_version=1, decision='approved', note='Owner checked captured patch', artifact_ids=ids)
        with pytest.raises(ValueError): reviews.save(run['id'], payload | {'artifact_ids': ids[:1]})
        assert reviews.get(run['id']) is None
        reviews.save(run['id'], payload)
        downstream = tasks.create(room['id'], {key: task[key] for key in ('source_message_id', 'title', 'scope', 'acceptance')}
            | dict(agent_id=agents[1]['id'], request_id='integration-review'))
        link(tasks, downstream, [task]); downstream = tasks.get(downstream['id'])
        review_run = c.executions.create(downstream['id'], request(version=downstream['requirement_version']))
        assert c.executions.claim(review_run['id'])
        snapshot = c.executions.snapshot(review_run['id'], include_artifacts=True)
        assert {item['id'] for item in snapshot['input_artifacts']} == set(ids)
        assert next(item['data'] for item in snapshot['input_artifacts'] if item['path'].endswith('change.patch')) == patch
        c.executions.report(review_run['id'], 1, downstream['requirement_version'], 1, 'Input observation ended; integrator was not invoked')
    finally: c.close()
    # Existing Owner download and persistence work without a new endpoint.
    with running(store.data_dir) as port:
        assert http(port, 'GET', f"/api/workbench/executions/{run['id']}/artifacts")[1] == rows
        for artifact in by_path.values():
            connection = HTTPConnection('127.0.0.1', port)
            try:
                connection.request('GET', f"/api/workbench/artifacts/{artifact['id']}/download", headers={'Authorization': 'Bearer '+TOKENS[port]})
                response = connection.getresponse(); assert response.status == 200 and response.read() == artifact['data']
            finally: connection.close()
        assert http(port, 'GET', f"/api/workbench/artifacts/{rows[0]['id']}/download", headers={'Authorization': ''})[0] == 401
    if backend == 'local':
        backup(store.data_dir, tmp_path/'backup'); restore(tmp_path/'backup', tmp_path/'restored')
        restored = Store(tmp_path/'restored')
        for artifact in by_path.values(): assert artifacts.get(restored, artifact['id'])['data'] == artifact['data']
        assert not (restored.data_dir/'execution-workspaces').exists()


@pytest.mark.parametrize('backend', ['local', 'docker'])
@pytest.mark.parametrize('exit_code,success', [(1, False), (None, False), (0, False)])
def test_failed_or_unknown_cli_never_collects_code(tmp_path, monkeypatch, backend, exit_code, success):
    *_, c, run, saved, calls = setup(tmp_path, monkeypatch, backend)
    def runner(**kw):
        calls.append(True); return dict(exit_code=exit_code, success=success, reason='unknown' if exit_code is None else 'exited', summary='Controlled failure', workspace=None)
    monkeypatch.setattr(cli_controller, 'run_docker' if backend == 'docker' else 'run_codex', runner)
    monkeypatch.setattr(cli_controller, 'capture_code', lambda *a: pytest.fail('Code collection must not run'))
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == ('unknown' if exit_code is None else 'failed'))
        assert calls == [True] and not artifacts.list_for(c.store, run['id'])
    finally: c.close()


@pytest.mark.parametrize('unknown', [False, True])
def test_collector_failure_keeps_usage_but_no_partial_artifacts(tmp_path, monkeypatch, unknown):
    *_, c, run, saved, calls = setup(tmp_path, monkeypatch)
    captured = []
    def failed(*args):
        captured.append(True)
        raise (PreparationUnknownError('Unconfirmed Git') if unknown else ValueError('Rejected source'))
    monkeypatch.setattr(cli_controller, 'capture_code', failed)
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == ('unknown' if unknown else 'failed'), timeout=30)
        for _ in range(3): c.tick()
        assert len(calls) == 1 and captured == [True] and not artifacts.list_for(c.store, run['id'])
        assert c.executions.get(run['id'])['usage']['input_tokens'] == 4
    finally: c.close()


def test_report_retry_reuses_captured_patch_without_rerunning_git_or_cli(tmp_path, monkeypatch):
    *_, c, run, saved, calls = setup(tmp_path, monkeypatch)
    real_capture, real_persist = cli_controller.capture_code, artifacts.persist
    captured, writes = [], []
    def capture(*args):
        captured.append(True); return real_capture(*args)
    def persist(*args):
        writes.append(True)
        if len(writes) == 1: raise sqlite3.OperationalError('Controlled transient database failure')
        return real_persist(*args)
    monkeypatch.setattr(cli_controller, 'capture_code', capture); monkeypatch.setattr(artifacts, 'persist', persist)
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == 'awaiting_review', timeout=60)
        assert len(calls) == 1 and captured == [True] and len(writes) == 2
        assert len(artifacts.list_for(c.store, run['id'])) == 3
    finally: c.close()


def test_stop_during_collection_preserves_cancelled_without_artifacts(tmp_path, monkeypatch):
    *_, c, run, saved, calls = setup(tmp_path, monkeypatch)
    def stop(data_dir, identity, binding, key, cancel):
        assert identity == run['id'] and cancel is not None
        c.executions.cancel(identity); cancel.set()
        raise ValueError('Capture cancelled after known CLI exit')
    monkeypatch.setattr(cli_controller, 'capture_code', stop)
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == 'cancelled', timeout=30)
        assert len(calls) == 1 and not artifacts.list_for(c.store, run['id'])
    finally: c.close()


@pytest.mark.parametrize('boundary', ['reserved-path', 'count', 'total'])
def test_combined_artifact_limits_refuse_entire_result(tmp_path, monkeypatch, boundary):
    *_, c, run, saved, calls = setup(tmp_path, monkeypatch)
    if boundary == 'reserved-path':
        monkeypatch.setattr(cli_controller, 'capture', lambda *a: [{'path': 'CorpPilot-Code/forged', 'data': b'x'}])
        monkeypatch.setattr(cli_controller, 'capture_code', lambda *a: pytest.fail('Reserved namespace collision must fail before collection'))
    else:
        monkeypatch.setattr(cli_controller, 'capture', lambda *a: [{'path': 'report', 'data': b'123'}])
        monkeypatch.setattr(cli_controller, 'capture_code', lambda *a: [{'path': 'corppilot-code/change.patch', 'data': b'456'}])
        monkeypatch.setattr(artifacts, 'MAX_FILES' if boundary == 'count' else 'MAX_TOTAL_BYTES', 1 if boundary == 'count' else 5)
    try:
        until(c, lambda: c.executions.get(run['id'])['state'] == 'failed', timeout=30)
        assert len(calls) == 1 and not artifacts.list_for(c.store, run['id'])
    finally: c.close()
