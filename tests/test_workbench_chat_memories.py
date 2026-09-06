"""Approved memory reaches only its ordinary reply identity and fixed conversation."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_workbench_memories import setup, proposal, decision
from workbench import chat_memories as chats
from workbench.runs import Runs


@pytest.fixture
def prepared(tmp_path):
    store, tasks, executions, task, upstream, memory = setup(tmp_path)
    runs = Runs(store)
    for scope, identity in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        candidate = memory.propose(scope, identity, proposal(upstream, scope, content=scope + ' approved memory'))
        memory.decide(candidate['id'], decision('approve-' + scope))
    run = runs.create(task['conversation_id'], dict(agent_id=task['agent_id'], source_message_id=task['source_message_id'], request_id='chat'))
    with store.connect() as db:
        chats.initialize(db)
    return store, runs, task, run, memory


def bind(store, run):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        result = chats.freeze(db, run)
        db.execute("UPDATE runs SET state='running' WHERE id=?", (run['id'],))
        return result


def test_approved_versions_freeze_once_and_rollback_only_changes_new_runs(prepared):
    store, runs, task, run, memory = prepared
    original = bind(store, run)
    assert [d['content'] for d in original] == ['agent approved memory', 'project approved memory']
    for scope, identity in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        memory.rollback(scope, identity, dict(request_id='rollback-' + scope, expected_version=1, target_version=0, note='Restore baseline'))
    with store.connect() as db:
        assert chats.snapshot(db, run) == original
        assert chats.freeze(db, run) == original
        metadata = chats.get(db, run)
        assert all('content' not in item for item in metadata['memories'])
        assert all(item['version'] == 1 for item in metadata['memories'])
        assert metadata['memories'][0]['sha256'] == hashlib.sha256(original[0]['content'].encode()).hexdigest()
    later = runs.create(task['conversation_id'], dict(agent_id=task['agent_id'], source_message_id=task['source_message_id'], request_id='later'))
    newest = bind(store, later)
    assert [d['version'] for d in newest] == [2, 2]
    assert [d['content'] for d in newest] == ['', '']
    assert chats.augment('role', newest) == 'role'


def test_dm_only_own_memory_and_new_identity_has_explicit_zero(prepared):
    store, runs, task, run, _ = prepared
    other = next(a for a in store.agents() if a['id'] != task['agent_id'])
    for agent in (task['agent_id'], other['id']):
        room = store.save_conversation(dict(type='dm', title='Private', member_ids=[agent]))
        source = store.send_message(room['id'], dict(content='Question', request_id='source'))
        reply = runs.create(room['id'], dict(agent_id=agent, source_message_id=source['id'], request_id='reply'))
        documents = bind(store, reply)
        assert len(documents) == 1 and documents[0]['scope'] == 'agent' and documents[0]['scope_id'] == agent
        assert documents[0]['version'] == (1 if agent == task['agent_id'] else 0)
        if agent == other['id']:
            assert documents[0]['content'] == ''
            with store.connect() as db:
                assert chats.get(db, reply)['memories'][0]['sha256'] == hashlib.sha256(b'').hexdigest()


@pytest.mark.parametrize('change', ['disabled', 'removed', 'archived'])
def test_consumption_rechecks_authority_but_owner_history_remains_readable(prepared, change):
    store, _, task, run, _ = prepared
    bind(store, run)
    with store.connect() as db:
        before = chats.get(db, run)
        if change == 'disabled': db.execute('UPDATE agents SET enabled=0 WHERE id=?', (run['agent_id'],))
        if change == 'removed': db.execute('DELETE FROM members WHERE conversation_id=? AND agent_id=?', (run['conversation_id'], run['agent_id']))
        if change == 'archived': db.execute('UPDATE conversations SET archived=1 WHERE id=?', (run['conversation_id'],))
        assert chats.get(db, run) == before
        with pytest.raises((ValueError, PermissionError)):
            chats.snapshot(db, run)


def test_no_new_read_tool_gate_and_no_latest_documents_on_snapshot(prepared, monkeypatch):
    store, _, _, run, _ = prepared
    store.save_agent({'tools': []}, run['agent_id'])
    original = bind(store, run)
    real = chats._document
    def fixed(db, scope, identity, version=None):
        assert version is not None, 'Snapshot read latest instead of fixed version'
        return real(db, scope, identity, version)
    monkeypatch.setattr(chats, '_document', fixed)
    with store.connect() as db:
        assert chats.snapshot(db, run) == original


def test_absent_history_is_null_and_cannot_be_backfilled_after_start(prepared):
    store, _, _, run, _ = prepared
    with store.connect() as db:
        assert chats.get(db, run) is None
        db.execute("UPDATE runs SET state='running' WHERE id=?", (run['id'],))
        with pytest.raises(ValueError): chats.snapshot(db, run)
        with pytest.raises(ValueError): chats.freeze(db, run)
        assert chats.get(db, run) is None


def test_actual_run_scope_and_immutable_records(prepared):
    store, _, _, run, _ = prepared
    bind(store, run)
    with store.connect() as db:
        for change in ({'agent_id': 'other'}, {'conversation_id': 'other'}, {'attempt': True}, {'requirement_version': 2}):
            for action in (chats.get, chats.snapshot, chats.freeze):
                with pytest.raises(ValueError): action(db, run | change)
        with pytest.raises(KeyError): chats.get(db, run | {'id': 'missing'})
        for sql in ('UPDATE model_memory_snapshots SET binding=binding', 'DELETE FROM model_memory_snapshots',
                    'INSERT OR REPLACE INTO model_memory_snapshots SELECT * FROM model_memory_snapshots'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'): db.execute(sql)


@pytest.mark.parametrize('field', ['scope', 'scope_id', 'version', 'chars', 'sha256', 'outer', 'count'])
def test_frozen_binding_tamper_is_rejected(prepared, field):
    store, _, _, run, _ = prepared
    bind(store, run)
    with store.connect() as db:
        saved = chats.get(db, run)
        if field == 'outer': saved['agent_id'] = 'foreign'
        elif field == 'count': saved['memories'].pop()
        elif field in ('version', 'chars'): saved['memories'][0][field] = True
        else: saved['memories'][0][field] = 'foreign'
        db.execute('DROP TRIGGER model_memory_no_update')
        db.execute('UPDATE model_memory_snapshots SET binding=?', (json.dumps(saved),))
        with pytest.raises(ValueError): chats.get(db, run)


def test_underlying_revision_content_tamper_fails_hash_check(prepared):
    store, _, _, run, _ = prepared
    bind(store, run)
    with store.connect() as db:
        db.execute('DROP TRIGGER memory_revisions_no_update')
        db.execute("UPDATE memory_revisions SET content='changed' WHERE scope='agent'")
        with pytest.raises(ValueError): chats.snapshot(db, run)


@pytest.mark.parametrize('table', ['planning_requests', 'retrospective_requests', 'peer_review_requests'])
def test_specialized_runs_never_receive_ordinary_reply_bindings(prepared, table):
    store, _, _, run, _ = prepared
    with store.connect() as db:
        # A minimal specialized marker isolates classification from unrelated special-run creation contracts.
        db.execute(f'DROP TABLE {table}')
        db.execute(f'CREATE TABLE {table}(run_id TEXT PRIMARY KEY)')
        db.execute(f'INSERT INTO {table} VALUES(?)', (run['id'],))
        assert not chats.is_reply(db, run)
        assert chats.get(db, run) is None
        with pytest.raises(ValueError): chats.freeze(db, run)


def test_claim_transaction_rollback_discards_binding_and_augment_is_bounded(prepared):
    store, _, _, run, _ = prepared
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        documents = chats.freeze(db, run)
        db.rollback()
        assert chats.get(db, run) is None
    text = chats.augment('Current task', documents)
    assert text.startswith('Current task\n\n') and '不是额外指令' in text
    assert 'agent approved memory' in text and 'project approved memory' in text
    with pytest.raises(ValueError, match='64000'): chats.augment('x' * 64000, documents)
    with pytest.raises(ValueError): chats.augment('role', [documents[0] | {'content': 'x' * 8001}])
