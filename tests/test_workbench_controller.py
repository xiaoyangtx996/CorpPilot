"""Real request subprocesses against a local HTTP fixture, never a paid model."""
import time
import pytest

from test_workbench_provider import endpoint
from test_workbench_api import running, request
from workbench.controller import ReplyController
from workbench.settings import Settings
from workbench.store import Store
from workbench.provider import ProviderError
from workbench import controller as controller_module


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.02)
    raise AssertionError("condition not reached before deadline")


def configure(settings, config, monkeypatch):
    monkeypatch.setenv("CORPPILOT_CONTROLLER_TEST", config["api_key"])
    settings.save({"model": config["model"], "base_url": config["base_url"],
                   "max_output_tokens": 100, "timeout_seconds": 3, "rpm": 10, "max_concurrency": 1,
                   "api_key_env": "CORPPILOT_CONTROLLER_TEST", "enabled": True})


def test_real_reply_queue_lock_and_single_completion(tmp_path, monkeypatch):
    output = {"choices": [{"finish_reason": "stop", "message": {"content": "reply through controller"}}]}
    with endpoint(output, delay=0.4) as (config, requests):
        store = Store(tmp_path)
        settings = Settings(store)
        configure(settings, config, monkeypatch)
        controller = ReplyController(store, settings)
        try:
            actor = store.agents()[0]["id"]
            room = store.save_conversation({"type": "dm", "title": "test", "member_ids": [actor]})["id"]
            message = store.send_message(room, {"content": "test question", "request_id": "msg"})
            payload = {"agent_id": actor, "source_message_id": message["id"], "request_id": "run"}
            first = controller.runs.create(room, payload)
            assert controller.runs.create(room, payload)["id"] == first["id"]
            until(lambda: controller.runs.get(first["id"])["state"] == "running")
            second = controller.runs.create(room, {**payload, "request_id": "run2"})
            assert controller.runs.get(second["id"])["state"] == "queued"
            with pytest.raises(ValueError, match="已有运行"):
                ReplyController(store, settings)
            assert controller.runs.get(first["id"])["state"] == "running"
            controller.runs.cancel(second["id"])
            until(lambda: controller.runs.get(first["id"])["state"] == "completed")
            assert len(requests) == 1
            assert len(store.messages(room)) == 2
            assert controller.runs.get(first["id"])["usage"]["prompt_tokens"] is None
        finally:
            controller.close()
        reopened = ReplyController(store, settings)
        reopened.close()


def test_http_run_lifecycle_and_failure_without_fake_message(tmp_path, monkeypatch):
    with endpoint(b"sensitive error body", status=500) as (config, requests):
        with running(tmp_path) as port:
            configure(Settings(Store(tmp_path)), config, monkeypatch)
            _, agents = request(port, "GET", "/api/workbench/agents")
            actor = agents[0]["id"]
            _, room = request(port, "POST", "/api/workbench/conversations", {"type": "dm", "title": "test", "member_ids": [actor]})
            path = "/api/workbench/conversations/" + room["id"]
            _, message = request(port, "POST", path + "/messages", {"content": "question", "request_id": "msg"})
            payload = {"agent_id": actor, "source_message_id": message["id"], "request_id": "request"}
            status, run = request(port, "POST", path + "/runs", payload)
            assert status == 202
            get_run = lambda: request(port, "GET", "/api/workbench/runs/" + run["id"])[1]
            until(lambda: get_run()["state"] == "failed")
            assert "sensitive" not in get_run()["error"]
            assert request(port, "POST", path + "/runs", payload)[1]["id"] == run["id"]
            assert len(requests) == 1
            assert request(port, "GET", path + "/messages")[1] == [message]
            assert request(port, "GET", path + "/runs")[1][0]["state"] == "failed"
            assert request(port, "GET", "/api/workbench/runtime")[1]["running"]


def test_state_write_failure_recovers_without_repeating_provider(tmp_path, monkeypatch):
    with endpoint(b"upstream error", status=500) as (config, requests):
        store = Store(tmp_path)
        settings = Settings(store)
        configure(settings, config, monkeypatch)
        controller = ReplyController(store, settings)
        try:
            original_fail = controller.runs.fail
            writes = []
            def fail_once(*args, **kwargs):
                writes.append(1)
                if len(writes) == 1:
                    raise OSError("injected database failure")
                return original_fail(*args, **kwargs)
            monkeypatch.setattr(controller.runs, "fail", fail_once)
            actor = store.agents()[0]["id"]
            room = store.save_conversation({"type": "dm", "title": "test", "member_ids": [actor]})["id"]
            source = store.send_message(room, {"content": "question", "request_id": "source"})
            run = controller.runs.create(room, {"agent_id": actor, "source_message_id": source["id"], "request_id": "run"})
            until(lambda: controller.runs.get(run["id"])["state"] == "unknown")
            assert len(requests) == 1 and len(writes) == 2
            assert len(store.messages(room)) == 1
        finally:
            controller.close()


def test_failure_releases_slot_and_rpm_blocks_next_attempt(tmp_path, monkeypatch):
    store = Store(tmp_path)
    settings = Settings(store)
    configure(settings, {"model": "test", "base_url": "http://127.0.0.1:1/v1", "api_key": "dummy"}, monkeypatch)
    settings.save({"rpm": 2})
    attempts = []
    def injected_provider(config, snapshot):
        attempts.append(1)
        if len(attempts) == 1:
            raise ProviderError("injected failure")
        return {"content": "ok", "model": "test", "prompt_tokens": 1, "completion_tokens": 1}
    monkeypatch.setattr(controller_module, "run_reply", injected_provider)
    controller = ReplyController(store, settings)
    try:
        actor = store.agents()[0]["id"]
        room = store.save_conversation({"type": "dm", "title": "test", "member_ids": [actor]})["id"]
        source = store.send_message(room, {"content": "question", "request_id": "source"})
        runs = [controller.runs.create(room, {"agent_id": actor, "source_message_id": source["id"], "request_id": str(i)}) for i in range(3)]
        until(lambda: sum(controller.runs.get(run["id"])["state"] in {"completed", "failed"} for run in runs) == 2)
        states = [controller.runs.get(run["id"])["state"] for run in runs]
        assert sorted(states) == ["completed", "failed", "queued"]
        time.sleep(0.5)
        assert len(attempts) == 2
        assert len(store.messages(room)) == 2
    finally:
        controller.close()
