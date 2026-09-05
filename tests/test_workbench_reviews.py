"""Owner decisions bind durable artifacts to one current execution, without running tools."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_workbench_executions import setup, request, revise
from test_workbench_api import running, request as http
from workbench import artifacts
from workbench.reviews import Reviews
from workbench.store import Store


def ready(tmp_path, items=None):
    store, tasks, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    assert executions.claim(run["id"])
    result = executions.report(run["id"], 1, 1, 0, "Actual result recorded", success=True,
        artifacts=items if items is not None else [{"path": "result.txt", "data": b"verified output"}])
    payload = {"request_id": "review", "expected_version": 1, "decision": "approved",
               "note": "Checked against acceptance", "artifact_ids": sorted(x["id"] for x in artifacts.list_for(store, run["id"]))}
    return store, tasks, executions, task, result, Reviews(store), payload


def test_persist_replay_and_no_execution_or_requirement_mutation(tmp_path):
    store, tasks, executions, task, run, reviews, payload = ready(tmp_path)
    assert reviews.get(run["id"]) is None
    saved = reviews.save(run["id"], payload)
    assert saved["decision"] == "approved" and saved["artifact_ids"] == payload["artifact_ids"]
    assert executions.get(run["id"]) == run and tasks.get(task["id"]) == task
    changed = revise(tasks, task)
    store.save_agent({"enabled": False}, task["agent_id"])
    assert reviews.save(run["id"], {**payload, "note": "  Checked against acceptance  "}) == saved
    assert Reviews(Store(tmp_path)).get(run["id"]) == saved
    assert tasks.get(task["id"]) == changed
    with store.connect() as db:
        row = db.execute("SELECT * FROM execution_reviews").fetchone()
        for statement, parameters in (("UPDATE execution_reviews SET note='overwritten'", ()),
                ("DELETE FROM execution_reviews", ()),
                ("INSERT OR REPLACE INTO execution_reviews VALUES (?,?,?,?,?,?,?)", tuple(row))):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement, parameters)
    for delta in ({"request_id": "different"}, {"decision": "rejected"}, {"note": "different"}, {"artifact_ids": []}):
        with pytest.raises(ValueError):
            reviews.save(run["id"], {**payload, **delta})


def test_competing_decisions_have_one_winner(tmp_path):
    _, _, _, _, run, reviews, payload = ready(tmp_path)
    def decide(number):
        try:
            return reviews.save(run["id"], {**payload, "request_id": str(number),
                "decision": "approved" if number % 2 else "rejected"})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(decide, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and reviews.get(run["id"]) == winners[0]


@pytest.mark.parametrize("change", ["version", "newer", "removed", "disabled", "tools", "archived"])
def test_new_review_rechecks_current_authority(tmp_path, change):
    store, tasks, executions, task, run, reviews, payload = ready(tmp_path)
    if change == "version":
        revise(tasks, task)
    elif change == "newer":
        executions.create(task["id"], request(request_id="second", note="Reviewed previous effects", previous=run["id"]))
    elif change == "removed":
        store.set_member(task["conversation_id"], task["agent_id"], False)
    elif change == "disabled":
        store.save_agent({"enabled": False}, task["agent_id"])
    elif change == "tools":
        store.save_agent({"tools": ["read"]}, task["agent_id"])
    else:
        store.save_conversation({"archived": True}, task["conversation_id"])
    with pytest.raises((ValueError, PermissionError)):
        reviews.save(run["id"], payload)
    assert reviews.get(run["id"]) is None


def test_empty_output_can_be_rejected_but_never_approved(tmp_path):
    _, _, executions, _, run, reviews, payload = ready(tmp_path, [])
    with pytest.raises(ValueError):
        reviews.save(run["id"], payload)
    assert reviews.save(run["id"], {**payload, "decision": "rejected"})["decision"] == "rejected"
    assert executions.get(run["id"]) == run


@pytest.mark.parametrize("corruption", ["content", "size", "sha256"])
def test_approval_checks_snapshot_integrity(tmp_path, corruption):
    store, _, _, _, run, reviews, payload = ready(tmp_path)
    with store.connect() as db:
        db.execute("DROP TRIGGER artifacts_no_update")
        value = {"content": b"corrupt", "size": 1, "sha256": "0" * 64}[corruption]
        db.execute(f"UPDATE execution_artifacts SET {corruption}=?", (value,))
    with pytest.raises(ValueError):
        reviews.save(run["id"], payload)
    assert reviews.get(run["id"]) is None
    assert reviews.save(run["id"], {**payload, "decision": "rejected"})["decision"] == "rejected"


def test_full_artifact_set_order_normalized_and_partial_rejected(tmp_path):
    _, _, _, _, run, reviews, payload = ready(tmp_path,
        [{"path": "first", "data": b"a"}, {"path": "second", "data": b"b"}])
    for decision in ("approved", "rejected"):
        with pytest.raises(ValueError):
            reviews.save(run["id"], {**payload, "decision": decision, "artifact_ids": payload["artifact_ids"][:1]})
    saved = reviews.save(run["id"], payload)
    assert reviews.save(run["id"], {**payload, "artifact_ids": list(reversed(payload["artifact_ids"]))}) == saved


def test_payload_and_exact_artifact_set(tmp_path):
    _, _, _, _, run, reviews, payload = ready(tmp_path)
    invalids = [None, [], {}, {**payload, "actor": "agent"}]
    for field, values in {"request_id": ["", None, "x" * 121], "expected_version": [True, 0, "1", 2],
                          "decision": ["complete", None], "note": [" ", None, "x" * 2001],
                          "artifact_ids": [None, "x", [], ["foreign"], payload["artifact_ids"] * 2]}.items():
        invalids.extend({**payload, field: value} for value in values)
    for invalid in invalids:
        with pytest.raises(ValueError):
            reviews.save(run["id"], invalid)
    assert reviews.get(run["id"]) is None


@pytest.mark.parametrize("state", ["queued", "running", "stopping", "cancelled", "failed", "unknown", "superseded"])
def test_non_reviewable_execution_refuses_decision(tmp_path, state):
    store, _, _, _, run, reviews, payload = ready(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE task_executions SET state=? WHERE id=?", (state, run["id"]))
    with pytest.raises(ValueError):
        reviews.save(run["id"], payload)
    assert reviews.get(run["id"]) is None


def test_review_http_persistence_and_no_execution_calls(tmp_path, monkeypatch):
    _, _, _, _, run, _, payload = ready(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Owner review must not launch a model or CLI")
    monkeypatch.setattr("workbench.cli_controller.run_codex", forbidden)
    monkeypatch.setattr("workbench.controller.run_reply", forbidden)
    path = f"/api/workbench/executions/{run['id']}/review"
    with running(tmp_path) as port:
        assert http(port, "GET", path) == (200, None)
        assert http(port, "POST", path, {**payload, "decision": "invalid"})[0] == 400
        status, saved = http(port, "POST", path, payload)
        assert status == 201 and saved["decision"] == "approved"
        assert http(port, "POST", path, payload) == (201, saved)
        assert http(port, "PATCH", path, payload)[0] == 404
        assert http(port, "GET", "/api/workbench/executions/missing/review")[0] == 404
    with running(tmp_path) as port:
        assert http(port, "GET", path) == (200, saved)
