"""Complete, bounded code patches from a confirmed stopped worker; never merge or push."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import unicodedata

from . import artifacts, git_checkout as git
from .process_tree import run_process

ERROR = '代码成果采集失败；请核查固定仓库、文件边界、凭据和大小限制'
MAX_PATCH_BYTES = artifacts.MAX_FILE_BYTES
MAX_METADATA_BYTES = 16 * 1024 * 1024


def _command(executable, env, cwd, args, data=b'', maximum=MAX_METADATA_BYTES, deadline=None, cancel=None):
    remaining = 60 if deadline is None else min(60, deadline - time.monotonic())
    if remaining <= 0 or (cancel is not None and cancel.is_set()):
        raise ValueError(ERROR)
    result = run_process([str(executable), *args], cwd, env, data, remaining, cancel=cancel, output_limit_bytes=maximum)
    if result['reason'] == 'unknown':
        raise git.PreparationUnknownError('Git 代码采集进程停止未确认，必须核查本机进程及副作用')
    if result['reason'] != 'exited' or result['exit_code'] != 0:
        raise ValueError(ERROR)
    return result['stdout']


def _tree(raw):
    result = {}
    for row in raw.split(b'\0'):
        if not row:
            continue
        info, name = row.split(b'\t', 1)
        mode, kind, oid, size = info.split()
        name = git._filename(name)
        if mode not in (b'100644', b'100755') or kind != b'blob' or name in result:
            raise ValueError(ERROR)
        result[name] = (mode.decode(), oid.decode(), int(size))
    if len(result) > git.MAX_FILES or any(v[2] < 0 or v[2] > git.MAX_FILE_BYTES for v in result.values()) or sum(v[2] for v in result.values()) > git.MAX_TOTAL_BYTES:
        raise ValueError(ERROR)
    return result


def capture_code(data_dir, execution_id, repository_binding_record, api_key, cancel=None):
    """Caller must prove the worker process tree has exited; returns in-memory artifacts."""
    try:
        artifacts._identity(execution_id)
        if os.name != 'nt' or not isinstance(api_key, str) or not api_key.strip():
            raise ValueError(ERROR)
        record = repository_binding_record
        if not isinstance(record, dict) or set(record) != {'conversation_id', 'revision', 'request_id', 'created_at', 'snapshot'}:
            raise ValueError(ERROR)
        artifacts._identity(record['conversation_id'])
        if type(record['revision']) is not int or record['revision'] < 1:
            raise ValueError(ERROR)
        snapshot = record['snapshot']
        if not isinstance(snapshot, dict) or set(snapshot) != {'source_path', 'commit', 'tree', 'files', 'total_bytes', 'integration_agent_id'}:
            raise ValueError(ERROR)
        artifacts._identity(snapshot['integration_agent_id'])
        secret = api_key.strip()
        secrets = [secret.encode(encoding) for encoding in ('utf-8', 'utf-16-le', 'utf-16-be')]

        def checked(data):
            if any(value in data for value in secrets):
                raise ValueError(ERROR)
            return data

        worker = git._safe_path(Path(data_dir).absolute() / 'execution-workspaces' / execution_id / 'work' / 'repository')
        executable = git._executable()
        deadline = time.monotonic() + 180

        def check_time():
            if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                raise ValueError(ERROR)

        def command(executable, env, cwd, args, data=b'', maximum=MAX_METADATA_BYTES):
            return _command(executable, env, cwd, args, data, maximum, deadline, cancel)

        with ExitStack() as locks, tempfile.TemporaryDirectory(prefix='corppilot-code-', ignore_cleanup_errors=True) as directory:
            # Deny replacement of the workspace and metadata while Git reads them. Metadata
            # files also reject hardlinks; no worker config is copied into the trusted repo.
            for parent in reversed((worker, *worker.parents)):
                locks.enter_context(artifacts._locked(parent, True))
            git._source(worker)
            pending, entries, metadata_bytes = [worker / '.git'], 0, 0
            while pending:
                check_time()
                node = pending.pop()
                locks.enter_context(artifacts._locked(node, True))
                with os.scandir(node) as scan:
                    children = list(scan)
                for entry in children:
                    check_time()
                    entries += 1
                    if entries > 100000:
                        raise ValueError(ERROR)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    else:
                        stream = locks.enter_context(artifacts._locked(Path(entry.path), False, git.MAX_TOTAL_BYTES))
                        metadata_bytes += os.fstat(stream.fileno()).st_size
                        if metadata_bytes > 2 * git.MAX_TOTAL_BYTES:
                            raise ValueError(ERROR)
            # Ignore rules are input too: reject linked directories/files before Git's
            # matcher can follow a hostile .gitignore. Bound even ignored-tree traversal.
            pending, entries = [worker], 0
            while pending:
                check_time()
                node = pending.pop()
                locks.enter_context(artifacts._locked(node, True))
                with os.scandir(node) as scan:
                    for entry in scan:
                        if node == worker and entry.name == '.git':
                            continue
                        entries += 1
                        check_time()
                        if entries > 100000:
                            raise ValueError(ERROR)
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            raise ValueError(ERROR)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif not stat.S_ISREG(info.st_mode):
                            raise ValueError(ERROR)
                        elif entry.name == '.gitignore':
                            locks.enter_context(artifacts._locked(Path(entry.path), False, MAX_METADATA_BYTES))
                        else:
                            with artifacts._locked(Path(entry.path), False, git.MAX_TOTAL_BYTES):
                                pass
            home = Path(directory); (home / 'empty').mkdir()
            env = git._environment(home, executable)
            worker_prefix = ['--git-dir', str(worker / '.git'), '--work-tree', str(worker)]
            head = command(executable, env, worker, [*worker_prefix, 'rev-parse', '--verify', 'HEAD^{commit}']).decode().strip()
            if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', head):
                raise ValueError(ERROR)
            head_files = _tree(command(executable, env, worker, [*worker_prefix, 'ls-tree', '-r', '-l', '-z', head]))
            trusted = home / 'repository'
            if not isinstance(snapshot['commit'], str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', snapshot['commit']):
                raise ValueError(ERROR)
            trusted.mkdir()
            command(executable, env, trusted, ['init', '--quiet', '--template=' + str(home / 'empty'), '--object-format=' + ('sha1' if len(snapshot['commit']) == 40 else 'sha256')])
            command(executable, env, trusted, ['fetch', '--quiet', '--no-tags', '--depth=1', '--no-write-fetch-head', '--no-recurse-submodules', worker.as_uri(), snapshot['commit'] + ':refs/heads/capture'])
            observed_commit = command(executable, env, trusted, ['rev-parse', '--verify', snapshot['commit'] + '^{commit}']).decode().strip()
            observed_tree = command(executable, env, trusted, ['rev-parse', '--verify', snapshot['commit'] + '^{tree}']).decode().strip()
            if observed_commit != snapshot['commit'] or observed_tree != snapshot['tree']:
                raise ValueError(ERROR)
            base = _tree(command(executable, env, trusted, ['ls-tree', '-r', '-l', '-z', snapshot['commit']]))
            if len(base) != snapshot['files'] or sum(v[2] for v in base.values()) != snapshot['total_bytes']:
                raise ValueError(ERROR)

            # Read index paths/modes only, using clean configuration. This includes newly
            # staged ignored files without executing clean filters or trusting index blobs.
            with artifacts._locked(worker / '.git/index', False, MAX_METADATA_BYTES) as stream:
                (trusted / '.git/index').write_bytes(stream.read(MAX_METADATA_BYTES + 1))
            indexed = {}
            for row in command(executable, env, trusted, ['ls-files', '--stage', '-z']).split(b'\0'):
                if not row:
                    continue
                info, name = row.split(b'\t', 1)
                mode, oid, stage = info.split()
                name = git._filename(name)
                if stage != b'0' or mode not in (b'100644', b'100755') or name in indexed:
                    raise ValueError(ERROR)
                indexed[name] = mode.decode()
            if len(indexed) > git.MAX_FILES:
                raise ValueError(ERROR)
            exclude = worker / '.git/info/exclude'
            if os.path.lexists(exclude):
                with artifacts._locked(exclude, False, MAX_METADATA_BYTES) as stream:
                    (trusted / '.git/info/exclude').write_bytes(stream.read(MAX_METADATA_BYTES + 1))
            # Git's ignore matcher is reused; it does not run repository filters. The
            # temporary index/config ensures fsmonitor and local global-exclude overrides
            # cannot invoke commands or read host configuration.
            others = command(executable, env, trusted, ['--work-tree', str(worker), 'ls-files', '--others', '--exclude-standard', '-z'])
            names = set(base) | set(head_files) | set(indexed)
            names.update(git._filename(raw) for raw in others.split(b'\0') if raw)
            if len(names) > git.MAX_FILES:
                raise ValueError(ERROR)
            collision = {}
            for name in names:
                check_time()
                checked(name.encode())
                parts = name.split('/')
                for index in range(1, len(parts) + 1):
                    spelling = '/'.join(parts[:index]); key = unicodedata.normalize('NFC', spelling).casefold()
                    value = (spelling, index == len(parts))
                    if key in collision and collision[key] != value:
                        raise ValueError(ERROR)
                    collision[key] = value

            command(executable, env, trusted, ['read-tree', '--empty'])
            changes, total, target = [], 0, {}
            raw_dir = home / 'raw'; raw_dir.mkdir()
            for name in sorted(names):
                check_time()
                old = base.get(name)
                actual = worker.joinpath(*name.split('/'))
                data = None
                try:
                    with ExitStack() as file_locks:
                        for parent in reversed(actual.parents):
                            if parent == worker or worker in parent.parents:
                                file_locks.enter_context(artifacts._locked(parent, True))
                        with artifacts._locked(actual, False, git.MAX_FILE_BYTES) as stream:
                            data = checked(stream.read(git.MAX_FILE_BYTES + 1))
                except FileNotFoundError:
                    pass
                if data is not None:
                    total += len(data)
                    if len(data) > git.MAX_FILE_BYTES or total > git.MAX_TOTAL_BYTES:
                        raise ValueError(ERROR)
                    mode = indexed.get(name, head_files.get(name, old or ('100644',))[0])
                    raw_file = raw_dir / str(len(target)); raw_file.write_bytes(data)
                    target[name] = dict(mode=mode, size=len(data), sha256=hashlib.sha256(data).hexdigest(), file=raw_file)
            # Two native commands handle the entire target tree. No filters, newline-
            # ambiguous user paths, or per-file add/update processes are involved.
            if target:
                paths = ''.join(v['file'].as_posix() + '\n' for v in target.values()).encode()
                oids = command(executable, env, trusted, ['hash-object', '-w', '--stdin-paths', '--no-filters'], paths).decode().splitlines()
                if len(oids) != len(target) or any(not re.fullmatch('[0-9a-f]{' + str(len(snapshot['commit'])) + '}', oid) for oid in oids):
                    raise ValueError(ERROR)
                index_rows = []
                for (name, value), oid in zip(target.items(), oids):
                    value['oid'] = oid
                    index_rows.append((value['mode'] + ' ' + oid + '\t' + name).encode() + b'\0')
                command(executable, env, trusted, ['update-index', '-z', '--index-info'], b''.join(index_rows))
            for name in sorted(names):
                check_time()
                old, new = base.get(name), target.get(name)
                if old and new and old[0] == new['mode'] and old[1] == new['oid']:
                    continue
                if old is None and new is None:
                    continue
                old_data = checked(command(executable, env, trusted, ['cat-file', 'blob', old[1]], maximum=git.MAX_FILE_BYTES)) if old else None
                changes.append(dict(path=name, status='A' if old is None else 'D' if new is None else 'M',
                                    old_mode=old[0] if old else None, new_mode=new['mode'] if new else None,
                                    old_size=len(old_data) if old_data is not None else None, new_size=new['size'] if new else None,
                                    old_sha256=hashlib.sha256(old_data).hexdigest() if old_data is not None else None,
                                    new_sha256=new['sha256'] if new else None))
            tree = command(executable, env, trusted, ['write-tree']).decode().strip()
            patch = checked(command(executable, env, trusted, ['diff-tree', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', '--no-renames', '--no-commit-id', '-r', '-p', snapshot['tree'], tree, '--'], maximum=MAX_PATCH_BYTES))
            manifest = dict(version=1, execution_id=execution_id, conversation_id=record['conversation_id'], repository_revision=record['revision'],
                            integration_agent_id=snapshot['integration_agent_id'], base_commit=snapshot['commit'], base_tree=snapshot['tree'],
                            observed_head=head, target_tree=tree, selection='base_head_index_tracked_and_nonignored_new', content='raw_worktree_bytes',
                            changes=changes, patch_sha256=hashlib.sha256(patch).hexdigest(), patch_bytes=len(patch))
            metadata = checked(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
            if len(metadata) > artifacts.MAX_FILE_BYTES or len(patch) > MAX_PATCH_BYTES:
                raise ValueError(ERROR)
            return [{'path': 'corppilot-code/change.patch', 'data': patch}, {'path': 'corppilot-code/manifest.json', 'data': metadata}]
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        raise ValueError(ERROR) from None
