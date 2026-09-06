"""Real temporary Git repositories; no source checkout changes or network operations."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench import git_checkout as native


def git(path, *args, stdin=None, author=True):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull)
    if author:
        env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
                   GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test')
    result = subprocess.run([shutil.which('git'), '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull, *args], cwd=path, env=env, input=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout.decode().strip()


def repo(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    git(source, 'init', '-q')
    (source / 'code.txt').write_bytes(b'first\n')
    git(source, 'add', 'code.txt'); git(source, 'commit', '-qm', 'first')
    return source, git(source, 'rev-parse', 'HEAD')


def test_two_self_contained_checkouts_pinned_old_source_unchanged(tmp_path):
    source, old = repo(tmp_path)
    (source / 'code.txt').write_text('second\n'); git(source, 'commit', '-qam', 'second')
    (source / 'untracked.txt').write_text('private local untracked')
    before = (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'))
    expected = native.inspect_source(source, old)
    assert expected['files'] == 1 and expected['total_bytes'] == 6
    copies = [tmp_path / 'one', tmp_path / 'two']
    for index, target in enumerate(copies):
        assert native.prepare_checkout(source, old, target, f'worker/run_{index}') == expected
        assert (target / 'code.txt').read_text() == 'first\n' and not (target / 'untracked.txt').exists()
        assert git(target, 'rev-parse', 'HEAD') == old
        assert git(target, 'branch', '--show-current') == f'worker/run_{index}'
        assert git(target, 'status', '--porcelain') == '' and git(target, 'remote') == ''
        assert not (target / '.git/objects/info/alternates').exists()
        assert not (target / '.git/FETCH_HEAD').exists()
        assert not (target / '.git/logs').exists()
        assert str(source) not in (target / '.git/config').read_text()
        for obj in (target / '.git/objects').rglob('*'):
            if obj.is_file(): assert obj.stat().st_nlink == 1
    (copies[0] / 'code.txt').write_text('worker one')
    git(copies[0], 'add', 'code.txt'); git(copies[0], 'commit', '-qm', 'worker change', author=False)
    assert git(copies[0], 'log', '-1', '--format=%an <%ae>') == 'CorpPilot Worker <worker@corppilot.local>'
    assert (copies[1] / 'code.txt').read_text() == 'first\n'
    assert (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain')) == before


def test_source_and_ambient_execution_configuration_cannot_run(tmp_path, monkeypatch):
    source, commit = repo(tmp_path)
    marker = tmp_path / 'EXECUTED'
    command = f'echo unsafe > "{marker.as_posix()}"'
    git(source, 'config', 'uploadpack.packObjectsHook', command)
    git(source, 'config', 'core.fsmonitor', command)
    git(source, 'config', 'filter.poison.smudge', command)
    (source / '.gitattributes').write_text('*.txt filter=poison\n')
    git(source, 'add', '.gitattributes'); git(source, 'commit', '-qm', 'attributes')
    commit = git(source, 'rev-parse', 'HEAD')
    for name in ('post-checkout', 'post-index-change'):
        hook = source / '.git/hooks' / name
        hook.write_text('#!/bin/sh\n' + command + '\n')
    global_config = tmp_path / 'poison.config'
    global_config.write_text('[uploadpack]\n\tpackObjectsHook = ' + command.replace('"', '\\"') + '\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(global_config))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1'); monkeypatch.setenv('GIT_CONFIG_KEY_0', 'uploadpack.packObjectsHook')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', command)
    monkeypatch.setenv('CODEX_API_KEY', 'private-never-in-checkout')
    target = tmp_path / 'prepared'
    assert not marker.exists()
    native.prepare_checkout(source, commit, target, 'worker')
    assert not marker.exists()
    assert 'poison' not in (target / '.git/config').read_text()
    assert 'private-never-in-checkout' not in (target / '.git/config').read_text()


@pytest.mark.parametrize('value', ['HEAD', 'main', 'a'*39, 'a'*41, 'A'*40, '--help', 'x'*64])
def test_only_exact_full_commit_allowed(tmp_path, value):
    source, _ = repo(tmp_path)
    with pytest.raises(ValueError): native.inspect_source(source, value)


@pytest.mark.parametrize('branch', ['', '../escape', '-option', 'a//b', 'a/', 'a..b', 'a.lock', 'a b'])
def test_bad_branch_does_not_create_destination(tmp_path, branch):
    source, commit = repo(tmp_path); target = tmp_path / 'out'
    with pytest.raises(ValueError): native.prepare_checkout(source, commit, target, branch)
    assert not target.exists()


def test_existing_and_nested_destinations_rejected(tmp_path):
    source, commit = repo(tmp_path)
    existing = tmp_path / 'existing'; existing.mkdir(); (existing / 'keep').write_text('keep')
    for target in (existing, source / 'nested', source, tmp_path):
        with pytest.raises(ValueError): native.prepare_checkout(source, commit, target, 'worker')
    assert (existing / 'keep').read_text() == 'keep'


@pytest.mark.parametrize('special', ['objects/info/alternates', 'objects/info/http-alternates', 'commondir'])
def test_shared_or_alternate_git_directory_rejected(tmp_path, special):
    source, commit = repo(tmp_path)
    (source / '.git' / special).write_text('elsewhere')
    with pytest.raises(ValueError): native.inspect_source(source, commit)


def tree_commit(source, tree):
    return git(source, 'commit-tree', tree, '-m', 'synthetic tree')


@pytest.mark.parametrize('mode,name', [('120000', 'link'), ('160000', 'submodule'), ('100644', 'CON'), ('100644', 'trailing.'), ('100644', 'C:drive')])
def test_unsupported_git_tree_paths_and_modes(tmp_path, mode, name):
    source, commit = repo(tmp_path)
    blob = git(source, 'hash-object', '-w', '--stdin', stdin=b'target')
    identity, kind = (commit, 'commit') if mode == '160000' else (blob, 'blob')
    tree = git(source, 'mktree', stdin=f'{mode} {kind} {identity}\t{name}\n'.encode())
    with pytest.raises(ValueError): native.inspect_source(source, tree_commit(source, tree))


def test_windows_case_collision_and_limits(tmp_path, monkeypatch):
    source, _ = repo(tmp_path)
    blob = git(source, 'hash-object', '-w', '--stdin', stdin=b'abcdef')
    tree = git(source, 'mktree', stdin=f'100644 blob {blob}\tA\n100644 blob {blob}\ta\n'.encode())
    with pytest.raises(ValueError): native.inspect_source(source, tree_commit(source, tree))
    commit = git(source, 'rev-parse', 'HEAD')
    for key, maximum in [('MAX_FILES', 0), ('MAX_FILE_BYTES', 5), ('MAX_TOTAL_BYTES', 5)]:
        with monkeypatch.context() as patch:
            patch.setattr(native, key, maximum)
            with pytest.raises(ValueError): native.inspect_source(source, commit)


def test_git_symlink_source_rejected(tmp_path):
    source, commit = repo(tmp_path)
    link = tmp_path / 'link'
    try: link.symlink_to(source, target_is_directory=True)
    except OSError: pytest.skip('Host does not permit symlink fixture')
    with pytest.raises(ValueError): native.inspect_source(link, commit)


def test_failure_retained_destination_is_not_reused(tmp_path, monkeypatch):
    source, commit = repo(tmp_path); target = tmp_path / 'out'; original = native._git
    def fail(executable, env, cwd, args, **kwargs):
        if args[0] == 'fetch': raise ValueError('injected')
        return original(executable, env, cwd, args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(native, '_git', fail)
        with pytest.raises(ValueError): native.prepare_checkout(source, commit, target, 'worker')
    assert target.is_dir()
    with pytest.raises(ValueError): native.prepare_checkout(source, commit, target, 'worker')


def test_process_environment_is_clean_bounded_and_file_only(tmp_path, monkeypatch):
    source, commit = repo(tmp_path); original = native.run_process; homes = set()
    monkeypatch.setenv('UNRELATED_SECRET', 'secret')
    def checked(argv, cwd, env, stdin, timeout_seconds, **kwargs):
        assert env['GIT_ALLOW_PROTOCOL'] == 'file' and env['GIT_NO_LAZY_FETCH'] == '1'
        assert 'UNRELATED_SECRET' not in env and 'CODEX_API_KEY' not in env
        assert timeout_seconds == 60 and kwargs['output_limit_bytes'] <= 16 * 1024 * 1024
        homes.add(env['HOME']); return original(argv, cwd, env, stdin, timeout_seconds, **kwargs)
    monkeypatch.setattr(native, 'run_process', checked)
    native.prepare_checkout(source, commit, tmp_path / 'one', 'one')
    native.prepare_checkout(source, commit, tmp_path / 'two', 'two')
    assert len(homes) == 2 and all(not Path(home).exists() for home in homes)


def test_sha256_repository_and_linked_worktree_rejection(tmp_path):
    source = tmp_path / 'sha256'; source.mkdir()
    git(source, 'init', '-q', '--object-format=sha256')
    (source / 'file.txt').write_bytes(b'content')
    git(source, 'add', 'file.txt'); git(source, 'commit', '-qm', 'sha256')
    commit = git(source, 'rev-parse', 'HEAD')
    assert len(commit) == 64
    target = tmp_path / 'independent'
    receipt = native.prepare_checkout(source, commit, target, 'worker')
    assert receipt['commit'] == commit and len(receipt['tree']) == 64
    assert git(target, 'rev-parse', '--show-object-format') == 'sha256'
    linked = tmp_path / 'linked'
    git(source, 'worktree', 'add', '--detach', str(linked), commit)
    assert (linked / '.git').is_file()
    with pytest.raises(ValueError): native.inspect_source(linked, commit)
    # First version also rejects a main repository with attached worktrees.
    with pytest.raises(ValueError): native.inspect_source(source, commit)


def test_unknown_native_process_is_not_known_preparation_failure(tmp_path, monkeypatch):
    source, commit = repo(tmp_path)
    monkeypatch.setattr(native, 'run_process', lambda *args, **kwargs: {'reason': 'unknown', 'exit_code': None, 'stdout': b'', 'stderr': b'SECRET'})
    with pytest.raises(native.PreparationUnknownError): native.inspect_source(source, commit)
    with pytest.raises(native.PreparationUnknownError): native.prepare_checkout(source, commit, tmp_path / 'out', 'worker')
