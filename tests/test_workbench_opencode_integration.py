"""OpenCode selection and native receipts keep the existing authority/queue contract."""
import copy
import json
import os

import pytest

from test_workbench_cli_controller import fixture, until, result
from test_workbench_tool_activities import fixture as tool_fixture
from workbench import cli_controller, cli_settings
from workbench.cli_settings import CLISettings
from workbench.store import Store
from workbench.tool_activities import ToolActivities, parse_opencode_tools


def test_engine_selection_persists_without_secrets_or_implicit_start(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_settings, 'run_process', lambda *a, **kw: pytest.fail('implicit process'))
    settings = CLISettings(Store(tmp_path / 'data'))
    assert settings.get()['engine'] == 'codex'
    exe = tmp_path / 'opencode.exe'
    exe.touch()
    monkeypatch.setenv('ZEN_TEST_KEY', 'private-key')
    saved = settings.save({'engine': 'opencode', 'executable': str(exe), 'model': 'opencode/big-pickle', 'api_key_env': 'ZEN_TEST_KEY'})
    assert CLISettings(settings.store).get() == saved
    assert 'private-key' not in json.dumps(saved)
    if os.name == 'nt':
        settings.save({'enabled': True})
        assert settings.resolve()['engine'] == 'opencode'
        with pytest.raises(ValueError, match='Docker'):
            settings.save({'backend': 'docker'})
        assert settings.get()['backend'] == 'local'
        with pytest.raises(ValueError, match='Zen'):
            settings.save({'model': 'other/model'})
        assert settings.get()['model'] == 'opencode/big-pickle'
    with pytest.raises(ValueError, match='engine'):
        settings.save({'engine': 'unknown'})


def native_event(status='completed', session='ses_one'):
    return {'type': 'tool_use', 'sessionID': session, 'part': {
        'id': 'part_private', 'type': 'tool', 'sessionID': session, 'messageID': 'msg_one', 'tool': 'write',
        'state': {'status': status, 'input': {'filePath': 'private-path', 'content': 'private-content'},
                  'output': 'private-output', 'error': 'private-error' if status == 'error' else None}}}


@pytest.mark.skipif(os.name != 'nt', reason='Windows CLI probe')
def test_opencode_version_probe_has_no_credentials(tmp_path, monkeypatch):
    settings = CLISettings(Store(tmp_path / 'data'))
    exe = tmp_path / 'opencode.exe'
    exe.touch()
    settings.save({'engine': 'opencode', 'executable': str(exe)})
    monkeypatch.setenv('OPENCODE_API_KEY', 'ambient-secret')
    def probe(argv, cwd, env, stdin, timeout, **kwargs):
        assert argv == [str(exe), '--version'] and stdin == b'' and timeout == 10
        assert 'CORPPILOT_ZEN_KEY' not in env and 'ambient-secret' not in json.dumps(env)
        assert env['OPENCODE_DISABLE_PROJECT_CONFIG'] == 'true'
        return {'reason': 'exited', 'exit_code': 0, 'stdout': b'1.18.29\n', 'stderr': b''}
    monkeypatch.setattr(cli_settings, 'run_process', probe)
    assert settings.probe()['version'] == '1.18.29'


def test_native_receipts_hash_all_text_and_persist_immutably(tmp_path):
    store, executions, run, receipts = tool_fixture(tmp_path)
    executions.claim(run['id'])
    events = [native_event(), native_event('error')]
    payload = parse_opencode_tools({'stdout': b'\n'.join(json.dumps(e).encode() for e in events), 'reason': 'exited'})
    assert payload['source'] == 'opencode_jsonl'
    assert [e['status'] for e in payload['events']] == ['completed', 'failed']
    assert 'private' not in json.dumps(payload)
    saved = receipts.record(run['id'], run['attempt'], run['requirement_version'], payload)
    assert ToolActivities(store).get(run['id']) == saved
    assert receipts.record(run['id'], run['attempt'], run['requirement_version'], payload) == saved
    bad = copy.deepcopy(payload)
    bad['source'] = 'codex_jsonl'
    with pytest.raises(ValueError, match='来源'):
        receipts.record(run['id'], run['attempt'], run['requirement_version'], bad)


def test_native_receipts_keep_valid_events_before_broken_or_mixed_output():
    raw = json.dumps(native_event()).encode()
    result = parse_opencode_tools({'stdout': raw + b'\n{broken\n' + json.dumps(native_event(session='ses_other')).encode(), 'reason': 'timeout'})
    assert len(result['events']) == 1
    assert result['invalid_lines'] == 2 and result['process_reason'] == 'timeout'
    event = native_event()
    del event['part']['messageID']
    assert parse_opencode_tools({'stdout': json.dumps(event).encode(), 'reason': 'exited'})['invalid_lines'] == 1


def test_controller_uses_opencode_once_and_keeps_original_result_gates(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    config = controller.settings.resolve()
    monkeypatch.setattr(controller.settings, 'resolve', lambda: {**config, 'engine': 'opencode', 'model': 'opencode/big-pickle'})
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: pytest.fail('wrong engine'))
    monkeypatch.setattr(cli_controller, 'run_opencode', lambda **kw: calls.append(kw) or result())
    try:
        until(controller, lambda: controller.executions.get(runs[0]['id'])['state'] == 'awaiting_review')
        assert len(calls) == 1 and calls[0]['execution_id'] == runs[0]['id']
        for _ in range(3):
            controller.tick()
        assert len(calls) == 1
    finally:
        controller.close()


def test_native_runner_receipts_and_artifact_reach_controller(tmp_path, monkeypatch):
    from test_workbench_opencode_cli import step, process
    from workbench import process_tree
    store, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    exe = tmp_path / 'opencode.exe'
    exe.touch()
    config = controller.settings.resolve()
    monkeypatch.setattr(controller.settings, 'resolve', lambda: {**config, 'engine': 'opencode',
                        'model': 'opencode/big-pickle', 'executable': str(exe)})
    def native(argv, cwd, env, stdin, timeout, cancel):
        assert '--pure' in argv and 'not-a-real-key' not in str(argv)
        folder = cwd / 'artifacts'
        folder.mkdir()
        (folder / 'proof.txt').write_text('Native adapter integration evidence')
        events = step()
        tool = native_event()
        tool['sessionID'] = tool['part']['sessionID'] = 'session-one'
        tool['part']['messageID'] = 'message-1'
        events.insert(1, tool)
        return process(events)
    monkeypatch.setattr(process_tree, 'run_process', native)
    try:
        until(controller, lambda: controller.executions.get(runs[0]['id'])['state'] == 'awaiting_review')
        receipt = ToolActivities(store).get(runs[0]['id'])
        assert receipt['payload']['source'] == 'opencode_jsonl'
        assert receipt['payload']['events'][0]['type'] == 'opencode_tool'
        row = controller.executions.get(runs[0]['id'])
        assert row['exit_code'] == 0 and row['usage']['input_tokens'] == 1918
        assert row['usage']['cached_input_tokens'] == 1792
    finally:
        controller.close()
