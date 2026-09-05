"""Docker selection persists through the existing CLI settings HTTP contract."""
import json
import sqlite3
import pytest

from test_workbench_api import running, request


def test_unknown_docker_requires_machine_evidence_before_owner_declaration(tmp_path, monkeypatch):
    from test_workbench_reconciliations import unknown
    from workbench.cli_controller import CLIController
    from workbench import cli_controller

    store, _, executions, _, run, ledger, payload = unknown(tmp_path)
    controller = CLIController(store)
    controller.close()
    with store.connect() as db:
        db.execute("INSERT INTO execution_backends VALUES(?,?,?)", (run["id"], "docker", "C:/qa/original-docker.exe"))
        for sql in ("UPDATE execution_backends SET backend='local'", "DELETE FROM execution_backends"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    evidence = {"verified": False, "state": None, "record": {"token": "never-public"}}
    calls = []

    def inspect(executable, path, execution_id):
        assert executable == "C:/qa/original-docker.exe" and execution_id == run["id"]
        assert path == tmp_path / "execution-workspaces" / run["id"] / "docker-worker.json"
        calls.append("inspect")
        return evidence

    def stop(*args, **kwargs):
        calls.append("stop")
        return inspect(*args, **kwargs)

    monkeypatch.setattr(cli_controller, "inspect_worker", inspect)
    monkeypatch.setattr(cli_controller, "stop_worker", stop)
    base = "/api/workbench/executions/" + run["id"]
    with running(tmp_path) as port:
        # Changing global settings cannot reclassify this historical execution.
        assert request(port, "PATCH", "/api/workbench/cli-settings", {"backend": "local"})[0] == 200
        status, observed = request(port, "GET", base + "/worker")
        assert status == 200 and observed["backend"] == "docker" and not observed["verified"]
        assert "never-public" not in json.dumps(observed)
        assert request(port, "POST", base + "/reconciliation", payload)[0] == 400
        assert ledger.get(run["id"]) is None
        evidence.update(verified=True, state={"running": True, "status": "running", "exit_code": 0, "container_id": "a" * 64})
        assert request(port, "GET", base + "/worker")[1]["can_stop"]
        assert request(port, "POST", base + "/reconciliation", payload)[0] == 400
        for invalid in ({}, {"confirm": 1}, {"confirm": False}, {"confirm": True, "extra": True}):
            assert request(port, "POST", base + "/worker/stop", invalid)[0] == 400
        assert "stop" not in calls
        assert request(port, "POST", base + "/worker/stop", {"confirm": True})[1]["state"]["running"]
        assert calls.count("stop") == 1 and ledger.get(run["id"]) is None
        evidence.update(state={"running": False, "status": "absent", "exit_code": None, "container_id": None})
        assert request(port, "GET", base + "/worker")[1]["verified"]
        status, saved = request(port, "POST", base + "/reconciliation", payload)
        assert status == 201
        evidence.update(verified=False, state=None)
        count = len(calls)
        assert request(port, "POST", base + "/reconciliation", payload) == (201, saved)
        assert len(calls) == count  # Historical exact replay doesn't require a live daemon.
        assert executions.get(run["id"]) == run and run["exit_code"] is None


def test_docker_settings_http_roundtrip_without_implicit_execution(tmp_path, monkeypatch):
    from workbench import cli_settings

    monkeypatch.setattr(cli_settings, "run_process", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("Saving settings must not start Docker")))
    monkeypatch.setenv("DOCKER_QA_KEY", "fixture-secret-never-public")
    path = "/api/workbench/cli-settings"
    executable = tmp_path / "docker.exe"
    executable.touch()
    with running(tmp_path / "data") as port:
        status, saved = request(port, "PATCH", path, {
            "backend": "docker", "docker_executable": str(executable),
            "docker_image": "sha256:" + "a" * 64, "docker_cpus": 2,
            "docker_memory_mb": 2048, "docker_pids_limit": 128,
            "model": "qa-model", "api_key_env": "DOCKER_QA_KEY"})
        assert status == 200 and saved["configured"] and saved["credential_available"]
        assert saved["backend"] == "docker" and not saved["enabled"]
        assert "fixture-secret-never-public" not in json.dumps(saved)
        assert request(port, "PATCH", path, {"docker_image": "unreviewed:latest"})[0] == 400
        assert request(port, "GET", path) == (200, saved)
    with running(tmp_path / "data") as port:
        assert request(port, "GET", path) == (200, saved)
