"""Real Git integration in disposable repositories; no remote writes or model calls."""
import copy
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench import code_integration as integration, git_checkout as native
from test_workbench_git_checkout import git, repo


def change(tmp_path, source, base, files):
    worker = tmp_path / ('worker-' + uuid.uuid4().hex)
    summary = native.prepare_checkout(source, base, worker, 'worker/change')
    (worker / '.git/info').mkdir(exist_ok=True)
    (worker / '.git/info/attributes').write_bytes(b'* -text -eol -crlf -ident -filter -working-tree-encoding\n')
    changes = []
    for name, data in files.items():
        path = worker / name; before = path.read_bytes() if path.exists() else None
        if data is None: path.unlink()
        else: path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        changes.append(dict(path=name, status='A' if before is None else 'D' if data is None else 'M',
            old_mode='100644' if before is not None else None, new_mode='100644' if data is not None else None,
            old_size=len(before) if before is not None else None, new_size=len(data) if data is not None else None,
            old_sha256=hashlib.sha256(before).hexdigest() if before is not None else None,
            new_sha256=hashlib.sha256(data).hexdigest() if data is not None else None))
    git(worker, '-c', 'core.autocrlf=false', 'add', '-A')
    executable = Path(shutil.which('git'))
    rendered = subprocess.run([str(executable), 'diff', '--cached', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', base], cwd=worker,
        env=native._environment(tmp_path, executable), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert rendered.returncode == 0
    patch = rendered.stdout
    manifest = dict(version=1, execution_id=str(uuid.uuid4()), conversation_id=str(uuid.uuid4()), repository_revision=1,
                    integration_agent_id=str(uuid.uuid4()), base_commit=base, base_tree=summary['tree'], observed_head=base,
                    target_tree=git(worker, 'write-tree'), selection='base_head_index_tracked_and_nonignored_new', content='raw_worktree_bytes',
                    changes=changes, patch_sha256=hashlib.sha256(patch).hexdigest(), patch_bytes=len(patch))
    return dict(patch=patch, manifest=manifest)


def same_project(first, second):
    for key in ('conversation_id', 'integration_agent_id', 'repository_revision'):
        second['manifest'][key] = first['manifest'][key]


def test_disjoint_changes_binary_commit_parent_source_unchanged(tmp_path):
    source, base = repo(tmp_path)
    first = change(tmp_path, source, base, {'first.txt': b'first new\n'})
    second = change(tmp_path, source, base, {'binary.dat': b'\x00\xffbinary\x00\xfe'})
    same_project(first, second)
    (source / 'code.txt').write_bytes(b'local dirty\n'); (source / 'private').write_bytes(b'not included')
    before = (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'), (source / '.git/config').read_bytes())
    destination = tmp_path / 'integrated'
    result = integration.integrate_code(source, base, destination, 'integration/reviewed', [first, second])
    assert result['branch'] == 'integration/reviewed'
    assert result['source_execution_ids'] == [first['manifest']['execution_id'], second['manifest']['execution_id']]
    assert git(destination, 'rev-list', '--parents', '-n', '1', 'HEAD') == result['commit'] + ' ' + base
    assert git(destination, 'rev-parse', 'HEAD^{tree}') == result['tree']
    assert git(destination, 'branch', '--show-current') == result['branch']
    assert git(destination, 'status', '--porcelain') == ''
    assert git(destination, 'log', '-1', '--format=%an <%ae>') == 'CorpPilot Integrator <integrator@corppilot.local>'
    assert (destination / 'code.txt').read_bytes() == b'first\n'
    assert (destination / 'binary.dat').read_bytes() == b'\x00\xffbinary\x00\xfe'
    assert not (destination / 'private').exists() and git(destination, 'remote') == ''
    assert before == (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'), (source / '.git/config').read_bytes())
    assert not (destination / '.git/objects/info/alternates').exists()
    for path in (destination / '.git/objects').rglob('*'):
        if path.is_file(): assert path.stat().st_nlink == 1


def test_conflicting_patches_fail_and_destination_not_reusable(tmp_path):
    source, base = repo(tmp_path)
    first = change(tmp_path, source, base, {'code.txt': b'one\n'})
    second = change(tmp_path, source, base, {'code.txt': b'two\n'})
    same_project(first, second)
    destination = tmp_path / 'conflict'
    with pytest.raises(ValueError): integration.integrate_code(source, base, destination, 'integration/result', [first, second])
    assert destination.exists() and (source / 'code.txt').read_bytes() == b'first\n'
    with pytest.raises(ValueError): integration.integrate_code(source, base, destination, 'integration/result', [first])


@pytest.mark.parametrize('kind', ['patch_hash', 'patch_size', 'base_commit', 'base_tree', 'target_tree', 'change_path', 'change_hash', 'change_size', 'extra'])
def test_tamper_rejected(tmp_path, kind):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {'new.txt': b'new\n'})
    manifest = item['manifest']
    if kind in ('patch_hash', 'patch_size'): manifest['patch_sha256' if kind == 'patch_hash' else 'patch_bytes'] = '0' * 64 if kind == 'patch_hash' else manifest['patch_bytes'] + 1
    elif kind in ('base_commit', 'base_tree', 'target_tree'): manifest[kind] = '0' * 40
    elif kind == 'change_path': manifest['changes'][0]['path'] = 'other.txt'
    elif kind == 'change_hash': manifest['changes'][0]['new_sha256'] = '0' * 64
    elif kind == 'change_size': manifest['changes'][0]['new_size'] += 1
    else: manifest['unknown'] = True
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'target', 'integration/result', [item])
    assert git(source, 'rev-parse', 'HEAD') == base


def test_second_patch_must_independently_apply_to_base(tmp_path):
    source, base = repo(tmp_path); first = change(tmp_path, source, base, {'new.txt': b'one\n'})
    first_dir = tmp_path / 'first-result'
    combined = integration.integrate_code(source, base, first_dir, 'integration/first', [first])
    second = change(tmp_path, first_dir, combined['commit'], {'new.txt': b'two\n'})
    same_project(first, second)
    second['manifest']['base_commit'] = base; second['manifest']['base_tree'] = first['manifest']['base_tree']
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'bad', 'integration/bad', [first, second])


def test_empty_patch_explicit_no_change_commit(tmp_path):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {})
    result = integration.integrate_code(source, base, tmp_path / 'empty', 'integration/empty', [item])
    assert result['tree'] == item['manifest']['base_tree'] and result['commit'] != base


def test_unknown_and_cancel_never_report_success(tmp_path, monkeypatch):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {})
    monkeypatch.setattr(integration, 'run_process', lambda *a, **kw: {'reason': 'unknown', 'exit_code': None, 'stdout': b''})
    with pytest.raises(native.PreparationUnknownError): integration.integrate_code(source, base, tmp_path / 'unknown', 'integration/unknown', [item])
    cancel = threading.Event(); cancel.set()
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'cancelled', 'integration/cancelled', [item], cancel=cancel)


def test_deadline_is_shared(tmp_path, monkeypatch):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {})
    actual = integration.run_process; calls = []
    def observed(*a, **kw): calls.append(a[4]); return actual(*a, **kw)
    monkeypatch.setattr(integration, 'run_process', observed)
    integration.integrate_code(source, base, tmp_path / 'deadline', 'integration/deadline', [item])
    assert calls and all(0 < timeout <= 60 for timeout in calls)
    monkeypatch.setattr(integration, 'MAX_SECONDS', 0)
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'expired', 'integration/expired', [item])


def test_source_hooks_filters_and_ambient_settings_not_inherited(tmp_path):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {'.gitattributes': b'* filter=bad diff=bad\n', 'new.txt': b'new\n'})
    marker = tmp_path / 'EXECUTED'; command = f'echo dangerous > "{marker.as_posix()}"'
    for key in ('core.fsmonitor', 'filter.bad.smudge', 'filter.bad.clean', 'diff.bad.textconv', 'uploadpack.packObjectsHook'):
        git(source, 'config', key, command)
    integration.integrate_code(source, base, tmp_path / 'safe', 'integration/safe', [item])
    assert not marker.exists()


def test_final_worktree_keeps_raw_bytes_despite_tracked_attributes(tmp_path):
    source, base = repo(tmp_path)
    item = change(tmp_path, source, base, {'.gitattributes': b'*.txt text eol=crlf ident\n*.utf working-tree-encoding=UTF-16\n',
        'new.txt': b'raw LF\n$Id$\n', 'binary.dat': b'\0\xffdata', 'encoded.utf': b'raw utf8\n'})
    destination = tmp_path / 'attributes'
    integration.integrate_code(source, base, destination, 'integration/raw', [item])
    assert (destination / 'new.txt').read_bytes() == b'raw LF\n$Id$\n'
    assert (destination / 'encoded.utf').read_bytes() == b'raw utf8\n'
    assert (destination / 'binary.dat').read_bytes() == b'\0\xffdata'
    assert git(destination, 'status', '--porcelain') == ''


def test_sha256_repository_integration_raw_blob_verification(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    git(source, 'init', '--object-format=sha256', '-q')
    (source / 'code.txt').write_bytes(b'first\n'); git(source, 'add', 'code.txt'); git(source, 'commit', '-qm', 'initial')
    base = git(source, 'rev-parse', 'HEAD')
    item = change(tmp_path, source, base, {'new.bin': b'\0\xffsha256'})
    result = integration.integrate_code(source, base, tmp_path / 'sha256', 'integration/sha256', [item])
    assert len(result['commit']) == 64 and len(result['tree']) == 64
    assert (tmp_path / 'sha256/new.bin').read_bytes() == b'\0\xffsha256'


@pytest.mark.parametrize('field,value', [('conversation_id', str(uuid.uuid4())), ('integration_agent_id', str(uuid.uuid4())), ('repository_revision', 2)])
def test_mixed_responsibility_rejected(tmp_path, field, value):
    source, base = repo(tmp_path); first = change(tmp_path, source, base, {'one': b'one'})
    second = copy.deepcopy(first); second['manifest']['execution_id'] = str(uuid.uuid4()); second['manifest'][field] = value
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'mixed', 'integration/mixed', [first, second])
    assert not (tmp_path / 'mixed').exists()  # Reject scope before Git, not later duplicate-patch conflict.


def test_total_input_limit_and_zero_revision(tmp_path, monkeypatch):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {'one': b'one'})
    item['manifest']['repository_revision'] = 0
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'zero', 'integration/zero', [item])
    item['manifest']['repository_revision'] = 1
    monkeypatch.setattr(integration.artifacts, 'MAX_TOTAL_BYTES', 1)
    with pytest.raises(ValueError): integration.integrate_code(source, base, tmp_path / 'limit', 'integration/limit', [item])


@pytest.mark.parametrize('kind', ['empty', 'too_many', 'duplicate', 'unsafe_path', 'symlink_mode', 'existing'])
def test_invalid_input_boundaries(tmp_path, kind):
    source, base = repo(tmp_path); item = change(tmp_path, source, base, {'new.txt': b'new\n'}); selected = [item]; destination = tmp_path / 'target'
    if kind == 'empty': selected = []
    elif kind == 'too_many': selected = [item] * 17
    elif kind == 'duplicate': selected = [item, copy.deepcopy(item)]
    elif kind == 'unsafe_path': item['manifest']['changes'][0]['path'] = '../outside'
    elif kind == 'symlink_mode': item['manifest']['changes'][0]['new_mode'] = '120000'
    elif kind == 'existing': destination.mkdir()
    with pytest.raises(ValueError): integration.integrate_code(source, base, destination, 'integration/safe', selected)
