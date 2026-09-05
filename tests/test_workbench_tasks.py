"""Requirement persistence, authority, immutable history, and concurrent edits."""
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.store import Store
from workbench.tasks import Tasks, TaskVersionConflict


def setup(tmp_path):
    store = Store(tmp_path)
    agents = store.agents()[:2]
    conversation = store.save_conversation({"type": "board", "title": "test", "member_ids": [a["id"] for a in agents]})
    source = store.send_message(conversation["id"], {"content": "Owner requirement", "request_id": "source"})
    payload = {"agent_id": agents[0]["id"], "source_message_id": source["id"], "request_id": "task",
               "title": "Title", "scope": "Scope", "acceptance": "Acceptance"}
    return store, Tasks(store), conversation["id"], payload


def revision(payload, version=1, **changes):
    return {**{key: payload[key] for key in ("title", "scope", "acceptance", "agent_id")},
            "expected_version": version, **changes}


def test_revision_persists_and_original_create_remains_idempotent(tmp_path):
    store, tasks, cid, payload = setup(tmp_path)
    first = tasks.create(cid, payload)
    assert str(uuid.UUID(first["id"])) == first["id"]
    assert first["requirement_version"] == 1
    assert "state" not in first and "creation_payload" not in first
    assert tasks.revise(first["id"], revision(payload)) == first
    second_agent = next(a["id"] for a in store.agents() if a["id"] != payload["agent_id"])
    second = tasks.revise(first["id"], revision(payload, title="New title", scope="New scope",
                                             acceptance="New acceptance", agent_id=second_agent))
    assert second["requirement_version"] == 2
    restored = Tasks(Store(tmp_path))
    assert restored.get(first["id"]) == second
    assert restored.list(cid) == [second]
    assert restored.create(cid, payload) == second
    history = restored.history(first["id"])
    assert [r["requirement_version"] for r in history] == [1, 2]
    for key in ("title", "scope", "acceptance", "agent_id"):
        assert history[0][key] == first[key] and history[1][key] == second[key]
    with pytest.raises(ValueError, match="不同 Task"):
        restored.create(cid, {**payload, "title": "New title"})
    with pytest.raises(TaskVersionConflict):
        restored.revise(first["id"], revision(second))
    with store.connect() as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert db.execute("SELECT version FROM tasks_schema_version").fetchone()[0] == 1
        for statement in ("UPDATE task_revisions SET title='tampered'", "DELETE FROM task_revisions"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(statement)
    assert restored.history(first["id"]) == history


def test_concurrent_create_and_revision_have_one_winner(tmp_path):
    _, tasks, cid, payload = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: tasks.create(cid, payload), range(8)))
    assert len({task["id"] for task in created}) == 1
    identity = created[0]["id"]

    def edit(number):
        try:
            return tasks.revise(identity, revision(payload, title=f"Edit {number}"))
        except TaskVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(edit, range(8)))
    winners = [task for task in results if task]
    assert len(winners) == 1 and winners[0]["requirement_version"] == 2
    assert len(tasks.history(identity)) == 2
    assert tasks.create(cid, payload) == winners[0]


@pytest.mark.parametrize("change", ["remove", "disable", "archive"])
def test_mutations_recheck_authority(tmp_path, change):
    store, tasks, cid, payload = setup(tmp_path)
    task = tasks.create(cid, payload)
    if change == "remove":
        store.set_member(cid, payload["agent_id"], False)
    elif change == "disable":
        store.save_agent({"enabled": False}, payload["agent_id"])
    else:
        store.save_conversation({"archived": True}, cid)
    for operation in (lambda: tasks.create(cid, {**payload, "request_id": "new"}),
                      lambda: tasks.revise(task["id"], revision(payload, title="New"))):
        with pytest.raises((PermissionError, ValueError)):
            operation()
    assert tasks.get(task["id"]) == task
    assert len(tasks.history(task["id"])) == 1


def test_sources_validation_and_missing_records(tmp_path):
    store, tasks, cid, payload = setup(tmp_path)
    other = store.save_conversation({"type": "project", "title": "other", "member_ids": [payload["agent_id"]]})
    private = store.send_message(other["id"], {"content": "private", "request_id": "private"})
    authored = store.send_message(cid, {"content": "agent", "request_id": "agent"}, payload["agent_id"])
    for source in (private["id"], authored["id"], "missing"):
        with pytest.raises(ValueError, match="Owner"):
            tasks.create(cid, {**payload, "source_message_id": source})
    for bad in (None, [], {**payload, "state": "completed"}, {key: value for key, value in payload.items() if key != "title"}):
        with pytest.raises(ValueError):
            tasks.create(cid, bad)
    for key in payload:
        for bad in (None, True, 1, [], " "):
            with pytest.raises(ValueError):
                tasks.create(cid, {**payload, key: bad})
    task = tasks.create(cid, payload)
    for bad in (None, True, 0, -1, 1.0, "1"):
        with pytest.raises(ValueError):
            tasks.revise(task["id"], revision(payload, version=bad))
    for bad in (None, [], {**revision(payload), "state": "completed"}, {"expected_version": 1}):
        with pytest.raises(ValueError):
            tasks.revise(task["id"], bad)
    for key in ("title", "scope", "acceptance", "agent_id"):
        with pytest.raises(ValueError):
            tasks.revise(task["id"], revision(payload, **{key: []}))
    for key, maximum in (("title", 200), ("scope", 16000), ("acceptance", 16000), ("request_id", 120)):
        with pytest.raises(ValueError):
            tasks.create(cid, {**payload, key: "x" * (maximum + 1)})
    assert tasks.list(other["id"]) == []
    for operation in (lambda: tasks.get("missing"), lambda: tasks.history("missing"),
                      lambda: tasks.list("missing"), lambda: tasks.revise("missing", revision(payload))):
        with pytest.raises(KeyError):
            operation()
    with store.connect() as db:
        db.execute("UPDATE tasks_schema_version SET version=99")
    with pytest.raises(ValueError, match="数据库版本"):
        Tasks(store)
