"""Controller fault gates plus a real local process tree; no model provider calls."""
from concurrent.futures import Future
import os
import sys
from threading import Event

import pytest

from test_workbench_cli_controller import fixture, until, result
from workbench import cli_controller
from workbench.cli import parse_result
from workbench.process_tree import run_process


def test_submit_failure_is_unknown_and_cancels_possible_queued_work(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    signals = []
    def broken_submit(fn, run, config, cancel):
        signals.append(cancel)
        raise RuntimeError("thread start failure after queue insertion")
    monkeypatch.setattr(controller.pool, "submit", broken_submit)
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "unknown")
        assert signals[0].is_set()
        assert controller.executions.get(runs[0]["id"])["exit_code"] is None
    finally:
        controller.close()


def test_report_write_failure_does_not_block_other_running_cancellation(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch, 2)
    active = Event()
    stopped = Event()
    def runner(**kw):
        if kw["execution_id"] == runs[0]["id"]:
            return result()
        active.set()
        assert kw["cancel"].wait(4)
        stopped.set()
        return {**result(-15, False, "cancelled"), "workspace": "test-work"}
    monkeypatch.setattr(cli_controller, "run_codex", runner)
    original = controller.executions.report
    def blocked(identity, *args, **kw):
        if identity == runs[0]["id"]:
            raise OSError("database unavailable for first report")
        return original(identity, *args, **kw)
    monkeypatch.setattr(controller.executions, "report", blocked)
    try:
        until(controller, active.is_set)
        controller.executions.cancel(runs[1]["id"])
        until(controller, stopped.is_set)
        until(controller, lambda: controller.executions.get(runs[1]["id"])["state"] == "cancelled")
        assert controller.executions.get(runs[0]["id"])["state"] == "running"
        monkeypatch.setattr(controller.executions, "report", original)
        until(controller, lambda: not controller.active)
        assert controller.executions.get(runs[0]["id"])["state"] == "awaiting_review"
    finally:
        controller.close()


def test_exceptional_future_becomes_unknown(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    def broken_submit(*args):
        future = Future()
        future.set_exception(RuntimeError("private exception"))
        return future
    monkeypatch.setattr(controller.pool, "submit", broken_submit)
    try:
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "unknown")
        assert "private" not in controller.executions.get(runs[0]["id"])["summary"]
    finally:
        controller.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job process backend")
def test_cancel_reaches_real_process_tree_and_records_observed_exit(tmp_path, monkeypatch):
    _, _, controller, _, runs = fixture(tmp_path, monkeypatch)
    witness = tmp_path / "started"
    def runner(**kw):
        code = "from pathlib import Path; import time; Path('started').write_text('yes'); time.sleep(30)"
        process = run_process([sys.executable, "-c", code], tmp_path, dict(os.environ), b"", 5, kw["cancel"])
        return {**parse_result(process, "fake-local-test-key"), "workspace": str(tmp_path)}
    monkeypatch.setattr(cli_controller, "run_codex", runner)
    try:
        until(controller, witness.exists)
        assert controller.executions.cancel(runs[0]["id"])["state"] == "stopping"
        until(controller, lambda: controller.executions.get(runs[0]["id"])["state"] == "cancelled")
        assert type(controller.executions.get(runs[0]["id"])["exit_code"]) is int
    finally:
        controller.close()
