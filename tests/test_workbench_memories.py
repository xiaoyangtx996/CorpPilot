"""Approved knowledge remains scoped, versioned and frozen independently of candidates."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_reviews import ready
from test_workbench_executions import request, revise
from workbench.memories import Memories, freeze, snapshot
from workbench.store import Store
from workbench.tasks import TaskVersionConflict


def setup(tmp_path):
    store, tasks, executions, task, run, reviews, review = ready(tmp_path)
    reviews.save(run["id"], review)
    return store, tasks, executions, task, run, Memories(store)


def proposal(run, key="proposal", version=0, content="Approved learning"):
    return dict(request_id=key, expected_version=version, source_execution_id=run["id"], content=content)


def decision(key="decision", value="approved"):
    return dict(request_id=key, decision=value, note="Owner inspected provenance and content")


def test_approval_replay_rollback_restart_and_rename(tmp_path):
    store, _, _, task, run, memory = setup(tmp_path)
    identity = task["agent_id"]
    assert memory.get("agent", identity)["version"] == 0
    p = proposal(run)
    candidate = memory.propose("agent", identity, p)
    assert memory.get("agent", identity)["content"] == ""
    assert memory.candidate(candidate["id"])["decision"] is None
    d = decision()
    approved = memory.decide(candidate["id"], d)
    assert approved["result_version"] == 1
    assert memory.get("agent", identity)["content"] == p["content"]
    rollback = dict(request_id="rollback", expected_version=1, target_version=0, note="Restore baseline")
    result = memory.rollback("agent", identity, rollback)
    assert result["version"] == 2 and result["content"] == ""
    store.save_agent({"name": "Renamed", "enabled": False}, identity)
    reopened = Memories(Store(tmp_path))
    assert reopened.propose("agent", identity, p) == candidate
    assert reopened.decide(candidate["id"], d) == approved
    assert reopened.rollback("agent", identity, rollback) == result
    assert [r["version"] for r in reopened.history("agent", identity)] == [1, 2]
    assert reopened.candidates("agent", identity)[0]["decision"] == approved
    for operation in (lambda: reopened.propose("agent", identity, {**p, "content": "Different"}),
                      lambda: reopened.decide(candidate["id"], {**d, "decision": "rejected"}),
                      lambda: reopened.rollback("agent", identity, {**rollback, "target_version": 1})):
        with pytest.raises(ValueError):
            operation()


def test_competing_candidates_require_new_version_and_stale_rejection(tmp_path):
    _, tasks, _, task, run, memory = setup(tmp_path)
    first = memory.propose("agent", task["agent_id"], proposal(run, "one"))
    second = memory.propose("agent", task["agent_id"], proposal(run, "two"))
    memory.decide(first["id"], decision("one-decision"))
    with pytest.raises(TaskVersionConflict):
        memory.decide(second["id"], decision("two-decision"))
    revise(tasks, task)
    rejected = memory.decide(second["id"], decision("two-reject", "rejected"))
    assert rejected["result_version"] == 1
    assert memory.get("agent", task["agent_id"])["version"] == 1


@pytest.mark.parametrize("change", ["version", "newer", "disabled", "removed", "archived", "tools"])
def test_approval_rechecks_source_and_rejection_can_close_stale(tmp_path, change):
    store, tasks, executions, task, run, memory = setup(tmp_path)
    candidate = memory.propose("agent", task["agent_id"], proposal(run))
    if change == "version":
        revise(tasks, task)
    elif change == "newer":
        executions.create(task["id"], request(request_id="newer", note="Effects inspected", previous=run["id"]))
    elif change == "disabled":
        store.save_agent({"enabled": False}, task["agent_id"])
    elif change == "removed":
        store.set_member(task["conversation_id"], task["agent_id"], False)
    elif change == "archived":
        store.save_conversation({"archived": True}, task["conversation_id"])
    else:
        store.save_agent({"tools": ["read"]}, task["agent_id"])
    with pytest.raises((ValueError, PermissionError)):
        memory.decide(candidate["id"], decision())
    with pytest.raises((ValueError, PermissionError)):
        memory.propose("agent", task["agent_id"], proposal(run, "new"))
    assert memory.decide(candidate["id"], decision("reject", "rejected"))["decision"] == "rejected"


def test_foreign_scope_dm_and_unapproved_source(tmp_path):
    store, _, _, task, run, memory = setup(tmp_path)
    other = next(a["id"] for a in store.agents() if a["id"] != task["agent_id"])
    room = store.save_conversation(dict(type="project", title="Other", member_ids=[task["agent_id"]]))
    dm = store.save_conversation(dict(type="dm", title="Private", member_ids=[task["agent_id"]]))
    for scope, identity in (("agent", other), ("project", room["id"]), ("project", dm["id"]), ("invalid", other)):
        with pytest.raises((ValueError, PermissionError)):
            memory.propose(scope, identity, proposal(run))
    assert memory.get("agent", other)["content"] == ""
    candidate = memory.propose("project", task["conversation_id"], proposal(run))
    memory.decide(candidate["id"], decision())
    assert memory.get("project", room["id"])["content"] == ""


def test_unapproved_and_corrupt_sources_fail_closed(tmp_path):
    store, _, _, task, run, _, _ = ready(tmp_path)
    memory = Memories(store)
    with pytest.raises(ValueError):
        memory.propose("agent", task["agent_id"], proposal(run))
    # A separate approved source is later corrupted to simulate an invalid snapshot.
    store, _, _, task, run, memory = setup(tmp_path / "approved")
    candidate = memory.propose("agent", task["agent_id"], proposal(run))
    with store.connect() as db:
        db.execute("DROP TRIGGER artifacts_no_update")
        db.execute("UPDATE execution_artifacts SET content=?", (b"corrupt",))
    with pytest.raises(ValueError):
        memory.decide(candidate["id"], decision())


def test_immutable_rows_and_concurrent_decision(tmp_path):
    store, _, _, task, run, memory = setup(tmp_path)
    candidate = memory.propose("agent", task["agent_id"], proposal(run))
    def choose(i):
        try:
            return memory.decide(candidate["id"], decision(str(i), "approved" if i % 2 else "rejected"))
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(item is not None for item in pool.map(choose, range(8))) == 1
    with store.connect() as db:
        for table in ("memory_candidates", "memory_decisions", "memory_requests"):
            row = db.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
            for sql, values in ((f"DELETE FROM {table}", ()),
                    (f"UPDATE {table} SET {row.keys()[0]}={row.keys()[0]}", ()),
                    (f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' for _ in row)})", tuple(row))):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(sql, values)


def test_freeze_isolated_versions_and_empty_binding(tmp_path):
    store, _, executions, task, run, memory = setup(tmp_path)
    # Claim-time binding of the first execution must stay empty after memory approval.
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM execution_memory_snapshots WHERE execution_id=?", (run["id"],)).fetchone():
            freeze(db, run, task["conversation_id"])
        assert snapshot(db, run, task["conversation_id"]) == []
    for scope, identity in (("agent", task["agent_id"]), ("project", task["conversation_id"])):
        candidate = memory.propose(scope, identity, proposal(run, scope, content=scope + " memory"))
        memory.decide(candidate["id"], decision(scope + "-decision"))
    with store.connect() as db:
        assert snapshot(db, run, task["conversation_id"]) == []
    newer = executions.create(task["id"], request(request_id="new", note="Checked effects", previous=run["id"]))
    with store.connect() as db:
        freeze(db, newer, task["conversation_id"])
        values = snapshot(db, newer, task["conversation_id"])
        assert {r["content"] for r in values} == {"agent memory", "project memory"}
        with pytest.raises(sqlite3.IntegrityError):
            freeze(db, newer, task["conversation_id"])
        with pytest.raises(PermissionError):
            snapshot(db, newer, "foreign")
    memory.rollback("agent", task["agent_id"], dict(request_id="rollback", expected_version=1, target_version=0, note="rollback"))
    with store.connect() as db:
        assert snapshot(db, newer, task["conversation_id"]) == values


@pytest.mark.parametrize("delta", [{"expected_version": True}, {"expected_version": -1}, {"extra": "bad"},
                                    {"content": " "}, {"content": "x" * 8001}, {"source_execution_id": ""}])
def test_invalid_proposal(tmp_path, delta):
    _, _, _, task, run, memory = setup(tmp_path)
    with pytest.raises(ValueError):
        memory.propose("agent", task["agent_id"], {**proposal(run), **delta})


def test_revisions_and_bindings_cannot_be_replaced(tmp_path):
    store, _, _, task, run, memory = setup(tmp_path)
    candidate = memory.propose("agent", task["agent_id"], proposal(run))
    memory.decide(candidate["id"], decision())
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM execution_memory_snapshots WHERE execution_id=?", (run["id"],)).fetchone():
            freeze(db, run, task["conversation_id"])
        for table in ("memory_revisions", "execution_memory_snapshots"):
            row = db.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
            for sql, values in ((f"DELETE FROM {table}", ()),
                    (f"UPDATE {table} SET {row.keys()[0]}={row.keys()[0]}", ()),
                    (f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' for _ in row)})", tuple(row))):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(sql, values)


def test_missing_binding_fails_and_dm_excludes_project(tmp_path):
    store, tasks, executions, task, run, memory = setup(tmp_path)
    dm = store.save_conversation(dict(type="dm", title="Private", member_ids=[task["agent_id"]]))
    source = store.send_message(dm["id"], dict(content="private task", request_id="dm-source"))
    private = tasks.create(dm["id"], dict(request_id="private", source_message_id=source["id"],
        agent_id=task["agent_id"], title="private", scope="private", acceptance="verified"))
    private_run = executions.create(private["id"], request())
    shared = memory.propose("project", task["conversation_id"], proposal(run))
    memory.decide(shared["id"], decision())
    with store.connect() as db:
        with pytest.raises(ValueError, match="绑定"):
            snapshot(db, private_run, dm["id"])
        freeze(db, private_run, dm["id"])
        assert snapshot(db, private_run, dm["id"]) == []
        assert db.execute("SELECT count(*) FROM execution_memory_snapshots WHERE execution_id=?",
                          (private_run["id"],)).fetchone()[0] == 1


def test_concurrent_scope_approvals_have_one_version_winner(tmp_path):
    _, _, _, task, run, memory = setup(tmp_path)
    candidates = [memory.propose("agent", task["agent_id"], proposal(run, str(i))) for i in range(4)]
    def approve(candidate):
        try:
            return memory.decide(candidate["id"], decision(candidate["id"]))
        except TaskVersionConflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(r is not None for r in pool.map(approve, candidates)) == 1
    assert memory.get("agent", task["agent_id"])["version"] == 1
