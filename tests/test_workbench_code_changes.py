"""Native Git capture and independent application, without model calls."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench import code_changes as code, git_checkout as native
from test_workbench_git_checkout import git, repo


def fixture(tmp_path):
    source, commit = repo(tmp_path)
    identity = str(uuid.uuid4())
    data = tmp_path / 'data'
    worker = data / 'execution-workspaces' / identity / 'work/repository'
    worker.parent.mkdir(parents=True)
    summary = native.prepare_checkout(source, commit, worker, 'corppilot/run-' + identity)
    binding = dict(conversation_id=str(uuid.uuid4()), revision=1, request_id='fixture', created_at='2026-09-06T00:00:00Z',
                   snapshot={**summary, 'integration_agent_id': str(uuid.uuid4())})
    return data, identity, worker, binding


def capture(f):
    rows = code.capture_code(f[0], f[1], f[3], 'fixture-secret-never-export')
    assert [row['path'] for row in rows] == ['corppilot-code/change.patch', 'corppilot-code/manifest.json']
    patch, manifest = rows[0]['data'], json.loads(rows[1]['data'])
    assert manifest['patch_sha256'] == hashlib.sha256(patch).hexdigest()
    assert manifest['patch_bytes'] == len(patch)
    return patch, manifest


def test_committed_staged_dirty_binary_and_untracked_apply_exactly(tmp_path):
    f = fixture(tmp_path); worker = f[2]
    (worker / 'committed.bin').write_bytes(b'\0committed\xff')
    (worker / '.gitignore').write_text('*.bin\nignored.txt\n')
    git(worker, 'add', '-f', 'committed.bin', '.gitignore'); git(worker, 'commit', '-qm', 'new')
    git(worker, 'rm', '--cached', 'committed.bin')  # HEAD tracking still includes an ignored, index-removed file.
    (worker / 'staged.bin').write_bytes(b'\0staged')
    git(worker, 'add', '-f', 'staged.bin')
    (worker / 'staged.bin').write_bytes(b'\0actual after stage')
    (worker / 'new.txt').write_bytes(b'new\r\n')
    (worker / 'ignored.txt').write_text('excluded')
    (worker / 'code.txt').unlink()
    before = (git(worker, 'status', '--porcelain'), git(worker, 'rev-parse', 'HEAD'), (worker / '.git/index').read_bytes())
    patch, manifest = capture(f)
    assert {v['path'] for v in manifest['changes']} == {'.gitignore', 'committed.bin', 'staged.bin', 'new.txt', 'code.txt'}
    assert before == (git(worker, 'status', '--porcelain'), git(worker, 'rev-parse', 'HEAD'), (worker / '.git/index').read_bytes())
    applied = tmp_path / 'applied'
    native.prepare_checkout(worker, f[3]['snapshot']['commit'], applied, 'review/apply')
    git(applied, 'apply', '--index', '--binary', '-', stdin=patch)
    assert git(applied, 'write-tree') == manifest['target_tree']
    for name in ['committed.bin', 'staged.bin', 'new.txt', '.gitignore']:
        assert (applied / name).read_bytes() == (worker / name).read_bytes()
    assert not (applied / 'code.txt').exists()


def test_empty_patch_and_source_missing(tmp_path):
    f = fixture(tmp_path)
    f[3]['snapshot']['source_path'] = str(tmp_path / 'unavailable-host-source')
    patch, manifest = capture(f)
    assert patch == b'' and manifest['changes'] == []
    assert manifest['base_tree'] == manifest['target_tree']


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16-le', 'utf-16-be'])
def test_binary_secret_rejected_before_compression(tmp_path, encoding):
    f = fixture(tmp_path)
    (f[2] / 'private.bin').write_bytes(b'\0' + 'fixture-secret-never-export'.encode(encoding))
    with pytest.raises(ValueError, match='代码成果'):
        capture(f)


def test_old_deleted_secret_rejected(tmp_path):
    source, _ = repo(tmp_path)
    (source / 'code.txt').write_text('fixture-secret-never-export')
    git(source, 'commit', '-qam', 'secret')
    identity = str(uuid.uuid4()); data = tmp_path / 'data'; worker = data / 'execution-workspaces' / identity / 'work/repository'; worker.parent.mkdir(parents=True)
    summary = native.prepare_checkout(source, git(source, 'rev-parse', 'HEAD'), worker, 'worker/main')
    binding = dict(conversation_id=str(uuid.uuid4()), revision=1, request_id='x', created_at='x', snapshot={**summary, 'integration_agent_id': str(uuid.uuid4())})
    (worker / 'code.txt').unlink()
    with pytest.raises(ValueError): code.capture_code(data, identity, binding, 'fixture-secret-never-export')


def test_hostile_filters_hooks_diff_and_fsmonitor_never_run(tmp_path):
    f = fixture(tmp_path); worker = f[2]; marker = tmp_path / 'EXECUTED'
    command = f'echo dangerous > "{marker.as_posix()}"'
    for key in ['filter.bad.clean', 'filter.bad.smudge', 'diff.bad.command', 'diff.bad.textconv', 'core.fsmonitor', 'uploadpack.packObjectsHook']:
        git(worker, 'config', key, command)
    (worker / '.gitattributes').write_text('* filter=bad diff=bad\n')
    (worker / 'code.txt').write_text('changed')
    patch, manifest = capture(f)
    assert manifest['changes'] and patch
    assert not marker.exists()


def test_hardlink_rejected(tmp_path):
    f = fixture(tmp_path); outside = tmp_path / 'outside'; outside.write_bytes(b'outside')
    os.link(outside, f[2] / 'linked')
    with pytest.raises(ValueError): capture(f)


def test_patch_limit_rejects_instead_of_truncating(tmp_path, monkeypatch):
    f = fixture(tmp_path); (f[2] / 'code.txt').write_bytes(b'new\n' * 1000)
    monkeypatch.setattr(code, 'MAX_PATCH_BYTES', 32)
    with pytest.raises(ValueError): capture(f)


def test_unknown_is_not_known_failure(tmp_path, monkeypatch):
    f = fixture(tmp_path)
    monkeypatch.setattr(code, 'run_process', lambda *a, **kw: {'reason': 'unknown', 'exit_code': None, 'stdout': b''})
    with pytest.raises(native.PreparationUnknownError): capture(f)


def test_wrong_frozen_tree_rejected(tmp_path):
    f = fixture(tmp_path); f[3]['snapshot']['tree'] = '0' * 40
    with pytest.raises(ValueError): capture(f)


def test_cancel_before_git_and_propagated_to_running_git(tmp_path, monkeypatch):
    f = fixture(tmp_path); cancel = threading.Event(); cancel.set()
    with pytest.raises(ValueError): code.capture_code(f[0], f[1], f[3], 'fixture-secret-never-export', cancel)
    cancel.clear(); observed = []
    def cancelled(*a, **kw):
        observed.append(kw['cancel']); cancel.set()
        return {'reason': 'cancelled', 'exit_code': None, 'stdout': b''}
    monkeypatch.setattr(code, 'run_process', cancelled)
    with pytest.raises(ValueError): code.capture_code(f[0], f[1], f[3], 'fixture-secret-never-export', cancel)
    assert observed == [cancel]


def test_unchanged_hundreds_use_batched_native_commands(tmp_path, monkeypatch):
    f = fixture(tmp_path)
    for index in range(200): (f[2] / f'new-{index}.txt').write_text(str(index))
    actual = code.run_process; calls = []
    def observed(*a, **kw):
        calls.append(a[0]); return actual(*a, **kw)
    monkeypatch.setattr(code, 'run_process', observed)
    _, manifest = capture(f)
    assert len(manifest['changes']) == 200
    assert len(calls) < 20


def test_linked_ignore_file_rejected(tmp_path):
    f = fixture(tmp_path); outside = tmp_path / 'ignore'; outside.write_text('*')
    try: os.symlink(outside, f[2] / '.gitignore')
    except OSError: pytest.skip('Symlink creation unavailable')
    with pytest.raises(ValueError): capture(f)


def test_file_directory_conversion_is_explicitly_rejected(tmp_path):
    f = fixture(tmp_path); (f[2] / 'code.txt').unlink(); (f[2] / 'code.txt').mkdir(); (f[2] / 'code.txt/new.txt').write_text('new')
    with pytest.raises(ValueError): capture(f)
