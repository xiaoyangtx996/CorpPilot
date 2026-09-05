"""Artifact bytes are snapshots, not a route to arbitrary workspace files."""
import hashlib
import http.client

import pytest

from test_workbench_api import running, request, TOKENS
from test_workbench_executions import setup, request as execution_request, revise
from workbench import artifacts, cli_controller
from test_workbench_cli_controller import fixture, until, result


def test_artifacts_commit_with_result_and_download_as_attachment(tmp_path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task["id"], execution_request())
    executions.claim(run["id"])
    content = b'<html><script>alert("not inline")</script></html>'
    saved = executions.report(run["id"], 1, 1, 0, "Delivered", success=True,
                              artifacts=[{"path": "report.html", "data": content}])
    assert saved["state"] == "awaiting_review"
    metadata = artifacts.list_for(store, run["id"])
    assert len(metadata) == 1 and "data" not in metadata[0] and "content" not in metadata[0]
    assert metadata[0]["sha256"] == hashlib.sha256(content).hexdigest()
    # A lost response retry must not create another snapshot or overwrite bytes.
    executions.report(run["id"], 1, 1, 0, "Changed", success=True,
                      artifacts=[{"path": "report.html", "data": b"different"}])
    assert artifacts.list_for(store, run["id"]) == metadata
    with running(tmp_path) as port:
        assert request(port, "GET", f'/api/workbench/executions/{run["id"]}/artifacts') == (200, metadata)
        path = f'/api/workbench/artifacts/{metadata[0]["id"]}/download'
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + TOKENS[port]})
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Type") == "application/octet-stream"
            assert response.getheader("Content-Disposition").startswith("attachment;")
            assert response.getheader("X-Content-Type-Options") == "nosniff"
            assert response.read() == content
        finally:
            connection.close()
        assert request(port, "GET", path, headers={"Origin": "https://untrusted.example"})[0] == 400
        assert request(port, "POST", path, {})[0] == 404
        assert request(port, "GET", "/api/workbench/artifacts/missing/download")[0] == 404


def test_artifact_failure_rolls_back_result_and_stale_run_cannot_publish(tmp_path):
    store, tasks, executions, task = setup(tmp_path)
    run = executions.create(task["id"], execution_request())
    executions.claim(run["id"])
    with pytest.raises(ValueError):
        executions.report(run["id"], 1, 1, 0, "Delivered", success=True,
                          artifacts=[{"path": "../outside", "data": b"unsafe"}])
    assert executions.get(run["id"])["state"] == "running"
    assert artifacts.list_for(store, run["id"]) == []
    revise(tasks, task)
    saved = executions.report(run["id"], 1, 1, 0, "Stale", success=True,
                              artifacts=[{"path": "old.txt", "data": b"old version"}])
    assert saved["state"] == "superseded"
    assert artifacts.list_for(store, run["id"]) == []


def test_controller_collects_fixed_directory_and_preserves_snapshot(tmp_path, monkeypatch):
    store, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    work = tmp_path / "execution-workspaces" / runs[0]["id"] / "work" / "artifacts"
    work.mkdir(parents=True)
    (work / "answer.txt").write_bytes(b"stable artifact")
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: {**result(), "workspace": "C:/untrusted-returned-path"})
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "awaiting_review")
        items = artifacts.list_for(store, runs[0]["id"])
        assert len(items) == 1
        (work / "answer.txt").write_bytes(b"changed after capture")
        assert artifacts.get(store, items[0]["id"])["data"] == b"stable artifact"
    finally:
        controller.close()


def test_capture_failure_never_turns_exit_zero_into_review(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_controller, "run_codex", lambda **kw: result())
    def fail_capture(*args):
        raise ValueError("untrusted private filename or secret")
    monkeypatch.setattr(cli_controller, "capture", fail_capture)
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "failed")
        run = controller.executions.get(runs[0]["id"])
        assert run["exit_code"] == 0 and "private" not in run["summary"]
    finally:
        controller.close()
