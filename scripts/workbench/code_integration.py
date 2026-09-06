"""Verify and combine bounded code patches in a new local checkout. Never export/push."""
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import tempfile
import time

from . import artifacts, git_checkout as git
from .code_changes import _tree
from .code_reviews import _manifest
from .process_tree import run_process

ERROR = '代码集成失败；请核查固定基线、完整补丁、冲突、文件边界及大小限制'
MAX_SECONDS = 180


def integrate_code(source_path, base_commit, destination, branch, changes, *, cancel=None):
    """Native capability only: caller must separately authorize integration and trust evidence."""
    try:
        if not isinstance(changes, list) or not 1 <= len(changes) <= 16:
            raise ValueError(ERROR)
        if not isinstance(base_commit, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', base_commit):
            raise ValueError(ERROR)
        if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_/-]{0,119}', branch):
            raise ValueError(ERROR)
        source = git._source(source_path); target = git._safe_path(destination)
        if os.path.lexists(target) or not target.parent.is_dir() or target == source or source in target.parents or target in source.parents:
            raise ValueError(ERROR)
        # Freeze caller-owned values before any asynchronous/native work. No patch body or
        # project path is echoed in exceptions or commits.
        selected, sources, total, common = [], set(), 0, None
        for item in changes:
            if not isinstance(item, dict) or set(item) != {'patch', 'manifest'} or not isinstance(item['patch'], bytes) or len(item['patch']) > artifacts.MAX_FILE_BYTES:
                raise ValueError(ERROR)
            manifest = item['manifest']
            if not isinstance(manifest, dict): raise ValueError(ERROR)
            raw = json.dumps(manifest, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            if len(raw) > artifacts.MAX_FILE_BYTES: raise ValueError(ERROR)
            total += len(raw) + len(item['patch'])
            if total > artifacts.MAX_TOTAL_BYTES: raise ValueError(ERROR)
            manifest = json.loads(raw)
            for key in ('execution_id', 'conversation_id', 'integration_agent_id'): artifacts._identity(manifest[key])
            if type(manifest['repository_revision']) is not int or manifest['repository_revision'] < 1: raise ValueError(ERROR)
            responsibility = tuple(manifest[key] for key in ('conversation_id', 'integration_agent_id', 'repository_revision'))
            if common is not None and responsibility != common: raise ValueError(ERROR)
            common = responsibility
            if manifest['execution_id'] in sources or manifest['base_commit'] != base_commit:
                raise ValueError(ERROR)
            sources.add(manifest['execution_id']); selected.append(dict(patch=item['patch'], manifest=manifest, raw=raw))
        executable = git._executable(); deadline = time.monotonic() + MAX_SECONDS

        def check_time():
            if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()): raise ValueError(ERROR)

        with tempfile.TemporaryDirectory(prefix='corppilot-integrate-', ignore_cleanup_errors=True) as directory:
            home = Path(directory); (home / 'empty').mkdir(); env = git._environment(home, executable)

            def command(executable, env, cwd, args, maximum=16 * 1024 * 1024, data=b''):
                check_time()
                result = run_process([str(executable), *args], cwd, env, data, min(60, deadline - time.monotonic()), cancel=cancel, output_limit_bytes=maximum)
                if result['reason'] == 'unknown':
                    raise git.PreparationUnknownError('Git 集成进程停止未确认；请核查本机进程及新目录，不能自动重试')
                if result['reason'] != 'exited' or result['exit_code'] != 0: raise ValueError(ERROR)
                return result['stdout']

            def run(*args, data=b'', maximum=16 * 1024 * 1024):
                return command(executable, env, target, list(args), maximum, data)

            command(executable, env, home, ['check-ref-format', 'refs/heads/' + branch])
            expected = git._inspect(source, base_commit, executable, env, command=command)
            for item in selected:
                m = item['manifest']
                binding = dict(conversation_id=m['conversation_id'], revision=m['repository_revision'],
                               snapshot=dict(integration_agent_id=m['integration_agent_id'], commit=base_commit, tree=expected['tree']))
                _manifest(item['raw'], item['patch'], {'id': m['execution_id']}, binding)
            check_time(); target.mkdir()  # Failed destinations are retained, never reused.
            run('init', '--quiet', '--template=' + str(home / 'empty'), '--object-format=' + ('sha1' if len(base_commit) == 40 else 'sha256'))
            run('fetch', '--quiet', '--no-tags', '--depth=1', '--no-write-fetch-head', '--no-recurse-submodules', source.as_uri(), base_commit + ':refs/heads/' + branch)
            actual = git._inspect(target, base_commit, executable, env, command=command)
            if any(actual[k] != expected[k] for k in ('commit', 'tree', 'files', 'total_bytes')): raise ValueError(ERROR)
            run('config', '--local', 'user.name', 'CorpPilot Integrator')
            run('config', '--local', 'user.email', 'integrator@corppilot.local')
            # F71 captures raw bytes. Persist the highest-priority local attribute policy
            # so checkout and subsequent ordinary status do not apply EOL/encoding filters.
            (target / '.git/info').mkdir(exist_ok=True)
            (target / '.git/info/attributes').write_bytes(b'* -text -eol -crlf -ident -filter -working-tree-encoding\n')
            base = _tree(run('ls-tree', '-r', '-l', '-z', base_commit))

            def apply(patch):
                if patch: run('apply', '--cached', '--binary', '--whitespace=nowarn', '-', data=patch)

            for item in selected:
                check_time(); m = item['manifest']
                run('read-tree', base_commit); apply(item['patch'])
                tree = run('write-tree').decode().strip()
                if tree != m['target_tree']: raise ValueError(ERROR)
                # A temporary unreferenced commit lets the existing F69 tree validator
                # reject symlinks/submodules/collisions before any checkout writes files.
                proof = run('commit-tree', tree, '-p', base_commit, '-m', 'CorpPilot validation').decode().strip()
                git._inspect(target, proof, executable, env, command=command)
                files = _tree(run('ls-tree', '-r', '-l', '-z', tree))
                changed = {name for name in set(base) | set(files) if base.get(name) != files.get(name)}
                if changed != {row['path'] for row in m['changes']}: raise ValueError(ERROR)
                for row in m['changes']:
                    check_time(); old, new = base.get(row['path']), files.get(row['path'])
                    if row['status'] != ('A' if old is None else 'D' if new is None else 'M'): raise ValueError(ERROR)
                    for side, file in (('old', old), ('new', new)):
                        mode, size, digest = None, None, None
                        if file:
                            mode, oid, size = file
                            data = run('cat-file', 'blob', oid, maximum=git.MAX_FILE_BYTES)
                            if len(data) != size: raise ValueError(ERROR)
                            digest = hashlib.sha256(data).hexdigest()
                        if any(row[side + '_' + key] != value for key, value in (('mode', mode), ('size', size), ('sha256', digest))): raise ValueError(ERROR)
            run('read-tree', base_commit)
            for item in selected: apply(item['patch'])  # No 3-way fallback, reject file, or auto-resolution.
            tree = run('write-tree').decode().strip()
            commit = run('commit-tree', tree, '-p', base_commit, '-m', 'CorpPilot verified code integration').decode().strip()
            final = git._inspect(target, commit, executable, env, command=command)
            run('update-ref', 'refs/heads/' + branch, commit, base_commit)
            run('symbolic-ref', 'HEAD', 'refs/heads/' + branch)
            run('reset', '--hard', '--quiet', commit)
            if (run('rev-parse', 'HEAD').decode().strip() != commit or run('rev-parse', 'HEAD^{tree}').decode().strip() != tree
                    or run('rev-list', '--parents', '-n', '1', 'HEAD').decode().strip() != commit + ' ' + base_commit
                    or run('symbolic-ref', '--short', 'HEAD').decode().strip() != branch
                    or run('status', '--porcelain', '--untracked-files=all')):
                raise ValueError(ERROR)
            git._source(target)
            materialized = _tree(run('ls-tree', '-r', '-l', '-z', tree))
            for name, (_, oid, size) in materialized.items():
                check_time(); path = target.joinpath(*name.split('/'))
                with ExitStack() as locks:
                    for parent in reversed((path.parent, *path.parent.parents)):
                        locks.enter_context(artifacts._locked(parent, True))
                    with artifacts._locked(path, False, git.MAX_FILE_BYTES) as stream:
                        digest = hashlib.new('sha1' if len(base_commit) == 40 else 'sha256')
                        digest.update(b'blob ' + str(size).encode() + b'\0'); counted = 0
                        while chunk := stream.read(512 * 1024):
                            check_time(); counted += len(chunk); digest.update(chunk)
                        if counted != size or digest.hexdigest() != oid: raise ValueError(ERROR)
            return dict(source_path=str(source), base_commit=base_commit, base_tree=expected['tree'], branch=branch,
                        commit=commit, tree=tree, files=final['files'], total_bytes=final['total_bytes'],
                        conversation_id=common[0], integration_agent_id=common[1], repository_revision=common[2],
                        source_execution_ids=[item['manifest']['execution_id'] for item in selected],
                        patch_sha256s=[item['manifest']['patch_sha256'] for item in selected])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError):
        raise ValueError(ERROR) from None
