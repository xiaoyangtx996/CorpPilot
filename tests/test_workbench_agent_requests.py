"""Creation receipts survive retries and later identity changes without duplicate agents."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench.store import Store
from workbench import skill_inputs


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    payload = dict(request_id='create-agent', name='长期成员', template_id=store.templates()[0]['id'],
                   model='default', skills=['coding'], tools=['read'], enabled=True)
    return store, payload


def test_concurrent_same_key_returns_single_original_creation(setup):
    store, payload = setup
    before = len(store.agents())
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: store.save_agent(payload), range(16)))
    assert all(value == values[0] for value in values)
    assert len(store.agents()) == before + 1
    receipt = store.agent_request(payload['request_id'])
    assert receipt == dict(request_id=payload['request_id'], payload={k: v for k, v in payload.items() if k != 'request_id'}, agent=values[0])
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM agent_creation_requests').fetchone()[0] == 1


def test_normalization_replay_uses_same_semantics_as_saved_agent(setup):
    store, payload = setup
    created = store.save_agent(payload | {'request_id': ' create-agent ', 'name': ' 长期成员 ', 'skills': ['coding', 'coding'], 'tools': ['read', 'read']})
    assert store.save_agent(payload) == created
    assert store.agent_request(' create-agent ') == store.agent_request('create-agent')
    minimal = dict(name='Defaults', template_id=payload['template_id'], request_id='defaults')
    first = store.save_agent(minimal)
    assert store.save_agent(minimal | dict(model='default', skills=[], tools=['read'], enabled=True)) == first


@pytest.mark.parametrize('change', [dict(name='Other'), dict(model='another'), dict(skills=[]), dict(tools=[]), dict(enabled=False)])
def test_conflicting_payload_does_not_mutate_or_duplicate(setup, change):
    store, payload = setup
    original = store.save_agent(payload)
    with store.connect() as db: before = list(db.iterdump())
    with pytest.raises(ValueError, match='不同'):
        store.save_agent(payload | change)
    with store.connect() as db: assert list(db.iterdump()) == before
    assert store.agent(original['id']) == original


def test_replay_after_rename_disable_and_unreadable_catalog_returns_original(setup, monkeypatch):
    store, payload = setup
    original = store.save_agent(payload)
    store.save_agent(dict(name='Renamed', enabled=False), original['id'])
    def unavailable(*args, **kwargs): raise ValueError('catalog unavailable')
    monkeypatch.setattr(skill_inputs, 'load_selected', unavailable)
    assert store.save_agent(payload) == original
    assert store.agent_request(payload['request_id'])['agent'] == original
    assert store.agent(original['id'])['name'] == 'Renamed'
    assert store.agent(original['id'])['enabled'] is False
    reopened = Store(store.data_dir)
    assert reopened.agent_request(payload['request_id']) == store.agent_request(payload['request_id'])
    assert reopened.save_agent(payload) == original


@pytest.mark.parametrize('change', [dict(template_id='missing'), dict(skills=['unknown']), dict(name=''), dict(enabled=1), dict(tools=['unknown'])])
def test_invalid_create_has_no_partial_identity_or_receipt(setup, change):
    store, payload = setup
    with store.connect() as db: before = list(db.iterdump())
    with pytest.raises(ValueError): store.save_agent(payload | change)
    with store.connect() as db: assert list(db.iterdump()) == before
    assert store.agent_request(payload['request_id']) is None


def test_receipt_insert_failure_rolls_back_agent_insert(setup):
    store, payload = setup
    before = len(store.agents())
    with store.connect() as db:
        db.execute("CREATE TRIGGER injected_receipt_failure BEFORE INSERT ON agent_creation_requests BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'): store.save_agent(payload)
    assert len(store.agents()) == before
    assert store.agent_request(payload['request_id']) is None


def test_legacy_creation_and_patch_remain_compatible(setup):
    store, payload = setup
    legacy = {k: v for k, v in payload.items() if k != 'request_id'}
    first, second = store.save_agent(legacy), store.save_agent(legacy)
    assert first['id'] != second['id']
    assert store.agent_request(payload['request_id']) is None
    with pytest.raises(ValueError): store.save_agent({'request_id': 'patch-key', 'name': 'wrong'}, first['id'])
    # Historical unavailable tags can remain unchanged while unrelated identity fields are edited.
    with store.connect() as db:
        db.execute('UPDATE agents SET skills=? WHERE id=?', (json.dumps(['legacy-unknown']), first['id']))
    changed = store.save_agent({'name': 'Updated', 'skills': ['legacy-unknown']}, first['id'])
    assert changed['name'] == 'Updated' and changed['skills'] == ['legacy-unknown']


@pytest.mark.parametrize('key', ['', ' ', None, False, 123, 'x' * 121])
def test_invalid_request_keys_are_not_treated_as_legacy(setup, key):
    store, payload = setup
    with pytest.raises(ValueError): store.save_agent(payload | {'request_id': key})
    with pytest.raises(ValueError): store.agent_request(key)


def test_receipts_are_immutable_and_get_is_read_only(setup):
    store, payload = setup
    store.save_agent(payload)
    with store.connect() as db: before = list(db.iterdump())
    store.agent_request(payload['request_id'])
    assert store.agent_request('missing') is None
    with store.connect() as db:
        assert list(db.iterdump()) == before
        for sql in ('UPDATE agent_creation_requests SET response=response', 'DELETE FROM agent_creation_requests',
                    'INSERT OR REPLACE INTO agent_creation_requests SELECT * FROM agent_creation_requests'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'): db.execute(sql)
