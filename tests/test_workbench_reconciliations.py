"""Owner reconciliation unblocks uncertainty without fabricating machine results."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_workbench_executions import setup, request, revise, report
from workbench.reconciliations import Reconciliations, unresolved
from workbench.store import Store
from workbench.tasks import TaskVersionConflict


def unknown(tmp_path):
    store, tasks, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    assert executions.claim(run["id"])
    run = report(executions, run, None)
    assert run["state"] == "unknown"
    payload = {"request_id": "checked", "attempt": 1, "requirement_version": 1,
        "process_stopped": True, "external_effects_checked": True, "note": "Inspected process tree and external effects"}
    return store, tasks, executions, task, run, Reconciliations(store), payload


def test_replay_restart_immutable_and_no_execution_mutation(tmp_path):
    store, tasks, executions, task, run, ledger, payload = unknown(tmp_path)
    assert ledger.get(run["id"]) is None
    saved = ledger.save(run["id"], payload)
    assert saved == {**payload, "execution_id": run["id"], "reconciled_at": saved["reconciled_at"]}
    assert executions.get(run["id"]) == run and tasks.get(task["id"]) == task
    changed = revise(tasks, task)
    assert changed["requirement_version"] == 2
    store.save_agent({"enabled": False}, task["agent_id"])
    assert ledger.save(run["id"], payload) == saved
    assert Reconciliations(Store(tmp_path)).get(run["id"]) == saved
    with store.connect() as db:
        row = db.execute("SELECT * FROM execution_reconciliations").fetchone()
        assert not unresolved(db)
        assert not unresolved(db, task["id"])
        assert db.execute("SELECT COUNT(*) FROM execution_artifacts").fetchone()[0] == 0
        for statement, parameters in (("UPDATE execution_reconciliations SET note='changed'", ()),
                ("DELETE FROM execution_reconciliations", ()),
                ("INSERT OR REPLACE INTO execution_reconciliations VALUES (?,?,?,?,?,?,?,?)", tuple(row))):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement, parameters)
    for delta in ({"request_id": "different"}, {"note": "different"}, {"attempt": 2}, {"requirement_version": 2}):
        with pytest.raises(ValueError):
            ledger.save(run["id"], {**payload, **delta})


def test_historical_unknown_can_be_checked_after_authority_changes(tmp_path):
    store, tasks, executions, task, run, ledger, payload = unknown(tmp_path)
    revise(tasks, task)
    store.save_agent({"enabled": False, "tools": []}, task["agent_id"])
    store.save_conversation({"archived": True}, task["conversation_id"])
    assert ledger.save(run["id"], payload)["requirement_version"] == 1
    assert executions.get(run["id"]) == run


def test_replay_stays_original_after_new_attempt(tmp_path):
    _, _, executions, task, run, ledger, payload = unknown(tmp_path)
    saved = ledger.save(run["id"], payload)
    replacement = executions.create(task["id"], request(request_id="replacement",
        previous=run["id"], note="Owner explicitly requests another attempt"))
    assert replacement["attempt"] == 2
    assert ledger.save(run["id"], payload) == saved
    assert executions.get(run["id"]) == run


def test_payload_rejects_false_acknowledgments_and_wrong_execution_identity(tmp_path):
    _, _, _, _, run, ledger, payload = unknown(tmp_path)
    invalids = [None, [], {}, {**payload, "state": "completed"}]
    for field, values in {"request_id": [None, "", "x" * 121], "note": [None, " ", "x" * 2001],
            "attempt": [True, 0, -1, "1", 1.0], "requirement_version": [True, 0, "1", 1.0],
            "process_stopped": [False, 1, "true", None], "external_effects_checked": [False, 1, "true", None]}.items():
        invalids.extend({**payload, field: value} for value in values)
    for invalid in invalids:
        with pytest.raises(ValueError):
            ledger.save(run["id"], invalid)
    for field in ("attempt", "requirement_version"):
        with pytest.raises(TaskVersionConflict):
            ledger.save(run["id"], {**payload, field: 2})
    assert ledger.get(run["id"]) is None
    with pytest.raises(KeyError):
        ledger.get("missing")
    with pytest.raises(KeyError):
        ledger.save("missing", payload)


@pytest.mark.parametrize("state", ["queued", "running", "stopping", "awaiting_review", "failed", "cancelled", "superseded"])
def test_only_unknown_accepts_new_reconciliation(tmp_path, state):
    store, _, _, _, run, ledger, payload = unknown(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE task_executions SET state=? WHERE id=?", (state, run["id"]))
    with pytest.raises(ValueError):
        ledger.save(run["id"], payload)
    assert ledger.get(run["id"]) is None


def test_concurrent_decisions_and_exact_replays(tmp_path):
    _, _, _, _, run, ledger, payload = unknown(tmp_path)
    def save(number):
        try:
            return ledger.save(run["id"], {**payload, "request_id": str(number)})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [item for item in pool.map(save, range(8)) if item is not None]
    assert len(winners) == 1
    winning_payload = {key: winners[0][key] for key in payload}
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: ledger.save(run["id"], winning_payload), range(8))) == winners * 8


def test_unresolved_is_scoped_and_pending_is_bounded_and_stable(tmp_path):
    store, tasks, executions, task, first, ledger, payload = unknown(tmp_path)
    second_task = tasks.create(task["conversation_id"], {key: value for key, value in {
        **task, "request_id": "second-task"}.items() if key in
        ("agent_id", "source_message_id", "request_id", "title", "scope", "acceptance")})
    second = executions.create(second_task["id"], request())
    assert executions.claim(second["id"])
    second = report(executions, second, None)
    expected = sorted([first, second], key=lambda item: (item["created_at"], item["id"]))
    assert ledger.pending() == expected
    assert ledger.pending(1) == expected[:1]
    for invalid in (True, 0, 101, "1", 1.0):
        with pytest.raises(ValueError):
            ledger.pending(invalid)
    saved = ledger.save(first["id"], payload)
    with store.connect() as db:
        assert unresolved(db) and unresolved(db, second_task["id"])
        assert not unresolved(db, task["id"])
    assert ledger.pending() == [second]
    assert ledger.save(first["id"], payload) == saved
    ledger.save(second["id"], payload)
    assert ledger.pending() == []
