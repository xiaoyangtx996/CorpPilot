"""Independent shutdown/admission races; no worker or provider is started."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_project_launch import fixture, counts
from workbench.cli_controller import CLIController


def test_launch_started_before_close_commits_before_closed_boundary(tmp_path, monkeypatch):
    store, _, source, _, payload = fixture(tmp_path)
    controller = CLIController(store)
    monkeypatch.setattr(controller.settings, 'resolve', lambda: {})
    entered, release, close_attempt = (threading.Event() for _ in range(3))
    role = threading.local()
    original_lock = controller.launch_lock

    class ObservedLock:
        def __enter__(self):
            if getattr(role, 'closing', False):
                close_attempt.set()
            original_lock.acquire()

        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(controller, 'launch_lock', ObservedLock())
    create = controller.project_launches.create

    def gated_create(*args):
        entered.set()
        assert release.wait(5), 'Launch gate was not released'
        return create(*args)

    def close():
        role.closing = True
        controller.close()

    monkeypatch.setattr(controller.project_launches, 'create', gated_create)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            launching = pool.submit(controller.launch_project, source, payload)
            try:
                assert entered.wait(5)
                closing = pool.submit(close)
                assert close_attempt.wait(5), 'Close must acquire the admission lock'
                assert not controller.closed and not closing.done()
                assert counts(store)['project_launches'] == 0
            finally:
                release.set()
            receipt = launching.result(timeout=5)
            closing.result(timeout=5)
        assert controller.closed
        assert controller.launch_project(source, payload) == receipt
        assert counts(store)['project_launches'] == 1
        assert {item['execution']['state'] for item in
                controller.project_executions.get(receipt['batch']['id'])['items']} == {'queued'}
    finally:
        release.set()
        if not controller.closed:
            controller.close()


def test_close_in_progress_rejects_new_launch_but_replays_exact_receipt(tmp_path, monkeypatch):
    store, _, source, _, payload = fixture(tmp_path)
    controller = CLIController(store)
    monkeypatch.setattr(controller.settings, 'resolve', lambda: {})
    receipt = controller.launch_project(source, payload)
    baseline = counts(store)
    shutdown_entered, release = threading.Event(), threading.Event()
    shutdown = controller.pool.shutdown

    def gated_shutdown(*args, **kwargs):
        shutdown_entered.set()
        assert release.wait(5), 'Shutdown gate was not released'
        return shutdown(*args, **kwargs)

    monkeypatch.setattr(controller.pool, 'shutdown', gated_shutdown)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(controller.close)
            try:
                assert shutdown_entered.wait(5) and controller.closed
                assert not closing.done()
                assert controller.launch_project(source, payload) == receipt
                changed = {**payload, 'plan': {**payload['plan'], 'request_id': 'new-during-close'}}
                with pytest.raises(ValueError, match='关闭'):
                    controller.launch_project(source, changed)
                assert counts(store) == baseline
            finally:
                release.set()
            closing.result(timeout=5)
    finally:
        release.set()
        if not controller.closed:
            controller.close()
