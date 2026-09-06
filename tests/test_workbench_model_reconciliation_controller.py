"""Real thread-pool ownership gates with controlled providers; no network or model calls."""
from threading import Event

import pytest

from test_workbench_controller import configure, until
from workbench import controller as controller_module
from workbench.controller import ReplyController
from workbench.provider import ProviderError
from workbench.settings import Settings
from workbench.store import Store


def setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    settings = Settings(store)
    configure(settings, {'model': 'fixture', 'base_url': 'http://127.0.0.1:1/v1',
                         'api_key': 'fixture-only'}, monkeypatch)
    agent = store.agents()[0]['id']
    room = store.save_conversation({'type': 'dm', 'title': 'Ownership test', 'member_ids': [agent]})['id']
    source = store.send_message(room, {'content': 'Owner request', 'request_id': 'source'})
    payload = {'agent_id': agent, 'source_message_id': source['id'], 'request_id': 'run'}
    declaration = {'request_id': 'owner-check', 'attempt': 1, 'requirement_version': 1,
                   'local_request_stopped': True, 'provider_effects_checked': True,
                   'note': 'Owner checked local completion and provider effects; original result remains unknown'}
    return store, settings, room, payload, declaration


def test_submit_queues_real_worker_then_throws_retains_ownership_until_restart(tmp_path, monkeypatch):
    store, settings, room, payload, declaration = setup(tmp_path, monkeypatch)
    entered, release = Event(), Event()
    calls, orphan_futures = [], []

    def controlled_provider(config, snapshot):
        calls.append(snapshot)
        entered.set()
        assert release.wait(10), 'Test did not release controlled provider'
        raise ProviderError('Controlled provider result unknown', unknown=True)

    monkeypatch.setattr(controller_module, 'run_reply', controlled_provider)
    controller = ReplyController(store, settings)
    actual_submit = controller.pool.submit

    def queued_then_throws(*args, **kwargs):
        orphan_futures.append(actual_submit(*args, **kwargs))
        assert entered.wait(5), 'Worker must really start before submit reports failure'
        raise RuntimeError('Submission reports failure after worker started')

    monkeypatch.setattr(controller.pool, 'submit', queued_then_throws)
    try:
        run = controller.runs.create(room, payload)
        identity = run['id']
        until(lambda: controller.runs.get(identity)['state'] == 'unknown')
        with controller.state_lock:
            assert identity not in controller.unsettled
            assert identity not in controller.futures.values()
            assert identity in controller.uncertain_submissions
            assert not orphan_futures[0].done()
            assert controller.status()['active_requests'] == 1
            assert '已退出' not in controller.runs.get(identity)['error']
            with pytest.raises(ValueError):
                controller.reconcile_unknown(identity, declaration)
            assert controller.model_reconciliations.get(identity) is None

        # Even an independently observed worker completion cannot repair the lost
        # Future binding. The controller conservatively retains ownership until close.
        release.set()
        orphan_futures[0].result(timeout=5)
        with controller.state_lock:
            controller._reconcile()
            assert identity in controller.uncertain_submissions
            assert controller.status()['active_requests'] == 1
            with pytest.raises(ValueError):
                controller.reconcile_unknown(identity, declaration)
        unknown = controller.runs.get(identity)
        assert len(calls) == 1
        assert unknown['model'] is None and unknown['usage'] is None
        assert len(store.messages(room)) == 1
    finally:
        release.set()
        controller.close()

    reopened = ReplyController(store, settings)
    try:
        receipt = reopened.reconcile_unknown(identity, declaration)
        assert reopened.reconcile_unknown(identity, declaration) == receipt
        assert reopened.model_reconciliations.get(identity) == receipt
        assert reopened.runs.get(identity) == unknown
        assert len(calls) == 1
        assert len(store.messages(room)) == 1
    finally:
        reopened.close()


def test_done_future_blocks_declaration_until_reconciled_and_exact_replay_survives_close(tmp_path, monkeypatch):
    store, settings, room, payload, declaration = setup(tmp_path, monkeypatch)
    calls = []

    def controlled_provider(config, snapshot):
        calls.append(snapshot)
        raise ProviderError('Controlled provider result unknown', unknown=True)

    monkeypatch.setattr(controller_module, 'run_reply', controlled_provider)
    controller = ReplyController(store, settings)
    actual_reconcile = controller._reconcile
    monkeypatch.setattr(controller, '_reconcile', lambda: None)
    try:
        run = controller.runs.create(room, payload)
        identity = run['id']
        until(lambda: controller.runs.get(identity)['state'] == 'unknown')
        with controller.state_lock:
            future = next(future for future, value in controller.futures.items() if value == identity)
        future.result(timeout=5)
        with controller.state_lock:
            assert future.done() and controller.futures[future] == identity
            assert identity not in controller.unsettled
            assert identity not in controller.uncertain_submissions
            with pytest.raises(ValueError):
                controller.reconcile_unknown(identity, declaration)
            assert controller.model_reconciliations.get(identity) is None
            actual_reconcile()
            assert identity not in controller.futures.values()
            unknown = controller.runs.get(identity)
            admissions = tuple(controller.monitor._admissions)
            receipt = controller.reconcile_unknown(identity, declaration)
            assert controller.runs.get(identity) == unknown
            assert tuple(controller.monitor._admissions) == admissions
        assert len(calls) == 1 and len(store.messages(room)) == 1
    finally:
        monkeypatch.setattr(controller, '_reconcile', actual_reconcile)
        controller.close()

    settings.save({'enabled': False})
    assert controller.reconcile_unknown(identity, declaration) == receipt
    with pytest.raises(ValueError):
        controller.reconcile_unknown(identity, {**declaration, 'note': 'Different declaration'})
    assert controller.runs.get(identity) == unknown
    assert len(calls) == 1
