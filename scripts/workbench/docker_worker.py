"""One local Docker container per execution; client exit is never worker exit proof."""
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .cli import InputPreparationError, prepare_workspace, parse_result
from .process_tree import run_process
from .store import _text

HOST = 'npipe:////./pipe/docker_engine'
RUN_LABEL = 'io.corppilot.execution'
TOKEN_LABEL = 'io.corppilot.owner'


def _save(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _command(executable, paths, args, *, stdin=b'', timeout=10, cancel=None, output_limit=65536):
    # Explicit local endpoint and empty per-run config prevent remote context/credential inheritance.
    env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC'}}
    env.update(HOME=str(paths['home']), USERPROFILE=str(paths['home']), TEMP=str(paths['tmp']), TMP=str(paths['tmp']))
    return run_process([str(executable), '--host', HOST, '--config', str(paths.get('docker_config', paths['root'] / 'docker-config')), *args],
                       paths['root'], env, stdin, timeout, cancel, output_limit_bytes=output_limit)


def _inspect(executable, paths, record):
    response = _command(executable, paths, ['container', 'inspect', record['name']])
    if response['reason'] != 'exited' or response['exit_code'] != 0:
        return None
    try:
        rows = json.loads(response['stdout'])
        if not isinstance(rows, list) or len(rows) != 1:
            return None
        row = rows[0]
        labels = row['Config']['Labels']
        if labels.get(RUN_LABEL) != record['execution_id'] or labels.get(TOKEN_LABEL) != record['token']:
            return None
        identity = row['Id']
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
            return None
        if record.get('container_id') and record['container_id'] != identity:
            return None
        if row.get('Image') != record.get('image_id') or not record.get('image_id'):
            return None
        state = row['State']
        if (type(state['Running']) is not bool or type(state['ExitCode']) is not int
                or state.get('Paused') is not False or state.get('Restarting') is not False
                or state.get('Status') not in ('created', 'running', 'exited', 'dead')
                or state['Running'] != (state['Status'] == 'running')
                or not 0 <= state['ExitCode'] <= 255
                or state['Status'] in ('created', 'running') and state['ExitCode'] != 0):
            return None
        return {'container_id': identity, 'running': state['Running'], 'exit_code': state['ExitCode'], 'status': state.get('Status', ''), 'absent': False}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _executable(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() != '.exe':
        raise ValueError('Docker 工具路径无效')
    return path


def _record_path(path, execution_id):
    path = Path(path)
    if not path.is_absolute() or path.name != 'docker-worker.json' or path.parent.parent.name != 'execution-workspaces':
        raise ValueError('Worker 记录路径无效')
    identity = str(uuid.UUID(path.parent.name))
    if identity != path.parent.name or execution_id is not None and identity != execution_id:
        raise ValueError('Worker 记录与执行不一致')
    for node in (path, *path.parents):
        if node.is_symlink() or node.exists() and getattr(node.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Worker 路径不允许重解析点')
    return path, identity


def _read_record(path):
    with path.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('Worker 记录超过上限')
    record = json.loads(raw)
    required = {'version', 'execution_id', 'token', 'name', 'image', 'phase', 'container_id', 'executable'}
    if (not isinstance(record, dict) or not required <= record.keys()
            or record.keys() - required - {'image_id', 'exit_code'}
            or type(record['version']) is not int or record['version'] != 1
            or record['execution_id'] != path.parent.name
            or not isinstance(record['token'], str) or not re.fullmatch('[0-9a-f]{32}', record['token'])
            or record['name'] != f"corppilot-{record['token'][:12]}-{record['execution_id']}"
            or record['phase'] not in ('create_intent', 'created', 'start_intent', 'stopped', 'removed', 'remove_failed')
            or not isinstance(record['image'], str) or not re.fullmatch(r'(?:sha256:|[A-Za-z0-9._:/-]+@sha256:)[0-9a-f]{64}', record['image'])
            or record['container_id'] is not None and (not isinstance(record['container_id'], str) or not re.fullmatch('[0-9a-f]{64}', record['container_id']))
            or 'image_id' in record and (not isinstance(record['image_id'], str) or not re.fullmatch('sha256:[0-9a-f]{64}', record['image_id']))
            or 'exit_code' in record and (type(record['exit_code']) is not int or not 0 <= record['exit_code'] <= 255)):
        raise ValueError('Worker 记录无效')
    _executable(record['executable'])
    return record


def _absent(executable, paths, identity, record):
    args = ['container', 'ls', '--all', '--no-trunc']
    if record is None:
        args += ['--filter', f'label={RUN_LABEL}={identity}']
    result = _command(executable, paths, [*args, '--format', '{{json .}}'])
    if result['reason'] != 'exited' or result['exit_code'] != 0 or len(result['stdout']) > 65536:
        return False
    try:
        rows = [json.loads(line) for line in result['stdout'].decode('utf-8').splitlines() if line.strip()]
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('ID'), str)
                    or not re.fullmatch('[0-9a-f]{64}', row['ID']) or not isinstance(row.get('Names'), str)
                    or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', row['Names'])):
                return False
        if record is None:
            return not rows
        if any(row['ID'] == record['container_id'] or row['Names'] == record['name'] for row in rows):
            return False
        # A lost create response may leave no CID; a renamed instance must still be found by Run label.
        return _absent(executable, paths, identity, None)
    except (ValueError, UnicodeError, TypeError):
        return False


def inspect_worker(executable, record_path, execution_id=None):
    """Read-only daemon evidence; a missing local record never proves absence."""
    unknown = {'verified': False, 'state': None, 'record': None}
    try:
        path, identity = _record_path(record_path, execution_id)
        record = None
        try:
            record = _read_record(path)
        except (OSError, ValueError, KeyError, TypeError):
            if execution_id is None:
                return unknown
        if record is not None:
            original_exe = _executable(record['executable'])
            if executable is not None and Path(executable).resolve() != original_exe.resolve():
                return unknown
            executable = original_exe
        else:
            executable = _executable(executable)
        cwd = next(parent for parent in path.parents if parent.is_dir())
        paths = {'root': cwd, 'home': path.parent / 'home', 'tmp': path.parent / 'tmp',
                 'docker_config': path.parent / 'docker-config'}
        state = _inspect(executable, paths, record) if record is not None else None
        if state is None and _absent(executable, paths, identity, record):
            state = {'running': False, 'status': 'absent', 'exit_code': None, 'container_id': None, 'absent': True}
        public = {key: value for key, value in record.items() if key != 'token'} if record else None
        return {'verified': state is not None, 'state': state, 'record': public}
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return unknown


def stop_worker(executable, record_path, execution_id=None):
    """Explicit recovery stop only after exact identity checks; never start or remove."""
    evidence = inspect_worker(executable, record_path, execution_id)
    if not evidence['verified'] or not evidence['state']['running'] or not evidence['record']:
        return evidence
    paths = {'root': Path(record_path).parent, 'home': Path(record_path).parent / 'home',
             'tmp': Path(record_path).parent / 'tmp'}
    executable = evidence['record']['executable']
    try:
        identity = evidence['state']['container_id']
        _command(executable, paths, ['container', 'stop', '--time', '2', identity], timeout=5)
        evidence = inspect_worker(executable, record_path, execution_id)
        if evidence['verified'] and evidence['state']['running']:
            _command(executable, paths, ['container', 'kill', evidence['state']['container_id']], timeout=5)
            evidence = inspect_worker(executable, record_path, execution_id)
        return evidence
    except Exception:
        return {'verified': False, 'state': None, 'record': evidence['record']}


def run_docker(executable, data_dir, execution_id, prompt, model, api_key, timeout_seconds,
               cancel=None, input_artifacts=None, *, image, cpus=1, memory_mb=1024, pids_limit=128):
    executable = Path(executable)
    if os.name != 'nt' or not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != '.exe':
        raise ValueError('Docker Worker 需要本机 Windows Docker .exe 绝对路径')
    if not isinstance(image, str) or not re.fullmatch(r'(?:sha256:|[A-Za-z0-9._:/-]+@sha256:)[0-9a-f]{64}', image):
        raise ValueError('Docker 镜像必须固定为已安装的 sha256 ID 或摘要')
    prompt, model, api_key = _text(prompt, '任务', 64000), _text(model, '模型', 200), _text(api_key, '凭据', 4096)
    for value, low, high in [(timeout_seconds, 1, 3600), (cpus, 1, 16), (memory_mb, 128, 32768), (pids_limit, 16, 1024)]:
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Docker Worker 资源或时限配置无效')
    if cancel is not None and cancel.is_set():
        return {'success': False, 'exit_code': None, 'reason': 'cancelled', 'summary': '启动前已取消', 'usage': None, 'workspace': None}
    try:
        paths = prepare_workspace(data_dir, execution_id, input_artifacts)
        (paths['root'] / 'docker-config').mkdir()
        inputs = paths['work'] / 'inputs'
        inputs.mkdir(exist_ok=True)
        # Docker --mount is a comma-delimited grammar, never silently reinterpret a host path.
        if ',' in str(paths['work']):
            raise ValueError()
        token = uuid.uuid4().hex
        record = {'version': 1, 'execution_id': execution_id, 'token': token,
                  'name': f'corppilot-{token[:12]}-{execution_id}', 'image': image, 'phase': 'create_intent', 'container_id': None, 'executable': str(executable)}
        record_path = paths['root'] / 'docker-worker.json'
        _save(record_path, record)
    except (OSError, ValueError, TypeError) as exc:
        raise InputPreparationError('Docker 工作区准备失败，尚未请求创建容器') from exc
    process = {'exit_code': None, 'reason': 'unknown', 'stdout': b'', 'stderr': b''}
    stopped = None
    cleanup = 'retained_unknown'
    try:
        image_info = _command(executable, paths, ['image', 'inspect', '--format', '{{json .}}', image])
        images = json.loads(image_info['stdout'])
        image_id = images['Id']
        if (image_info['reason'] != 'exited' or image_info['exit_code'] != 0
                or not isinstance(image_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)
                or images.get('Os') != 'linux' or image.startswith('sha256:') and image != image_id):
            raise ValueError()
        record['image_id'] = image_id
        _save(record_path, record)
    except Exception as exc:
        raise InputPreparationError('固定 Linux 镜像尚未安装或无法验证，未请求创建容器') from exc
    try:
        _command(executable, paths, ['container', 'create', '--pull', 'never', '--name', record['name'],
            '--label', f'{RUN_LABEL}={execution_id}', '--label', f'{TOKEN_LABEL}={token}',
            '--interactive', '--init', '--user', '1000:1000', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges=true', '--cpus', str(cpus), '--memory', f'{memory_mb}m',
            '--memory-swap', f'{memory_mb}m', '--pids-limit', str(pids_limit), '--restart', 'no',
            '--log-driver', 'none', '--network', 'bridge', '--workdir', '/work',
            '--tmpfs', '/home/worker:rw,nosuid,nodev,size=268435456,uid=1000,gid=1000,mode=0700',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=268435456,uid=1000,gid=1000,mode=1777',
            '--mount', f'type=bind,source={paths["work"]},target=/work',
            '--mount', f'type=bind,source={inputs},target=/work/inputs,readonly', image_id])
        state = _inspect(executable, paths, record)
        if state is not None:
            record.update(container_id=state['container_id'], phase='created')
            _save(record_path, record)
        # A lost create response may be reconciled by exact labels; no second create is issued.
        if state is not None and not state['running'] and state['status'] == 'created':
            if cancel is not None and cancel.is_set():
                process['reason'] = 'cancelled'
            else:
                record['phase'] = 'start_intent'; _save(record_path, record)
                process = _command(executable, paths, ['container', 'start', '--attach', '--interactive', state['container_id']],
                    stdin=json.dumps({'prompt': prompt, 'model': model, 'api_key': api_key}, ensure_ascii=False).encode('utf-8'),
                    timeout=timeout_seconds, cancel=cancel, output_limit=4 * 1024 * 1024)
        elif state is None:
            process['reason'] = 'unknown'
    except InputPreparationError:
        raise
    except Exception:
        process['reason'] = 'unknown'
    finally:
        # Even an exception after starting must attempt bounded label-verified cleanup.
        try:
            state = _inspect(executable, paths, record)
            if state is not None and state['running']:
                if process['reason'] == 'exited':
                    process['reason'] = 'unknown'
                _command(executable, paths, ['container', 'stop', '--time', '2', state['container_id']], timeout=5)
                state = _inspect(executable, paths, record)
                if state is not None and state['running']:
                    _command(executable, paths, ['container', 'kill', state['container_id']], timeout=5)
                    state = _inspect(executable, paths, record)
            if state is not None and not state['running'] and state['status'] in ('created', 'exited', 'dead'):
                stopped = state
        except Exception:
            process['reason'] = 'unknown'
    if stopped is not None:
        process['exit_code'] = stopped['exit_code']
        record.update(phase='stopped', container_id=stopped['container_id'], exit_code=stopped['exit_code'])
        try:
            _save(record_path, record)
            removed = _command(executable, paths, ['container', 'rm', stopped['container_id']])
            cleanup = 'removed' if removed['reason'] == 'exited' and removed['exit_code'] == 0 else 'remove_failed'
            record['phase'] = cleanup; _save(record_path, record)
        except Exception:
            cleanup = 'remove_failed'
    else:
        process['exit_code'] = None; process['reason'] = 'unknown'
    result = parse_result(process, api_key)
    result.update(workspace=str(paths['work']), cleanup=cleanup, worker_record=str(record_path))
    if cleanup == 'remove_failed':
        result['summary'] += '；容器已确认停止，但清理失败，保留核查记录'
    return result
