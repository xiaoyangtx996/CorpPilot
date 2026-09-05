"""Dispatch behavior with real persistence and a bounded, explicitly fake CLI runner."""
import sqlite3
import sys
import time
from pathlib import Path
from threading import Event, Lock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench import cli_controller
from workbench.cli_controller import CLIController
from test_workbench_executions import setup, request, revise


def result(code=0, success=True, reason="exited"):
    return {"exit_code": code, "success": success, "reason": reason,
            "summary": "Fake runner evidence", "usage": None, "workspace": "C:/qa/work"}


def ready(monkeypatch, controller, concurrency=2):
    monkeypatch.setattr(controller.settings, "resolve", lambda: {
        "enabled": True, "executable": "C:/fake/codex.exe", "model": "qa-model",
        "api_key_env": "QA_FAKE_KEY", "api_key": "not-a-real-key",
        "timeout_seconds": 10, "max_concurrency": concurrency})


@pytest.mark.parametrize("confirmed", [True, False])
def test_docker_dispatch_preserves_review_and_unknown_boundaries(tmp_path, monkeypatch, confirmed):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    config = controller.settings.resolve() | {
        "backend": "docker", "docker_executable": "C:/Docker/docker.exe",
        "docker_image": "sha256:" + "a" * 64, "docker_cpus": 2,
        "docker_memory_mb": 2048, "docker_pids_limit": 128}
    monkeypatch.setattr(controller.settings, "resolve", lambda: config)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: pytest.fail("local fallback"))
    calls, captured = [], []

    def runner(**kwargs):
        calls.append(kwargs)
        return result() if confirmed else result(None, False, "unknown")

    monkeypatch.setattr(cli_controller, "run_docker", runner)
    monkeypatch.setattr(cli_controller, "capture", lambda *args: captured.append(args) or [])
    try:
        expected = "awaiting_review" if confirmed else "unknown"
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == expected)
        for _ in range(3):
            controller.tick()
        assert len(calls) == 1
        call = calls[0]
        assert call["executable"] == config["docker_executable"]
        assert (call["image"], call["cpus"], call["memory_mb"], call["pids_limit"]) == (
            config["docker_image"], 2, 2048, 128)
        assert call["execution_id"] == runs[0]["id"]
        assert "input_artifacts" in call and "memories" in call["prompt"]
        assert config["api_key"] not in call["prompt"]
        assert len(captured) == int(confirmed)
        if not confirmed:
            assert controller.executions.get(runs[0]["id"])["exit_code"] is None
            assert controller.status()["error"]
    finally:
        controller.close()


def until(controller, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.tick()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "Controller did not reach the expected state"


def fixture(tmp_path, monkeypatch, count=1):
    store, tasks, executions, task = setup(tmp_path)
    store.save_agent({"tools": ["read", "write", "execute"]}, task["agent_id"])
    controller = CLIController(store)
    ready(monkeypatch, controller)
    task_list = [task]
    for index in range(1, count):
        task_list.append(tasks.create(task["conversation_id"], {
            **{key: task[key] for key in ("agent_id", "source_message_id", "title", "scope", "acceptance")},
            "request_id": f"task-{index}"}))
    runs = [executions.create(item["id"], request()) for item in task_list]
    return store, tasks, controller, task_list, runs


def test_parallel_limit_and_same_identity_different_tasks(tmp_path, monkeypatch):
    store, tasks, controller, task_list, runs = fixture(tmp_path, monkeypatch, 3)
    release = Event()
    calls = []
    lock = Lock()

    def runner(**kwargs):
        with lock:
            calls.append(kwargs)
        assert release.wait(4)
        return result()

    monkeypatch.setattr(cli_controller, "run_codex", runner)
    try:
        until(controller, lambda: len(calls) == 2)
        for _ in range(5):
            controller.tick()
        assert len(calls) == 2
        states = [controller.executions.get(run["id"])["state"] for run in runs]
        assert sorted(states) == ["queued", "running", "running"]
        assert len({call["execution_id"] for call in calls}) == 2
        assert all(task_list[0]["scope"] in call["prompt"] and task_list[0]["acceptance"] in call["prompt"] for call in calls)
        assert all("not-a-real-key" not in call["prompt"] for call in calls)
        release.set()
        until(controller, lambda: all(controller.executions.get(run["id"])["state"] == "awaiting_review" for run in runs))
        assert len(calls) == 3
        assert [tasks.get(item["id"]) for item in task_list] == task_list
    finally:
        release.set()
        controller.close()


@pytest.mark.parametrize("change", ["version", "disabled", "removed", "tools", "archived"])
def test_queued_authority_change_never_invokes_runner(tmp_path, monkeypatch, change):
    store, tasks, controller, task_list, runs = fixture(tmp_path, monkeypatch)
    task = task_list[0]
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    if change == "version":
        revise(tasks, task)
    elif change == "disabled":
        store.save_agent({"enabled": False}, task["agent_id"])
    elif change == "removed":
        store.set_member(task["conversation_id"], task["agent_id"], False)
    elif change == "tools":
        store.save_agent({"tools": ["read"]}, task["agent_id"])
    else:
        store.save_conversation({"archived": True}, task["conversation_id"])
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] not in ("queued", "running"))
        assert calls == []
        assert controller.executions.get(runs[0]["id"])["state"] == ("superseded" if change == "version" else "failed")
    finally:
        controller.close()


@pytest.mark.parametrize("message", ["CLI 尚未启用", "CLI 密钥环境变量未设置"])
def test_unavailable_settings_leave_queue_visible_and_unstarted(tmp_path, monkeypatch, message):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    calls = []
    def unavailable():
        raise ValueError(message)
    monkeypatch.setattr(controller.settings, "resolve", unavailable)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    try:
        controller.tick()
        assert calls == []
        assert controller.executions.get(runs[0]["id"])["state"] == "queued"
        assert controller.status()["error"]
        assert controller.status()["active_requests"] == 0
    finally:
        controller.close()


@pytest.mark.parametrize("shutdown", [False, True])
def test_running_cancel_waits_for_runner_confirmation(tmp_path, monkeypatch, shutdown):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    started, observed, release = Event(), Event(), Event()
    def runner(**kwargs):
        started.set()
        assert kwargs["cancel"].wait(4)
        observed.set()
        assert release.wait(4)
        return result(-15, False, "cancelled")
    monkeypatch.setattr(cli_controller, "run_codex", runner)
    try:
        until(controller, started.is_set)
        if shutdown:
            release.set()
            controller.close()
        else:
            assert controller.executions.cancel(runs[0]["id"])["state"] == "stopping"
            until(controller, observed.is_set)
            assert controller.executions.get(runs[0]["id"])["state"] == "stopping"
            release.set()
            until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "cancelled")
        assert observed.is_set()
        assert controller.executions.get(runs[0]["id"])["state"] == "cancelled"
        assert controller.executions.get(runs[0]["id"])["exit_code"] == -15
    finally:
        release.set()
        controller.close()


def test_protocol_failure_with_zero_exit_is_failed(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: result(0, False, "protocol_error"))
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "failed")
        assert controller.executions.get(runs[0]["id"])["exit_code"] == 0
    finally:
        controller.close()


def test_report_write_failure_retries_persistence_not_cli(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    calls, writes = [], []
    report = controller.executions.report
    def flaky_report(*args, **kwargs):
        writes.append(True)
        if len(writes) <= 2:
            raise sqlite3.OperationalError("injected database failure")
        return report(*args, **kwargs)
    monkeypatch.setattr(controller.executions, "report", flaky_report)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "awaiting_review")
        assert len(calls) == 1 and len(writes) == 3
        controller.tick()
        assert len(calls) == 1
    finally:
        controller.close()


def test_unknown_predecessor_does_not_allow_replacement_process(tmp_path, monkeypatch):
    _, _, controller, task_list, runs = fixture(tmp_path, monkeypatch, 2)
    calls = []
    executions = controller.executions
    assert executions.claim(runs[0]["id"])
    executions.report(runs[0]["id"], 1, 1, None, "Termination could not be confirmed")
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    try:
        with pytest.raises(ValueError):
            executions.create(task_list[0]["id"], request(request_id="next", previous=runs[0]["id"], note="Owner reviewed effects"))
        for _ in range(5):
            controller.tick()
            time.sleep(.01)
        assert calls == []
        assert executions.get(runs[1]["id"])["state"] == "queued"
        assert executions.get(runs[0]["id"])["state"] == "unknown"
        assert controller.status()["error"]
    finally:
        controller.close()


@pytest.mark.parametrize("change", ["version", "tools", "removed"])
def test_running_authority_change_signals_stop_and_rejects_old_result(tmp_path, monkeypatch, change):
    store, tasks, controller, task_list, runs = fixture(tmp_path, monkeypatch)
    started, observed, release = Event(), Event(), Event()
    def runner(**kwargs):
        started.set()
        assert kwargs["cancel"].wait(4)
        observed.set()
        assert release.wait(4)
        # Even an exit-zero result racing with revocation cannot reach review.
        return result()
    monkeypatch.setattr(cli_controller, "run_codex", runner)
    try:
        until(controller, started.is_set)
        task = task_list[0]
        if change == "version":
            updated = revise(tasks, task)
        elif change == "tools":
            store.save_agent({"tools": ["read"]}, task["agent_id"])
        else:
            store.set_member(task["conversation_id"], task["agent_id"], False)
        until(controller, observed.is_set)
        assert controller.executions.get(runs[0]["id"])["state"] != "awaiting_review"
        release.set()
        expected = "superseded" if change == "version" else "failed"
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == expected)
        if change == "version":
            assert tasks.get(task["id"]) == updated
    finally:
        release.set()
        controller.close()


def test_runner_exception_is_unknown_and_never_replayed(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("untrusted output not-a-real-key")
    monkeypatch.setattr(cli_controller, "run_codex", runner)
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "unknown")
        for _ in range(3):
            controller.tick()
        run = controller.executions.get(runs[0]["id"])
        assert len(calls) == 1
        assert run["exit_code"] is None
        assert "not-a-real-key" not in str(run) + str(controller.status())
    finally:
        controller.close()


def test_report_response_lost_after_commit_does_not_replay_cli(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    calls, writes = [], []
    original = controller.executions.report
    def response_lost(*args, **kwargs):
        writes.append(True)
        saved = original(*args, **kwargs)
        if len(writes) == 1:
            raise sqlite3.OperationalError("injected response lost after commit")
        return saved
    monkeypatch.setattr(controller.executions, "report", response_lost)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    try:
        until(controller, lambda: len(writes) >= 2)
        assert controller.executions.get(runs[0]["id"])["state"] == "awaiting_review"
        assert len(calls) == 1 and len(writes) == 2
        assert controller.status()["active_requests"] == 0
    finally:
        controller.close()


def test_legacy_queued_successor_of_unknown_is_not_claimed(tmp_path, monkeypatch):
    store, _, controller, task_list, runs = fixture(tmp_path, monkeypatch)
    executions = controller.executions
    executions.cancel(runs[0]["id"])
    successor = executions.create(task_list[0]["id"], request(request_id="next", previous=runs[0]["id"], note="Previous attempt did not start"))
    # Emulate a queued successor saved by the earlier schema behavior.
    with store.connect() as db:
        db.execute("UPDATE task_executions SET state='unknown' WHERE id=?", (runs[0]["id"],))
    calls = []
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: calls.append(kw) or result())
    try:
        assert not executions.claim(successor["id"])
        controller.tick()
        assert calls == []
        assert executions.get(successor["id"])["state"] != "running"
    finally:
        controller.close()
