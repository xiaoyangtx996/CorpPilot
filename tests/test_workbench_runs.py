"""Persistence, exactly-once delivery, and conversation authority for model replies."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.runs import Runs
from workbench.store import Store


def setup(tmp_path):
    store = Store(tmp_path)
    agent, second = store.agents()[:2]
    conversation = store.save_conversation({"type": "board", "title": "test", "member_ids": [agent["id"], second["id"]]})
    source = store.send_message(conversation["id"], {"content": "Owner question", "request_id": "source"})
    runs = Runs(store)
    payload = {"agent_id": agent["id"], "source_message_id": source["id"], "request_id": "reply"}
    return store, runs, conversation["id"], payload


def test_concurrent_create_claim_finish_are_exactly_once(tmp_path):
    store, runs, cid, payload = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: runs.create(cid, payload), range(8)))
        assert len({run["id"] for run in created}) == 1
        identity = created[0]["id"]
        assert sum(pool.map(lambda _: runs.claim(identity), range(8))) == 1
        finished = list(pool.map(lambda _: runs.finish(identity, "Real response", "test-model", 5, 7), range(8)))
    assert len({run["reply_message_id"] for run in finished}) == 1
    assert len(store.messages(cid)) == 2
    assert store.messages(cid)[-1]["sender_id"] == payload["agent_id"]
    restored = Runs(Store(tmp_path))
    assert restored.get(identity)["usage"] == {"prompt_tokens": 5, "completion_tokens": 7}
    assert restored.list(cid) == [finished[0]]
    with pytest.raises(ValueError, match="不同 Run"):
        runs.create(cid, {**payload, "source_message_id": "different"})


@pytest.mark.parametrize("change", ["remove", "disable", "archive"])
def test_authority_is_rechecked_before_context_and_reply(tmp_path, change):
    store, runs, cid, payload = setup(tmp_path)
    run = runs.create(cid, payload)
    assert runs.claim(run["id"])
    if change == "remove":
        store.set_member(cid, payload["agent_id"], False)
    elif change == "disable":
        store.save_agent({"enabled": False}, payload["agent_id"])
    else:
        store.save_conversation({"archived": True}, cid)
    with pytest.raises((PermissionError, ValueError)):
        runs.create(cid, {**payload, "request_id": "after-revocation"})
    for operation in (lambda: runs.snapshot(run["id"]), lambda: runs.finish(run["id"], "no", "test", None, None)):
        with pytest.raises((PermissionError, ValueError)):
            operation()
    assert len(store.messages(cid)) == 1
    assert runs.fail(run["id"], "authority revoked")["state"] == "failed"
    assert runs.finish(run["id"], "late", "test", 1, 1)["state"] == "failed"
    assert len(store.messages(cid)) == 1


def test_source_and_scope_validation_and_snapshot_window(tmp_path):
    store, runs, cid, payload = setup(tmp_path)
    other = store.save_conversation({"type": "project", "title": "private", "member_ids": [payload["agent_id"]]})
    private = store.send_message(other["id"], {"content": "secret", "request_id": "private"})
    authored = store.send_message(cid, {"content": "agent message", "request_id": "agent"}, payload["agent_id"])
    for source in (private["id"], authored["id"], "missing"):
        with pytest.raises(ValueError, match="Owner"):
            runs.create(cid, {**payload, "source_message_id": source})
    with pytest.raises(ValueError):
        runs.create(cid, {**payload, "instructions": "untrusted"})
    for i in range(105):
        source = store.send_message(cid, {"content": str(i), "request_id": str(i)})
    run = runs.create(cid, {**payload, "source_message_id": source["id"]})
    store.send_message(cid, {"content": "future", "request_id": "future"})
    with pytest.raises(ValueError):
        runs.snapshot(run["id"])
    runs.claim(run["id"])
    snapshot = runs.snapshot(run["id"])
    assert snapshot["context_truncated"] is True
    assert len(snapshot["messages"]) == 100
    assert snapshot["messages"][0]["content"] == "5"
    assert snapshot["messages"][-1]["id"] == source["id"]
    assert all(message["conversation_id"] == cid for message in snapshot["messages"])
    assert snapshot["instructions"] == next(template["instructions"] for template in store.templates()
                                                if template["id"] == snapshot["agent"]["template_id"])


def test_recovery_and_cancellation_do_not_fake_a_stopped_request(tmp_path):
    store, runs, cid, payload = setup(tmp_path)
    running = runs.create(cid, payload)
    queued = runs.create(cid, {**payload, "request_id": "queued"})
    cancelled = runs.create(cid, {**payload, "request_id": "cancelled"})
    assert runs.claim(running["id"])
    with pytest.raises(ValueError, match="无法取消"):
        runs.cancel(running["id"])
    assert runs.get(running["id"])["state"] == "running"
    assert runs.cancel(cancelled["id"])["state"] == "cancelled"
    assert not runs.claim(cancelled["id"])
    restored = Runs(Store(tmp_path))
    assert restored.recover() == 1
    assert restored.recover() == 0
    assert restored.get(running["id"])["state"] == "unknown"
    assert restored.get(queued["id"])["state"] == "queued"
    assert [item["id"] for item in restored.pending()] == [queued["id"]]
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            restored.pending(limit)
    assert restored.finish(running["id"], "late", "test", None, None)["state"] == "unknown"
    assert len(store.messages(cid)) == 1


def test_invalid_results_and_future_schema_are_rejected(tmp_path):
    store, runs, cid, payload = setup(tmp_path)
    run = runs.create(cid, payload)
    runs.claim(run["id"])
    for count in (-1, True, "1"):
        with pytest.raises(ValueError):
            runs.finish(run["id"], "response", "test", count, 1)
    with pytest.raises(ValueError):
        runs.fail(run["id"], "x" * 2001)
    with store.connect() as db:
        db.execute("UPDATE runs_schema_version SET version=999")
    with pytest.raises(ValueError, match="版本"):
        Runs(store)
