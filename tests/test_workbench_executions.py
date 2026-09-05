"""Execution identity, authority and confirmed completion never mutate requirements."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.executions import Executions
from workbench.store import Store
from workbench.tasks import Tasks


def setup(tmp_path):
    store = Store(tmp_path)
    agents = store.agents()[:2]
    agent = agents[0]
    store.save_agent({"tools": ["read", "write", "execute"]}, agent["id"])
    conversation = store.save_conversation({"type": "board", "title": "Execution tests", "member_ids": [a["id"] for a in agents]})
    source = store.send_message(conversation["id"], {"content": "Owner scope", "request_id": "source"})
    tasks = Tasks(store)
    task = tasks.create(conversation["id"], {"agent_id": agent["id"], "source_message_id": source["id"],
        "request_id": "task", "title": "Implement", "scope": "Bounded scope", "acceptance": "Verified"})
    return store, tasks, Executions(store), task


def request(version=1, request_id="run", note="", previous=None):
    return {"expected_version": version, "request_id": request_id, "reconciliation_note": note, "previous_execution_id": previous}


def revise(tasks, task):
    return tasks.revise(task["id"], {**{key: task[key] for key in ("title", "scope", "acceptance", "agent_id")},
        "expected_version": task["requirement_version"], "scope": "Revised scope"})


def report(executions, run, code=0):
    return executions.report(run["id"], run["attempt"], run["requirement_version"], code, "Process result", success=code == 0)


def test_concurrent_create_claim_and_reopen(tmp_path):
    _, tasks, executions, task = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: executions.create(task["id"], request()), range(8)))
    run = runs[0]
    assert len({r["id"] for r in runs}) == 1
    assert (run["state"], run["attempt"], run["requirement_version"]) == ("queued", 1, 1)
    assert executions.pending() == [run]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: executions.claim(run["id"]), range(8)))
    assert sum(claims) == 1
    assert executions.pending() == []
    with pytest.raises(ValueError):
        executions.create(task["id"], request(request_id="other", note="Reviewed"))
    snapshot = executions.snapshot(run["id"])
    assert snapshot["task"] == task
    assert snapshot["agent"]["id"] == task["agent_id"]
    assert snapshot["source_message"]["id"] == task["source_message_id"]
    assert snapshot["instructions"]
    result = report(executions, run)
    assert result["state"] == "awaiting_review"
    restored = Executions(Store(tmp_path))
    assert restored.get(run["id"]) == result
    assert restored.list(task["id"]) == [result]
    assert restored.create(task["id"], request()) == result
    assert tasks.get(task["id"]) == task


@pytest.mark.parametrize("claimed", [False, True])
def test_requirement_change_supersedes_without_advancing_task(tmp_path, claimed):
    _, tasks, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    if claimed:
        assert executions.claim(run["id"])
    updated = revise(tasks, task)
    if claimed:
        with pytest.raises((ValueError, PermissionError)):
            executions.snapshot(run["id"])
        assert report(executions, run)["state"] == "superseded"
    else:
        assert not executions.claim(run["id"])
        assert executions.get(run["id"])["state"] == "superseded"
    assert tasks.get(task["id"]) == updated
    next_run = executions.create(task["id"], request(2, "second", "Old version stopped and checked", run["id"]))
    assert next_run["attempt"] == 2 and next_run["requirement_version"] == 2


@pytest.mark.parametrize("change", ["remove", "disable", "archive"])
@pytest.mark.parametrize("claimed", [False, True])
def test_dispatch_rechecks_current_authority(tmp_path, change, claimed):
    store, tasks, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    if claimed:
        assert executions.claim(run["id"])
    if change == "remove":
        store.set_member(task["conversation_id"], task["agent_id"], False)
    elif change == "disable":
        store.save_agent({"enabled": False}, task["agent_id"])
    else:
        store.save_conversation({"archived": True}, task["conversation_id"])
    if claimed:
        with pytest.raises((PermissionError, ValueError)):
            executions.snapshot(run["id"])
        assert report(executions, run, 0)["state"] == "failed"
        assert tasks.get(task["id"]) == task
    else:
        assert not executions.claim(run["id"])
        assert executions.get(run["id"])["state"] == "failed"


def test_cancellation_requires_confirmed_exit_and_unknown_requires_reconciliation(tmp_path):
    _, tasks, executions, task = setup(tmp_path)
    queued = executions.create(task["id"], request())
    assert executions.cancel(queued["id"])["state"] == "cancelled"
    assert not executions.claim(queued["id"])
    run = executions.create(task["id"], request(request_id="second", note="Queued attempt never started", previous=queued["id"]))
    assert executions.claim(run["id"])
    assert executions.cancel(run["id"])["state"] == "stopping"
    with pytest.raises(ValueError):
        executions.create(task["id"], request(request_id="third", note="Cancellation requested"))
    assert report(executions, run, None)["state"] == "unknown"
    with pytest.raises(ValueError):
        executions.create(task["id"], request(request_id="third"))
    with pytest.raises(ValueError, match="未核实"):
        executions.create(task["id"], request(request_id="third", note="Owner checked external effects and authorizes retry", previous=run["id"]))
    assert tasks.get(task["id"]) == task


@pytest.mark.parametrize("code,state", [(0, "awaiting_review"), (3, "failed"), (None, "unknown")])
def test_callback_identity_terminal_immutability_and_task_isolation(tmp_path, code, state):
    store, tasks, executions, task = setup(tmp_path)
    other = tasks.create(task["conversation_id"], {**{key: task[key] for key in
        ("agent_id", "source_message_id", "title", "scope", "acceptance")}, "request_id": "other"})
    run = executions.create(task["id"], request())
    other_run = executions.create(other["id"], request())
    assert run["agent_id"] == other_run["agent_id"] and run["id"] != other_run["id"]
    assert executions.claim(run["id"]) and executions.claim(other_run["id"])
    before = executions.get(run["id"])
    executions.report(run["id"], run["attempt"] + 1, 1, 0, "Wrong attempt")
    executions.report(run["id"], run["attempt"], 2, 0, "Wrong version")
    assert executions.get(run["id"]) == before
    result = report(executions, run, code)
    assert result["state"] == state
    assert report(executions, run, 19) == result
    assert executions.get(other_run["id"])["state"] == "running"
    assert tasks.get(task["id"]) == task and tasks.get(other["id"]) == other
    assert Executions(Store(tmp_path)).get(run["id"]) == result


def test_request_validation_and_idempotency_conflict(tmp_path):
    _, _, executions, task = setup(tmp_path)
    for invalid in (None, [], {}, {**request(), "state": "completed"}, request(version=True),
                    request(version=0), request(request_id=" "), request(note=None)):
        with pytest.raises(ValueError):
            executions.create(task["id"], invalid)
    run = executions.create(task["id"], request())
    with pytest.raises(ValueError):
        executions.create(task["id"], request(note="Changed payload"))
    assert executions.get(run["id"]) == run


def test_distinct_concurrent_requests_and_stale_retry_have_one_winner(tmp_path):
    _, _, executions, task = setup(tmp_path)

    def create(number, previous=None):
        try:
            return executions.create(task["id"], request(request_id=f"run-{number}",
                note="Checked previous attempt", previous=previous))
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(8)))
    winners = [run for run in results if run]
    assert len(winners) == 1
    previous = executions.cancel(winners[0]["id"])
    next_run = create(20, previous["id"])
    assert next_run is not None
    executions.cancel(next_run["id"])
    assert create(21, previous["id"]) is None
    assert len(executions.list(task["id"])) == 2


def test_global_active_capacity_released_only_when_no_longer_active(tmp_path):
    _, tasks, executions, task = setup(tmp_path)
    payload = {key: task[key] for key in ("agent_id", "source_message_id", "title", "scope", "acceptance")}
    runs = [executions.create(task["id"], request())]
    for number in range(100):
        other = tasks.create(task["conversation_id"], {**payload, "request_id": f"task-{number}"})
        if number < 99:
            runs.append(executions.create(other["id"], request()))
    assert len(executions.pending()) == 100
    assert executions.claim(runs[0]["id"])
    executions.cancel(runs[0]["id"])
    with pytest.raises(ValueError):
        executions.create(other["id"], request())
    assert report(executions, runs[0], -15)["state"] == "cancelled"
    assert executions.create(other["id"], request())["state"] == "queued"


def test_recovery_preserves_queue_and_marks_unverified_processes_unknown(tmp_path):
    _, tasks, executions, task = setup(tmp_path)
    payload = {key: task[key] for key in ("agent_id", "source_message_id", "title", "scope", "acceptance")}
    runs = [executions.create(task["id"], request())]
    for number in range(2):
        other = tasks.create(task["conversation_id"], {**payload, "request_id": f"recovery-{number}"})
        runs.append(executions.create(other["id"], request()))
    assert executions.claim(runs[0]["id"])
    assert executions.claim(runs[1]["id"])
    executions.cancel(runs[1]["id"])
    restored = Executions(Store(tmp_path))
    assert restored.recover() == 2
    assert [restored.get(run["id"])["state"] for run in runs] == ["unknown", "unknown", "queued"]
    assert restored.pending() == [runs[2]]
    assert restored.recover() == 0
    assert report(restored, runs[0], 0)["state"] == "unknown"
    assert tasks.get(task["id"]) == task
