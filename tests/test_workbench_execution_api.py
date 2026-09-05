"""Real HTTP execution contracts; runner calls are explicit, non-billing fakes."""
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from test_workbench_api import request, running
from test_workbench_executions import setup, request as execution_payload, revise
from test_workbench_cli_controller import result
from workbench import cli_controller
from workbench.cli_settings import CLISettings


def configure(monkeypatch):
    state = {"enabled": True}

    def resolve(self):
        if not state["enabled"]:
            raise ValueError("CLI configuration disabled for test")
        return {"enabled": True, "executable": "C:/fake/codex.exe", "model": "qa-model",
                "api_key": "fake-http-secret", "timeout_seconds": 10, "max_concurrency": 1}

    monkeypatch.setattr(CLISettings, "resolve", resolve)
    return state


def until(port, path, states):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, value = request(port, "GET", path)
        assert status == 200
        if value["state"] in states:
            return value
        time.sleep(.02)
    raise AssertionError(f"Execution did not reach {states}: {value}")


def test_execution_http_runs_once_and_replays_after_disabled_restart(tmp_path, monkeypatch):
    _, _, _, task = setup(tmp_path)
    config = configure(monkeypatch)
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    payload = execution_payload()
    with running(tmp_path) as port:
        status, runtime = request(port, "GET", "/api/workbench/cli-runtime")
        assert status == 200 and runtime["running"] and runtime["active_requests"] == 0
        assert request(port, "GET", path) == (200, [])
        status, created = request(port, "POST", path, payload)
        assert status == 202 and created["attempt"] == 1
        run_path = '/api/workbench/executions/' + created["id"]
        finished = until(port, run_path, {"awaiting_review"})
        assert len(calls) == 1 and calls[0]["execution_id"] == created["id"]
        config["enabled"] = False
        assert request(port, "POST", path, payload) == (202, finished)
        assert request(port, "POST", path, {**payload, "reconciliation_note": "changed"})[0] == 400
        assert request(port, "GET", path) == (200, [finished])
        assert "fake-http-secret" not in str(finished)
    with running(tmp_path) as port:
        assert request(port, "GET", run_path) == (200, finished)
        assert request(port, "POST", path, payload) == (202, finished)
        assert len(calls) == 1


def test_execution_http_requires_config_permission_and_current_version(tmp_path, monkeypatch):
    store, tasks, _, task = setup(tmp_path)
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        assert request(port, "POST", path, execution_payload())[0] == 400
        configure(monkeypatch)
        store.save_agent({"tools": ["read"]}, task["agent_id"])
        assert request(port, "POST", path, execution_payload())[0] == 403
        store.save_agent({"tools": ["read", "execute"]}, task["agent_id"])
        revise(tasks, task)
        assert request(port, "POST", path, execution_payload())[0] == 409
        assert request(port, "GET", path) == (200, [])
        assert calls == []


def test_execution_http_queued_cancel_and_untrusted_mutations(tmp_path, monkeypatch):
    _, _, _, task = setup(tmp_path)
    config = configure(monkeypatch)
    # Hold dispatch so the queue cancellation boundary is deterministic.
    monkeypatch.setattr(cli_controller.CLIController, "tick", lambda self: None)
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        for extra in ({"state": "awaiting_review"}, {"agent_id": task["agent_id"]}, {"exit_code": 0}):
            assert request(port, "POST", path, {**execution_payload(), **extra})[0] == 400
        assert request(port, "POST", path, execution_payload(), headers={"Origin": "http://evil.example"})[0] == 400
        assert request(port, "GET", path)[1] == []
        status, run = request(port, "POST", path, execution_payload())
        assert status == 202 and run["state"] == "queued"
        run_path = '/api/workbench/executions/' + run["id"]
        assert request(port, "POST", run_path + "/report", {"exit_code": 0})[0] == 404
        assert request(port, "PATCH", run_path, {"state": "awaiting_review"})[0] == 404
        assert request(port, "POST", run_path + "/cancel", {"reason": "extra"})[0] == 400
        assert request(port, "GET", run_path)[1]["state"] == "queued"
        config["enabled"] = False
        status, cancelled = request(port, "POST", run_path + "/cancel", {})
        assert status == 200 and cancelled["state"] == "cancelled"
        assert request(port, "POST", run_path + "/cancel", {}) == (200, cancelled)
        assert request(port, "GET", "/api/workbench/executions/missing")[0] == 404
        assert request(port, "GET", "/api/workbench/tasks/missing/executions")[0] == 404


def test_execution_http_running_cancel_waits_for_runner_confirmation(tmp_path, monkeypatch):
    _, _, _, task = setup(tmp_path)
    config = configure(monkeypatch)
    started, observed_cancel, release = Event(), Event(), Event()

    def runner(**kwargs):
        started.set()
        assert kwargs["cancel"].wait(5)
        observed_cancel.set()
        assert release.wait(5)
        return result(code=1, success=False, reason="cancelled")

    monkeypatch.setattr(cli_controller, "run_codex", runner)
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    try:
        with running(tmp_path) as port:
            status, run = request(port, "POST", path, execution_payload())
            assert status == 202 and started.wait(4)
            run_path = '/api/workbench/executions/' + run["id"]
            config["enabled"] = False
            assert request(port, "POST", run_path + "/cancel", {})[1]["state"] == "stopping"
            assert observed_cancel.wait(4)
            assert request(port, "GET", run_path)[1]["state"] == "stopping"
            release.set()
            stopped = until(port, run_path, {"cancelled"})
            assert stopped["exit_code"] == 1
    finally:
        release.set()


def test_execution_http_concurrent_same_key_has_one_runner(tmp_path, monkeypatch):
    _, _, _, task = setup(tmp_path)
    configure(monkeypatch)
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: request(port, "POST", path, execution_payload()), range(8)))
        assert all(status == 202 for status, _ in replies)
        identities = {run["id"] for _, run in replies}
        assert len(identities) == 1
        until(port, '/api/workbench/executions/' + identities.pop(), {"awaiting_review"})
        assert len(request(port, "GET", path)[1]) == 1
        assert len(calls) == 1


def test_execution_http_unknown_cannot_be_replaced_by_owner_note(tmp_path, monkeypatch):
    _, _, executions, task = setup(tmp_path)
    previous = executions.create(task["id"], execution_payload())
    assert executions.claim(previous["id"])
    configure(monkeypatch)
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    path = f'/api/workbench/tasks/{task["id"]}/executions'
    with running(tmp_path) as port:
        # Restart cannot prove the previous process stopped; no replacement may run.
        assert request(port, "GET", path)[1][0]["state"] == "unknown"
        status, replay = request(port, "POST", path, execution_payload())
        assert status == 202 and replay["id"] == previous["id"] and replay["state"] == "unknown"
        assert request(port, "POST", path, execution_payload(request_id="retry",
            note="Owner reviewed effects", previous=previous["id"]))[0] == 400
        assert len(request(port, "GET", path)[1]) == 1 and calls == []
