"""Pinned local Git trees into new self-contained checkouts; no source writes or network."""
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata

from .process_tree import run_process

MAX_FILES = 10000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
ERROR = '本地代码仓库检查或准备失败；请核对固定提交、路径和大小限制'


class PreparationUnknownError(RuntimeError):
    """The native Git process tree has not been confirmed stopped."""


def _safe_path(value):
    if not isinstance(value, (str, Path)) or not str(value) or any(ord(c) < 32 for c in str(value)):
        raise ValueError(ERROR)
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError(ERROR)
    path = Path(os.path.abspath(path))
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(ERROR)
    return path


def _source(value):
    path = _safe_path(value)
    git = path / '.git'
    if not path.is_dir() or not git.is_dir():
        raise ValueError(ERROR)
    for name in ('commondir', 'worktrees', 'objects/info/alternates', 'objects/info/http-alternates'):
        if os.path.lexists(git / name):
            raise ValueError(ERROR)
    # Ordinary local object stores only. Never traverse linked metadata directories.
    pending, count = [git], 0
    while pending:
        node = pending.pop(); _safe_path(node)
        if node.is_dir():
            children = list(node.iterdir()); count += len(children)
            if count > 100000:
                raise ValueError(ERROR)
            pending.extend(children)
    return path


def _environment(home, executable):
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT'}}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home), APPDATA=str(home),
               LOCALAPPDATA=str(home), TEMP=str(home), TMP=str(home),
               PATH=os.pathsep.join([str(executable.parent), str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32')]),
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='file',
               GIT_PROTOCOL_FROM_USER='0', GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1', GIT_OPTIONAL_LOCKS='0')
    settings = {'core.hooksPath': str(home / 'empty'), 'core.fsmonitor': 'false', 'core.attributesFile': os.devnull,
                'protocol.allow': 'never', 'protocol.file.allow': 'always', 'credential.helper': '',
                'core.logAllRefUpdates': 'false', 'core.autocrlf': 'false', 'core.safecrlf': 'false',
                'maintenance.auto': 'false', 'gc.auto': '0', 'fetch.writeCommitGraph': 'false',
                'fetch.recurseSubmodules': 'false', 'submodule.recurse': 'false', 'transfer.bundleURI': 'false'}
    env['GIT_CONFIG_COUNT'] = str(len(settings))
    for index, (key, value) in enumerate(settings.items()):
        env[f'GIT_CONFIG_KEY_{index}'] = key; env[f'GIT_CONFIG_VALUE_{index}'] = value
    return env


def _git(executable, env, cwd, args, maximum=2 * 1024 * 1024):
    result = run_process([str(executable), *args], cwd, env, b'', 60, output_limit_bytes=maximum)
    if result['reason'] == 'unknown':
        raise PreparationUnknownError('Git 准备进程退出情况未知，必须核查后再继续')
    if result['reason'] != 'exited' or result['exit_code'] != 0:
        raise ValueError(ERROR)
    return result['stdout']


def _filename(raw):
    path = raw.decode('utf-8')
    parts = path.split('/')
    if len(path) > 1000 or len(parts) > 32:
        raise ValueError(ERROR)
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith(('.', ' '))
                or part.casefold() == '.git' or re.fullmatch(r'(?i)\.?git~[0-9]+', part)
                or any(unicodedata.category(c).startswith('C') or c in '\\:<>"|?*' for c in part)
                or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)):
            raise ValueError(ERROR)
    return path


def _inspect(path, commit, executable, env, command=None):
    command = command or _git
    if not isinstance(commit, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit):
        raise ValueError(ERROR)
    prefix = ['--git-dir', str(path / '.git'), '--work-tree', str(path)]
    observed = command(executable, env, path, [*prefix, 'rev-parse', '--verify', commit + '^{commit}']).decode().strip()
    if observed != commit:
        raise ValueError(ERROR)
    tree = command(executable, env, path, [*prefix, 'rev-parse', '--verify', commit + '^{tree}']).decode().strip()
    if not re.fullmatch('[0-9a-f]{' + str(len(commit)) + '}', tree):
        raise ValueError(ERROR)
    raw = command(executable, env, path, [*prefix, 'ls-tree', '-r', '-l', '-z', commit], maximum=16 * 1024 * 1024)
    files, total, names = 0, 0, {}
    for record in raw.split(b'\0'):
        if not record: continue
        metadata, filename = record.split(b'\t', 1)
        mode, kind, identity, size = metadata.split()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError(ERROR)
        size = int(size); files += 1; total += size
        if size < 0 or size > MAX_FILE_BYTES or files > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError(ERROR)
        name = _filename(filename); pieces = name.split('/')
        for index in range(1, len(pieces) + 1):
            spelling = '/'.join(pieces[:index]); folded = unicodedata.normalize('NFC', spelling).casefold()
            is_file = index == len(pieces)
            previous = names.get(folded)
            if previous is not None and (previous != (spelling, is_file) or is_file):
                raise ValueError(ERROR)
            names[folded] = (spelling, is_file)
    return {'source_path': str(path), 'commit': commit, 'tree': tree, 'files': files, 'total_bytes': total}


def _executable():
    found = shutil.which('git.exe' if os.name == 'nt' else 'git')
    if not found or not Path(found).is_absolute():
        raise ValueError('未找到可用的本机 Git')
    return Path(found)


def inspect_source(source_path, commit):
    try:
        path = _source(source_path); executable = _executable()
        with tempfile.TemporaryDirectory(prefix='corppilot-git-', ignore_cleanup_errors=True) as directory:
            home = Path(directory); (home / 'empty').mkdir()
            return _inspect(path, commit, executable, _environment(home, executable))
    except (OSError, ValueError, TypeError, OverflowError):
        raise ValueError(ERROR) from None


def prepare_checkout(source_path, commit, destination, branch):
    try:
        source = _source(source_path); target = _safe_path(destination); executable = _executable()
        if os.path.lexists(target) or not target.parent.is_dir() or target == source or source in target.parents or target in source.parents:
            raise ValueError(ERROR)
        if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_/-]{0,119}', branch):
            raise ValueError(ERROR)
        with tempfile.TemporaryDirectory(prefix='corppilot-git-', ignore_cleanup_errors=True) as directory:
            home = Path(directory); (home / 'empty').mkdir(); env = _environment(home, executable)
            _git(executable, env, home, ['check-ref-format', 'refs/heads/' + branch])
            expected = _inspect(source, commit, executable, env)
            target.mkdir()  # Failed preparations remain visible and cannot be silently reused.
            _git(executable, env, target, ['init', '--quiet', '--template=' + str(home / 'empty'),
                                         '--object-format=' + ('sha1' if len(commit) == 40 else 'sha256'), str(target)])
            _git(executable, env, target, ['fetch', '--quiet', '--no-tags', '--depth=1', '--no-write-fetch-head',
                                         '--no-recurse-submodules', source.as_uri(), commit + ':refs/heads/' + branch])
            actual = _inspect(target, commit, executable, env)
            if {k: actual[k] for k in ('commit', 'tree', 'files', 'total_bytes')} != {k: expected[k] for k in ('commit', 'tree', 'files', 'total_bytes')}:
                raise ValueError(ERROR)
            _git(executable, env, target, ['checkout', '--quiet', branch])
            _git(executable, env, target, ['config', '--local', 'user.name', 'CorpPilot Worker'])
            _git(executable, env, target, ['config', '--local', 'user.email', 'worker@corppilot.local'])
            if _git(executable, env, target, ['rev-parse', 'HEAD']).decode().strip() != commit:
                raise ValueError(ERROR)
            _source(target)
            return expected
    except (OSError, ValueError, TypeError, OverflowError):
        raise ValueError(ERROR) from None
