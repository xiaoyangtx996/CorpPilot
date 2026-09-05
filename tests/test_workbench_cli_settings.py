"""CLI configuration is atomic; version checks never inherit credentials."""
import json
import os
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench import cli_settings
from workbench.cli_settings import CLISettings
from workbench.store import Store


def test_persist_resolve_and_no_implicit_process(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_settings, "run_process", lambda *a, **kw: pytest.fail("implicit execution"))
    monkeypatch.setattr(cli_settings.shutil, "which", lambda _: None)
    monkeypatch.setenv("CLI_TEST_SECRET", "secret-must-not-be-stored")
    store = Store(tmp_path / "data")
    settings = CLISettings(store)
    status = settings.get()
    assert not status["enabled"] and not status["configured"]
    assert status["timeout_seconds"] == 120 and status["max_concurrency"] == 2
    with pytest.raises(ValueError):
        settings.save({"enabled": True})
    executable = tmp_path / "codex.exe"
    saved = settings.save({"executable": str(executable), "model": "chosen-model", "api_key_env": "CLI_TEST_SECRET"})
    assert saved["configured"] and saved["credential_available"] and not saved["executable_available"]
    with pytest.raises(ValueError):
        settings.save({"enabled": True})
    executable.touch()
    if os.name == "nt":
        saved = settings.save({"enabled": True})
        restored = CLISettings(Store(store.data_dir))
        assert restored.get() == saved
        assert restored.resolve()["api_key"] == "secret-must-not-be-stored"
        monkeypatch.setenv("CLI_TEST_SECRET", "  ")
        with pytest.raises(ValueError, match="密钥环境变量未设置"):
            restored.resolve()
    with store.connect() as db:
        dumped = "\n".join(db.iterdump())
    assert "secret-must-not-be-stored" not in dumped + json.dumps(saved)
    settings.save({"enabled": False, "executable": "", "model": "", "api_key_env": ""})
    assert not settings.get()["configured"]


@pytest.mark.parametrize("patch", [None, [], {"api_key": "secret"}, {"args": ["--help"]},
    {"enabled": 1}, {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": 3601},
    {"max_concurrency": 0}, {"max_concurrency": 17}, {"max_concurrency": 1.5},
    {"api_key_env": " KEY "}, {"api_key_env": "9BAD"}, {"model": "a\nb"},
    {"executable": "codex.exe"}, {"executable": "C:/codex.exe --help"},
    {"executable": "C:/codex.cmd"}, {"executable": "C:/bad\n.exe"}])
def test_reject_invalid_patch_atomically(tmp_path, patch):
    settings = CLISettings(Store(tmp_path))
    before = settings.get()
    with pytest.raises(ValueError) as error:
        settings.save(patch)
    assert "secret" not in str(error.value)
    assert settings.get() == before


def test_concurrent_patches_and_version_guard(tmp_path):
    store = Store(tmp_path)
    left, right = CLISettings(store), CLISettings(store)
    barrier = threading.Barrier(2)

    def save(settings, patch):
        barrier.wait()
        settings.save(patch)

    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(save, left, {"model": "parallel-model"})
        two = pool.submit(save, right, {"timeout_seconds": 77})
        one.result()
        two.result()
    assert left.get()["model"] == "parallel-model" and left.get()["timeout_seconds"] == 77
    with store.connect() as db:
        db.execute("UPDATE cli_settings SET version=2")
    with pytest.raises(ValueError, match="版本"):
        left.get()


@pytest.mark.skipif(os.name != "nt", reason="CLI version execution requires Windows")
def test_probe_is_explicit_fixed_bounded_and_secret_free(tmp_path, monkeypatch):
    settings = CLISettings(Store(tmp_path / "data"))
    executable = tmp_path / "fixture.exe"
    executable.touch()
    settings.save({"executable": str(executable)})
    monkeypatch.setenv("CODEX_API_KEY", "ambient-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    calls = []

    def run(argv, cwd, env, stdin, timeout_seconds, **kwargs):
        calls.append((argv, cwd))
        assert argv == [str(executable), "--version"]
        assert stdin == b"" and timeout_seconds == 10 and kwargs["output_limit_bytes"] == 65536
        assert "CODEX_API_KEY" not in env and "OPENAI_API_KEY" not in env
        assert "ambient-secret" not in json.dumps(env)
        assert not list(Path(env["CODEX_HOME"]).iterdir())
        assert cwd.exists() and Path(env["HOME"]).exists()
        return {"reason": "exited", "exit_code": 0, "stdout": b"codex-cli 0.114.0\r\n", "stderr": b"ambient-secret"}

    monkeypatch.setattr(cli_settings, "run_process", run)
    assert settings.probe()["version"] == "0.114.0"
    assert len(calls) == 1 and not calls[0][1].exists()
    cli_settings._PROBE_LOCK.acquire()
    try:
        assert "正在进行" in settings.probe()["message"]
        assert len(calls) == 1
    finally:
        cli_settings._PROBE_LOCK.release()
    monkeypatch.setattr(cli_settings, "run_process", lambda *a, **kw: {
        "reason": "exited", "exit_code": 0, "stdout": b"python 3.12 ambient-secret", "stderr": b"ambient-secret"})
    result = settings.probe()
    assert not result["available"] and "ambient-secret" not in json.dumps(result)
    executable.unlink()
    assert not settings.probe()["available"]


@pytest.mark.skipif(os.name != "nt", reason="CLI version execution requires Windows")
@pytest.mark.parametrize("reason,code,output", [
    ("timeout", None, b"codex-cli 0.114.0"),
    ("output_limit", 0, b"codex-cli 0.114.0"),
    ("exited", 1, b"codex-cli 0.114.0"),
    ("exited", 0, b"codex-cli 0.114.0\nuntrusted extra output"),
])
def test_probe_requires_successful_bounded_version_result(tmp_path, monkeypatch, reason, code, output):
    executable = tmp_path / "fixture.exe"
    executable.touch()
    settings = CLISettings(Store(tmp_path / "data"))
    settings.save({"executable": str(executable)})
    monkeypatch.setattr(cli_settings, "run_process", lambda *a, **kw: {
        "reason": reason, "exit_code": code, "stdout": output, "stderr": b"private-detail"})
    result = settings.probe()
    assert not result["available"] and result["version"] is None
    assert "private-detail" not in json.dumps(result)


@pytest.mark.parametrize('patch', [
    {'backend': 'remote'}, {'docker_executable': 'docker.exe'}, {'docker_image': 'alpine:latest'},
    {'docker_image': '--help'}, {'docker_image': 'sha256:' + 'g' * 64},
    {'docker_cpus': True}, {'docker_cpus': 17}, {'docker_memory_mb': 127},
    {'docker_memory_mb': 32769}, {'docker_pids_limit': 15}, {'docker_pids_limit': 1025},
])
def test_docker_validation_atomic(tmp_path, patch):
    settings = CLISettings(Store(tmp_path))
    before = settings.get()
    with pytest.raises(ValueError):
        settings.save(patch)
    assert settings.get() == before


def test_docker_config_uses_own_executable_and_legacy_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_settings.shutil, 'which', lambda _: None)
    store = Store(tmp_path / 'data')
    settings = CLISettings(store)
    settings.save({'model': 'legacy', 'timeout_seconds': 77})
    assert CLISettings(store).get()['backend'] == 'local'
    executable = tmp_path / 'docker.exe'
    executable.touch()
    monkeypatch.setenv('DOCKER_TEST_KEY', 'never-serialize')
    value = settings.save({'backend': 'docker', 'docker_executable': str(executable),
                           'docker_image': 'repo/name@sha256:' + 'a' * 64, 'api_key_env': 'DOCKER_TEST_KEY'})
    assert value['configured'] and value['executable_available'] and value['executable'] == ''
    assert (value['docker_cpus'], value['docker_memory_mb'], value['docker_pids_limit']) == (1, 1024, 128)
    assert value['timeout_seconds'] == 77
    if os.name == 'nt':
        settings.save({'enabled': True})
        assert settings.resolve()['api_key'] == 'never-serialize'
    assert 'never-serialize' not in json.dumps(settings.get())
    with store.connect() as db:
        assert 'never-serialize' not in '\n'.join(db.iterdump())


@pytest.mark.skipif(os.name != 'nt', reason='Docker process isolation requires Windows')
@pytest.mark.parametrize('mode', ['ok', 'info_failure', 'missing_image', 'wrong_image', 'bad_version', 'output_limit', 'timeout', 'windows_daemon', 'windows_image', 'malformed'])
def test_docker_probe_local_bounded_no_pull_or_credentials(tmp_path, monkeypatch, mode):
    settings = CLISettings(Store(tmp_path / 'data'))
    executable = tmp_path / 'docker.exe'
    executable.touch()
    digest = 'sha256:' + 'a' * 64
    settings.save({'backend': 'docker', 'docker_executable': str(executable), 'docker_image': digest})
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_AUTH_CONFIG', 'OPENAI_API_KEY'):
        monkeypatch.setenv(name, 'ambient-secret')
    calls = []
    def run(argv, cwd, env, stdin, timeout_seconds, **kwargs):
        calls.append(argv)
        assert argv[:3] == [str(executable), '--host', 'npipe:////./pipe/docker_engine']
        assert argv[3] == '--config' and list(Path(argv[4]).iterdir()) == []
        assert not any(key.startswith('DOCKER_') for key in env)
        assert 'ambient-secret' not in json.dumps(env) and 'CODEX_API_KEY' not in env
        assert stdin == b'' and 0 < timeout_seconds <= 10 and 0 < kwargs['output_limit_bytes'] <= 65536
        info = len(calls) == 1
        assert argv[5:] == (['info', '--format', '{"ServerVersion":{{json .ServerVersion}},"OSType":{{json .OSType}}}'] if info else ['image', 'inspect', '--format', '{"Id":{{json .Id}},"Os":{{json .Os}}}', digest])
        data = {'ServerVersion': '27.5.1', 'OSType': 'linux'} if info else {'Id': digest, 'Os': 'linux'}
        if mode == 'bad_version' and info: data['ServerVersion'] = 'secret'
        if mode == 'wrong_image' and not info: data['Id'] = 'sha256:' + 'b' * 64
        if mode == 'windows_daemon' and info: data['OSType'] = 'windows'
        if mode == 'windows_image' and not info: data['Os'] = 'windows'
        output = b'{' if mode == 'malformed' else json.dumps(data).encode()
        return {'reason': mode if mode in ('output_limit', 'timeout') else 'exited',
                'exit_code': 1 if (mode == 'info_failure' and info or mode == 'missing_image' and not info) else 0,
                'stdout': output, 'stderr': b'ambient-secret'}
    monkeypatch.setattr(cli_settings, 'run_process', run)
    result = settings.probe()
    assert result['available'] == (mode == 'ok')
    assert 'ambient-secret' not in json.dumps(result)
    assert len(calls) <= 2
