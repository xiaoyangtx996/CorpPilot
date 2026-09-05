"""Persist CLI settings via actual HTTP without triggering work or disclosing keys."""
import sys
from test_workbench_api import request, running


def test_cli_settings_http_restart_and_explicit_probe(tmp_path, monkeypatch):
    from workbench.cli_settings import CLISettings
    calls = []
    monkeypatch.setattr(CLISettings, "probe", lambda self: calls.append(True) or {
        "available": True, "version": "0.153.3", "message": "CLI 版本已确认"})
    monkeypatch.setenv("CORPPILOT_CLI_API_TEST_KEY", "never-return-this-secret")
    path = "/api/workbench/cli-settings"
    with running(tmp_path) as port:
        status, initial = request(port, "GET", path)
        assert status == 200 and initial["enabled"] is False and not calls
        payload = {"model": "test-model", "api_key_env": "CORPPILOT_CLI_API_TEST_KEY",
                   "executable": sys.executable, "max_concurrency": 3, "timeout_seconds": 60}
        status, saved = request(port, "PATCH", path, payload)
        assert status == 200 and saved["credential_available"] and not calls
        assert "never-return-this-secret" not in str(saved)
        assert request(port, "PATCH", path, {"api_key": "must-not-store"})[0] == 400
        assert request(port, "POST", path + "/probe", {"argv": ["bad"]})[0] == 400
        assert not calls
        status, result = request(port, "POST", path + "/probe", {})
        assert status == 200 and result["available"] and calls == [True]
        assert request(port, "GET", path + "/probe")[0] == 404
    with running(tmp_path) as port:
        assert request(port, "GET", path) == (200, saved)
