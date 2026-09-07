"""Container entrypoint contracts with no Linux filesystem writes or CLI calls."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def entry(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'docker/worker/entrypoint.py'
    spec = importlib.util.spec_from_file_location('opencode_entrypoint_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls, directories = [], []
    monkeypatch.setattr(module.os, 'makedirs', lambda path, **kw: directories.append((path, kw)))
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: False)
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0))
    return module, calls, directories


def invoke(entry, monkeypatch, request):
    module, _, _ = entry
    raw = request if isinstance(request, bytes) else json.dumps(request).encode('utf-8')
    monkeypatch.setattr(module.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
    return module.main()


def request(**changes):
    return {'prompt': 'Read inputs and write 输出.txt. {env:AMBIENT_SECRET} {file:/etc/secret}',
            'model': 'opencode/big-pickle', 'api_key': 'TEST_ZEN_PRIVATE', 'engine': 'opencode', **changes}


def test_native_argv_private_stdin_and_isolated_configuration(entry, monkeypatch):
    monkeypatch.setenv('AMBIENT_SECRET', 'do-not-inherit')
    monkeypatch.setenv('OPENCODE_CONFIG', '/ambient/config.json')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'do-not-inherit')
    payload = request()
    assert invoke(entry, monkeypatch, payload) == 0
    _, calls, directories = entry
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == ['opencode', '--pure', '--log-level', 'ERROR', 'run', '--model', 'opencode/big-pickle',
                    '--format', 'json', '--title', 'CorpPilot execution', '--dir', '/work']
    assert options['input'] == payload['prompt'].encode('utf-8')
    assert options['cwd'] == '/work'
    env = options['env']
    assert env['CORPPILOT_ZEN_KEY'] == payload['api_key']
    assert payload['api_key'] not in str(argv) + env['OPENCODE_CONFIG_CONTENT']
    assert payload['prompt'] not in str(argv) + env['OPENCODE_CONFIG_CONTENT']
    assert set(env) == {'PATH', 'HOME', 'TMPDIR', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL',
                        'CORPPILOT_ZEN_KEY', 'OPENCODE_DISABLE_PROJECT_CONFIG', 'OPENCODE_DISABLE_CLAUDE_CODE',
                        'OPENCODE_CONFIG_DIR', 'OPENCODE_CONFIG_CONTENT', 'XDG_DATA_HOME',
                        'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'}
    assert env['HOME'] == '/home/worker' and env['TMPDIR'] == '/tmp'
    assert env['OPENCODE_DISABLE_PROJECT_CONFIG'] == env['OPENCODE_DISABLE_CLAUDE_CODE'] == 'true'
    assert env['OPENCODE_CONFIG_DIR'] == env['XDG_CONFIG_HOME'] == '/home/worker/config'
    assert directories == [(f'/home/worker/{folder}', {'mode': 0o700, 'exist_ok': True})
                           for folder in ('data', 'config', 'cache', 'state')]
    config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
    assert config['model'] == config['small_model'] == payload['model']
    assert config['enabled_providers'] == ['opencode']
    assert config['permission'] == {'*': 'deny', 'read': 'allow', 'glob': 'allow', 'grep': 'allow',
                                    'edit': 'allow', 'external_directory': 'deny'}
    assert config['share'] == 'disabled' and config['autoupdate'] is False and config['lsp'] is False
    assert config['provider'] == {'opencode': {'options': {'apiKey': '{env:CORPPILOT_ZEN_KEY}'}}}


@pytest.mark.parametrize('payload', [request(engine='codex'), request(engine='unknown'),
    request(engine=None), request(extra=True), request(model='openai/gpt-5'),
    request(model='opencode/../secret'), request(model='opencode/{env:SECRET}'),
    request(model='opencode/big-pickle\n'), request(prompt=' '), request(prompt='x' * 64001),
    request(api_key=''), request(api_key=42), [], None, b'{malformed', b'x' * 512001],
    ids=['explicit-codex', 'unknown-engine', 'null-engine', 'extra-field', 'non-zen', 'model-path',
         'model-substitution', 'model-newline', 'blank-prompt', 'large-prompt', 'blank-key',
         'non-string-key', 'array', 'null', 'malformed-json', 'large-request'])
def test_invalid_payload_never_starts_or_creates_directories(entry, monkeypatch, capsys, payload):
    assert invoke(entry, monkeypatch, payload) == 125
    assert entry[1:] == ([], [])
    assert capsys.readouterr().err == 'Worker input or CLI startup failed\n'


@pytest.mark.parametrize('name', ['opencode.json', 'opencode.jsonc'])
def test_managed_policy_is_not_overridden(entry, monkeypatch, capsys, name):
    monkeypatch.setattr(entry[0].os.path, 'lexists', lambda path: path == '/etc/opencode/' + name)
    assert invoke(entry, monkeypatch, request()) == 125
    assert entry[1:] == ([], [])
    assert 'administrator review required' in capsys.readouterr().err


@pytest.mark.parametrize('exit_code,expected', [(0, 0), (7, 7), (-9, 137), (-15, 143)])
def test_process_exit_semantics(entry, monkeypatch, exit_code, expected):
    monkeypatch.setattr(entry[0].subprocess, 'run', lambda *args, **kw: SimpleNamespace(returncode=exit_code))
    assert invoke(entry, monkeypatch, request()) == expected


@pytest.mark.parametrize('failure', ['process', 'directory'])
def test_startup_exception_never_prints_credentials(entry, monkeypatch, capsys, failure):
    def fail(*args, **kwargs):
        raise OSError('TEST_ZEN_PRIVATE')
    if failure == 'process':
        monkeypatch.setattr(entry[0].subprocess, 'run', fail)
    else:
        monkeypatch.setattr(entry[0].os, 'makedirs', fail)
    assert invoke(entry, monkeypatch, request()) == 125
    assert capsys.readouterr().err == 'Worker input or CLI startup failed\n'


def test_duplicate_payload_fields_are_rejected(entry, monkeypatch):
    raw = json.dumps(request()).encode('utf-8')
    assert invoke(entry, monkeypatch, raw[:-1] + b', "engine": "opencode"}') == 125
    assert entry[1:] == ([], [])


def test_legacy_codex_three_field_protocol(entry, monkeypatch):
    payload = {'prompt': 'legacy task', 'model': 'legacy-model', 'api_key': 'LEGACY_PRIVATE'}
    assert invoke(entry, monkeypatch, payload) == 0
    argv, options = entry[1][0]
    assert argv[:2] == ['codex', 'exec'] and argv[-1] == '-'
    assert options['input'] == b'legacy task' and options['cwd'] == '/work'
    assert options['env']['CODEX_API_KEY'] == 'LEGACY_PRIVATE'
    assert options['env']['CODEX_HOME'] == '/home/worker/.codex'
    assert 'CORPPILOT_ZEN_KEY' not in options['env'] and 'LEGACY_PRIVATE' not in str(argv)


def test_opencode_image_pins_only_the_supported_client():
    directory = Path(__file__).resolve().parents[1] / 'docker/worker'
    dockerfile = (directory / 'Dockerfile.opencode').read_text(encoding='utf-8')
    assert 'opencode-ai@1.18.29' in dockerfile and '@openai/codex' not in dockerfile
    assert 'LABEL io.corppilot.opencode="1.18.29"' in dockerfile
    assert 'COPY entrypoint.py /opt/corppilot/entrypoint.py' in dockerfile
    assert 'USER 1000:1000' in dockerfile and 'WORKDIR /work' in dockerfile
