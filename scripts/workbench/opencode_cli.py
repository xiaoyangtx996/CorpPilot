"""OpenCode 1.18.29 JSONL adapter; local directories are not an OS sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .cli import InputPreparationError, execution_environment, prepare_workspace
from .store import _text
from .tool_activities import MAX_OUTPUT_BYTES, _constant, _pairs, parse_opencode_tools


def opencode_environment(paths, api_key, model='opencode/big-pickle', timeout_seconds=120):
    """Only the selected Zen credential and fresh run-local configuration are inherited."""
    env = execution_environment(paths, api_key)
    env.pop('CODEX_API_KEY')
    env.pop('CODEX_HOME')
    program_data = os.environ.get('ProgramData') or r'C:\ProgramData'
    if not Path(program_data).is_absolute():
        raise ValueError('无法核验 OpenCode 系统受管理配置路径')
    # Preserve organization policy: refuse instead of shadowing or overriding it.
    managed = Path(program_data) / 'opencode'
    if any(os.path.lexists(managed / name) for name in ('opencode.json', 'opencode.jsonc')):
        raise ValueError('检测到 OpenCode 系统受管理配置，须由管理员核对执行权限与隔离兼容性后再启用')
    env['ProgramData'] = program_data
    for name, folder in [('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                         ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')]:
        directory = paths['home'] / folder
        directory.mkdir(exist_ok=True)
        env[name] = str(directory)
    env['OPENCODE_CONFIG_DIR'] = env['XDG_CONFIG_HOME']
    env['OPENCODE_DISABLE_PROJECT_CONFIG'] = 'true'
    env['OPENCODE_DISABLE_CLAUDE_CODE'] = 'true'
    env['CORPPILOT_ZEN_KEY'] = api_key
    # OpenCode shell tools inherit its credentials. Until subprocess credentials can
    # be separated, allow native file tools only, with no shell or nested agents.
    config = {'model': model, 'small_model': model, 'enabled_providers': ['opencode'],
              'share': 'disabled', 'autoupdate': False, 'lsp': False,
              'permission': {'*': 'deny', 'read': 'allow', 'glob': 'allow', 'grep': 'allow',
                             'edit': 'allow', 'external_directory': 'deny'},
              'provider': {'opencode': {'options': {'apiKey': '{env:CORPPILOT_ZEN_KEY}',
                                                  'timeout': timeout_seconds * 1000}}}}
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    return env


def _usage(parts):
    """OpenCode input excludes cache read/write; Workbench input includes both."""
    total = {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0}
    try:
        for part in parts:
            tokens = part['tokens']
            values = [tokens['input'], tokens['output'], tokens['cache']['read'], tokens['cache']['write'], tokens['reasoning']]
            if any(type(value) is not int or not 0 <= value <= 9007199254740991 for value in values):
                return None
            total['input_tokens'] += values[0] + values[2] + values[3]
            total['output_tokens'] += values[1] + values[4]
            total['cached_input_tokens'] += values[2]
        return total if all(value <= 9007199254740991 for value in total.values()) else None
    except (KeyError, TypeError):
        return None


def parse_result(process, api_key):
    """A zero exit is insufficient: require one coherent, finally stopped session."""
    api_key = _text(api_key, 'CLI API 凭据', 4096)
    result = {'success': False, 'exit_code': process['exit_code'], 'reason': process['reason'],
              'summary': 'OpenCode 未产生已确认的完成结果', 'usage': None, 'tool_activities': None}
    if process['reason'] != 'exited' or process['exit_code'] != 0:
        result['summary'] = {'cancelled': 'OpenCode 已停止，请核查执行前已产生的副作用',
                             'timeout': 'OpenCode 超时，请核查已产生的副作用',
                             'output_limit': 'OpenCode 输出超过限制，已请求终止',
                             'start_failed': 'OpenCode 无法启动，请检查工具和运行环境',
                             'unknown': '无法确认 OpenCode 进程树退出，结果未知'}.get(
                                 process['reason'], 'OpenCode 返回非零退出码')
        return result
    result['reason'] = 'protocol_error'
    try:
        raw = process['stdout']
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
            return result
        events = [json.loads(line, object_pairs_hook=_pairs, parse_constant=_constant)
                  for line in raw.decode('utf-8').splitlines() if line.strip()]
        session, active, last_message = None, None, None
        step_tools = False
        identities, messages, finishes, texts = set(), set(), [], []
        for event in events:
            if not isinstance(event, dict) or event.get('type') not in ('step_start', 'step_finish', 'text', 'tool_use', 'reasoning'):
                return result
            part = event.get('part')
            identity = event.get('sessionID')
            if not isinstance(part, dict) or not isinstance(identity, str) or not identity:
                return result
            if session is None:
                session = identity
            if session != identity or part.get('sessionID') != session:
                return result
            part_id, message_id = part.get('id'), part.get('messageID')
            if not isinstance(part_id, str) or not part_id or part_id in identities or not isinstance(message_id, str) or not message_id:
                return result
            identities.add(part_id)
            kind = event['type']
            expected = {'step_start': 'step-start', 'step_finish': 'step-finish', 'tool_use': 'tool'}.get(kind, kind)
            if part.get('type') != expected:
                return result
            if kind == 'step_start':
                if active is not None or message_id in messages or finishes and finishes[-1].get('reason') != 'tool-calls' and not step_tools:
                    return result
                active = message_id
                messages.add(message_id)
                texts = []
                step_tools = False
            elif message_id != active:
                return result
            elif kind == 'step_finish':
                if part.get('reason') not in ('stop', 'tool-calls'):
                    return result
                finishes.append(part)
                last_message, active = active, None
            elif kind == 'text':
                if not isinstance(part.get('text'), str):
                    return result
                texts.append(part['text'])
            elif kind == 'tool_use':
                state = part.get('state')
                if not isinstance(state, dict) or state.get('status') != 'completed':
                    return result
                step_tools = True
        if (active is not None or not finishes or finishes[-1]['reason'] != 'stop'
                or events[-1]['type'] != 'step_finish' or finishes[-1]['messageID'] != last_message
                or not ''.join(texts).strip()):
            return result
        result.update(success=True, reason='exited', usage=_usage(finishes),
                      summary=''.join(texts).strip().replace(api_key, '[凭据已隐藏]')[:2000])
    except (ValueError, TypeError, UnicodeError, KeyError, RecursionError, OverflowError):
        pass
    return result


def run_opencode(executable, data_dir, execution_id, prompt, model, api_key, timeout_seconds,
                 cancel=None, input_artifacts=None, repository=None):
    executable = Path(executable)
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != '.exe':
        raise ValueError('请选择存在的 OpenCode .exe 绝对路径')
    model = _text(model, 'CLI 模型', 200)
    if not re.fullmatch(r'opencode/[A-Za-z0-9][A-Za-z0-9._-]*', model):
        raise ValueError('OpenCode 当前仅支持官方 Zen 的 opencode/模型 标识')
    prompt = _text(prompt, '执行任务', 64000)
    api_key = _text(api_key, 'CLI API 凭据', 4096)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError('CLI 总时限必须为1–3600秒')
    cancelled = {'success': False, 'exit_code': None, 'reason': 'cancelled', 'summary': '启动前已取消',
                 'usage': None, 'tool_activities': None, 'workspace': None}
    if cancel is not None and cancel.is_set():
        return cancelled
    try:
        paths = prepare_workspace(data_dir, execution_id, input_artifacts, repository)
        env = opencode_environment(paths, api_key, model, timeout_seconds)
    except (OSError, ValueError, TypeError) as exc:
        raise InputPreparationError('OpenCode 输入或工作目录准备失败，进程尚未启动') from exc
    if cancel is not None and cancel.is_set():
        return cancelled
    argv = [str(executable), '--pure', '--log-level', 'ERROR', 'run', '--model', model,
            '--format', 'json', '--title', 'CorpPilot execution', '--dir', str(paths['work'])]
    from .process_tree import run_process
    process = run_process(argv, paths['work'], env, prompt.encode('utf-8'), timeout_seconds, cancel)
    result = parse_result(process, api_key)
    result['tool_activities'] = parse_opencode_tools(process)
    result['workspace'] = str(paths['work'])
    return result
