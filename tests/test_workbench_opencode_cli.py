"""Native OpenCode completion and run-local configuration contract (no paid calls)."""
import copy
import json
from pathlib import Path
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench.cli import InputPreparationError, prepare_workspace
from workbench.opencode_cli import opencode_environment, parse_result, run_opencode


def step(number=1, reason='stop', text='Delivered'):
    def event(kind, **values):
        return {'type': kind, 'sessionID': 'session-one', 'part': {
            'id': f'{number}-{kind}', 'messageID': f'message-{number}', 'sessionID': 'session-one',
            'type': kind.replace('_', '-'), **values}}
    return [event('step_start'), event('text', text=text), event('step_finish', reason=reason,
        tokens={'input': 124, 'output': 24, 'reasoning': 3, 'cache': {'read': 1792, 'write': 2}})]


def process(events=None, **changes):
    return {'exit_code': 0, 'reason': 'exited', 'stderr': b'',
            'stdout': b'\n'.join(json.dumps(event).encode() for event in (step() if events is None else events)), **changes}


def test_native_multistep_result_and_normalized_usage():
    result = parse_result(process([*step(1, 'tool-calls', 'Working'), *step(2)]), 'secret')
    assert result['success'] and result['summary'] == 'Delivered'
    assert result['usage'] == {'input_tokens': 3836, 'output_tokens': 54, 'cached_input_tokens': 3584}
    assert result['tool_activities'] is None  # Not represented as Codex activity.
    result = parse_result(process(step(text='secret result')), ' secret ')
    assert 'secret' not in str(result)


def test_zero_exit_requires_coherent_final_stop():
    valid = step()
    malformed = [[], valid[:1], valid[1:], valid[:-1], [*valid, valid[-1]],
                 [*valid, *step(2)], step(reason='length'), step(reason='tool-calls'),
                 step(text=' '), [*step(1, 'tool-calls'), *step(1)],
                 [*valid, {'type': 'error', 'error': 'secret'}], [True]]
    for field, value in [('sessionID', 'other'), ('part', None), ('type', 'unknown')]:
        events = copy.deepcopy(valid)
        events[1][field] = value
        malformed.append(events)
    for field, value in [('sessionID', 'other'), ('messageID', 'other'), ('type', 'step-start'), ('id', '')]:
        events = copy.deepcopy(valid)
        events[1]['part'][field] = value
        malformed.append(events)
    for events in malformed:
        result = parse_result(process(events), 'secret')
        assert result['success'] is False and result['reason'] == 'protocol_error', events
    for raw in (b'not JSON', b'\xff', b'{"type":"text","type":"step_finish"}', b'{"n":NaN}', b'x' * (4 * 1024 * 1024 + 1)):
        assert not parse_result(process(stdout=raw), 'secret')['success']


@pytest.mark.parametrize('reason', ['cancelled', 'timeout', 'output_limit', 'start_failed', 'unknown'])
def test_process_failure_cannot_become_success(reason):
    result = parse_result(process(reason=reason), 'secret')
    assert not result['success'] and result['reason'] == reason
    assert not parse_result(process(exit_code=7), 'secret')['success']


def test_tool_failures_and_unknown_usage_are_not_fabricated():
    events = step()
    tool = {'type': 'tool_use', 'sessionID': 'session-one', 'part': {'id': 'tool-one',
            'sessionID': 'session-one', 'messageID': 'message-1', 'type': 'tool',
            'tool': 'write', 'state': {'status': 'completed'}}}
    events.insert(1, tool)
    assert parse_result(process(events), 'secret')['success']
    # Some providers label a tool step "stop"; the official CLI still continues.
    assert parse_result(process([*events, *step(2)]), 'secret')['success']
    for status in ('error', 'running', 'unknown'):
        tool['part']['state']['status'] = status
        assert not parse_result(process(events), 'secret')['success']
    for value in (-1, True, '124', 9007199254740992, None):
        events = step()
        events[-1]['part']['tokens']['input'] = value
        result = parse_result(process(events), 'secret')
        assert result['success'] and result['usage'] is None
    events = step()
    del events[-1]['part']['tokens']['cache']
    assert parse_result(process(events), 'secret')['usage'] is None


def test_environment_isolated_and_no_shell_credential_inheritance(tmp_path, monkeypatch):
    monkeypatch.setenv('ProgramData', str(tmp_path / 'system-programdata'))
    for name in ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG_DIR', 'NODE_OPTIONS',
                 'HTTP_PROXY', 'UNRELATED_KEY', 'XDG_CONFIG_HOME'):
        monkeypatch.setenv(name, 'ambient-secret')
    one = prepare_workspace(tmp_path, str(uuid.uuid4()))
    two = prepare_workspace(tmp_path, str(uuid.uuid4()))
    first = opencode_environment(one, 'only-this-key')
    second = opencode_environment(two, 'other-key')
    assert 'ambient-secret' not in str(first)
    for name in ('HOME', 'USERPROFILE', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        assert first[name] != second[name] and Path(first[name]).is_dir()
    assert 'CODEX_API_KEY' not in first and 'CODEX_HOME' not in first
    assert first['ProgramData'] == second['ProgramData'] == str(tmp_path / 'system-programdata')
    config = json.loads(first['OPENCODE_CONFIG_CONTENT'])
    assert config['provider']['opencode']['options']['apiKey'] == '{env:CORPPILOT_ZEN_KEY}'
    assert 'only-this-key' not in first['OPENCODE_CONFIG_CONTENT']
    assert config['permission'] == {'*': 'deny', 'read': 'allow', 'glob': 'allow', 'grep': 'allow',
                                     'edit': 'allow', 'external_directory': 'deny'}
    assert first['OPENCODE_DISABLE_PROJECT_CONFIG'] == first['OPENCODE_DISABLE_CLAUDE_CODE'] == 'true'
    assert config['small_model'] == config['model'] == 'opencode/big-pickle'


@pytest.mark.parametrize('filename', ['opencode.json', 'opencode.jsonc'])
def test_managed_configuration_is_never_shadowed(tmp_path, monkeypatch, filename):
    program_data = tmp_path / 'managed-system'
    managed = program_data / 'opencode'
    managed.mkdir(parents=True)
    (managed / filename).write_text('{}')
    monkeypatch.setenv('ProgramData', str(program_data))
    paths = prepare_workspace(tmp_path / 'runs', str(uuid.uuid4()))
    with pytest.raises(ValueError, match='受管理配置'):
        opencode_environment(paths, 'secret')


def test_runner_argv_stdin_and_preparation_boundaries(tmp_path, monkeypatch):
    import workbench.process_tree as process_tree
    captured = {}
    def run(argv, cwd, env, stdin, timeout, cancel):
        captured.update(argv=argv, cwd=cwd, env=env, stdin=stdin)
        return process()
    monkeypatch.setattr(process_tree, 'run_process', run)
    executable = tmp_path / 'opencode.exe'
    executable.touch()
    result = run_opencode(executable, tmp_path, str(uuid.uuid4()), 'Private task', 'opencode/big-pickle', 'secret', 5)
    assert result['success']
    assert 'secret' not in str(captured['argv']) and 'Private task' not in str(captured['argv'])
    assert captured['stdin'] == b'Private task' and captured['env']['CORPPILOT_ZEN_KEY'] == 'secret'
    assert '--pure' in captured['argv'] and '--format' in captured['argv'] and '--continue' not in captured['argv']
    cancelled = threading.Event(); cancelled.set()
    before = list((tmp_path / 'execution-workspaces').iterdir())
    result = run_opencode(executable, tmp_path, str(uuid.uuid4()), 'task', 'opencode/big-pickle', 'secret', 5, cancelled)
    assert result['reason'] == 'cancelled' and result['workspace'] is None
    assert list((tmp_path / 'execution-workspaces').iterdir()) == before
    with pytest.raises(InputPreparationError):
        run_opencode(executable, tmp_path, '../bad', 'task', 'opencode/big-pickle', 'secret', 5)
    for model in ('other/model', 'opencode/', 'opencode/model --option', 'opencode/a/b'):
        with pytest.raises(ValueError):
            run_opencode(executable, tmp_path, str(uuid.uuid4()), 'task', model, 'secret', 5)


def test_cancellation_during_preparation_never_starts_process(tmp_path, monkeypatch):
    import workbench.opencode_cli as adapter
    import workbench.process_tree as process_tree
    cancel = threading.Event()
    original = adapter.opencode_environment
    def prepare(*args):
        env = original(*args)
        cancel.set()
        return env
    monkeypatch.setattr(adapter, 'opencode_environment', prepare)
    monkeypatch.setattr(process_tree, 'run_process', lambda *args: pytest.fail('Cancelled run was started'))
    executable = tmp_path / 'opencode.exe'; executable.touch()
    result = run_opencode(executable, tmp_path, str(uuid.uuid4()), 'task', 'opencode/big-pickle', 'secret', 5, cancel)
    assert result['reason'] == 'cancelled' and result['workspace'] is None
