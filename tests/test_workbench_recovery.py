"""Restored queues remain inert until explicit offline release."""
import json
import time

import pytest

from test_workbench_controller import configure, until
from test_workbench_provider import endpoint
from test_workbench_executions import request as execution_request
from workbench.controller import ReplyController
from workbench.directory_lock import acquire
from workbench.recovery import activate
from workbench.settings import Settings
from workbench.store import Store


def marker(store, original):
    original.mkdir(exist_ok=True)
    path = store.data_dir / 'restore-quarantine.json'
    path.write_text(json.dumps({'version': 1, 'backup': str(original / 'backup'), 'source_data_dir': str(original)}))
    (store.data_dir / 'restore-complete.json').write_text(json.dumps({'version': 1, 'backup': str(original / 'backup')}))
    return path


def test_restored_queue_waits_until_offline_release(tmp_path, monkeypatch):
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'after explicit release'}}]}
    with endpoint(output) as (config, requests):
        store = Store(tmp_path / 'restored')
        gate = marker(store, tmp_path / 'original')
        settings = Settings(store); configure(settings, config, monkeypatch)
        ctl = ReplyController(store, settings)
        try:
            actor = store.agents()[0]['id']
            room = store.save_conversation({'type': 'dm', 'title': 'restored', 'member_ids': [actor]})
            message = store.send_message(room['id'], {'content': 'one original queued request', 'request_id': 'message'})
            run = ctl.runs.create(room['id'], {'agent_id': actor, 'source_message_id': message['id'], 'request_id': 'original'})
            store.save_agent({'tools': ['read', 'write', 'execute']}, actor)
            task = ctl.cli.executions.tasks.create(room['id'], {'agent_id': actor, 'source_message_id': message['id'],
                'request_id': 'task', 'title': 'restored task', 'scope': 'write a report', 'acceptance': 'report saved'})
            execution = ctl.cli.executions.create(task['id'], execution_request())
            dispatches = []
            monkeypatch.setattr(ctl.goals, 'tick', lambda: dispatches.append('goal'))
            monkeypatch.setattr(ctl.cli, 'tick', lambda: dispatches.append('cli'))
            until(lambda: '隔离' in ctl.status()['error'])
            time.sleep(.3)
            assert ctl.runs.get(run['id'])['state'] == 'queued'
            assert requests == [] and ctl.cli.active == {}
            assert dispatches == []
            assert ctl.cli.executions.get(execution['id'])['state'] == 'queued'
            with pytest.raises(ValueError, match='已有运行'):
                activate(store.data_dir, original_stopped=True, effects_checked=True)
            assert gate.exists()
        finally:
            ctl.close()
        with acquire(tmp_path / 'original'):
            with pytest.raises(ValueError, match='已有运行'):
                activate(store.data_dir, original_stopped=True, effects_checked=True)
        assert gate.exists()
        result = activate(store.data_dir, original_stopped=True, effects_checked=True)
        assert result['released'] and not gate.exists()
        ctl = ReplyController(store, settings)
        try:
            until(lambda: ctl.runs.get(run['id'])['state'] == 'completed')
            assert len(requests) == 1
        finally:
            ctl.close()


@pytest.mark.parametrize('content', ['{}', '{invalid', '{"version":true}'])
def test_invalid_gate_still_blocks_and_cannot_be_released(tmp_path, content):
    store = Store(tmp_path / 'restored')
    gate = marker(store, tmp_path / 'original'); gate.write_text(content)
    ctl = ReplyController(store, Settings(store))
    try:
        until(lambda: '隔离' in ctl.status()['error'])
    finally:
        ctl.close()
    with pytest.raises(ValueError):
        activate(store.data_dir, original_stopped=True, effects_checked=True)
    assert gate.read_text() == content


def test_release_requires_both_confirmations_and_no_unchecked_unknown(tmp_path):
    store = Store(tmp_path / 'restored'); gate = marker(store, tmp_path / 'original')
    ctl = ReplyController(store, Settings(store))
    actor = store.agents()[0]['id']
    room = store.save_conversation({'type': 'dm', 'title': 'unknown', 'member_ids': [actor]})
    message = store.send_message(room['id'], {'content': 'review external effects', 'request_id': 'message'})
    run = ctl.runs.create(room['id'], {'agent_id': actor, 'source_message_id': message['id'], 'request_id': 'original'})
    ctl.close()
    for options in ({}, {'original_stopped': True}, {'effects_checked': True}):
        with pytest.raises(ValueError): activate(store.data_dir, **options)
    with store.connect() as db:
        db.execute("UPDATE runs SET state='unknown' WHERE id=?", (run['id'],))
    with pytest.raises(ValueError, match='未知'):
        activate(store.data_dir, original_stopped=True, effects_checked=True)
    assert gate.exists()


def test_partial_restore_cannot_be_released(tmp_path):
    store = Store(tmp_path / 'restored'); gate = marker(store, tmp_path / 'original')
    (store.data_dir / 'restore-complete.json').unlink()
    with pytest.raises(ValueError, match='完整完成'):
        activate(store.data_dir, original_stopped=True, effects_checked=True)
    assert gate.exists()
