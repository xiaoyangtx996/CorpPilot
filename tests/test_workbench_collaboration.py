import copy
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench.collaboration import Collaboration
from workbench.store import Store


def setup(tmp_path):
    store = Store(tmp_path)
    people = store.agents()[:3]
    source = store.save_conversation({'type': 'dm', 'title': 'secretary', 'member_ids': [people[0]['id']]})
    message = store.send_message(source['id'], {'content': 'PRIVATE ORIGINAL', 'request_id': 'owner'})
    api = Collaboration(store)
    payload = dict(request_id='plan', source_message_id=message['id'], title='project', shared_brief='EXPLICIT SHARED', coordinator_id=people[0]['id'], tasks=[
        dict(key='a', title='first', scope='scope', acceptance='acceptance', agent_id=people[1]['id'], depends_on=[]),
        dict(key='b', title='second', scope='scope2', acceptance='acceptance2', agent_id=people[1]['id'], depends_on=['a'])])
    return store, api, source['id'], payload


def counts(store):
    with store.connect() as db:
        return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['conversations', 'members', 'messages', 'tasks', 'task_revisions', 'task_dependencies', 'collaboration_receipts'])


def test_atomic_project_authority_privacy_and_gates(tmp_path):
    store, api, cid, payload = setup(tmp_path)
    receipt = api.create(cid, payload)
    project = store.conversation(receipt['project_conversation_id'])
    assert project['type'] == 'project'
    assert project['member_ids'] == sorted({payload['coordinator_id'], payload['tasks'][0]['agent_id']})
    messages = store.messages(project['id'])
    assert len(messages) == 1 and messages[0]['content'] == 'EXPLICIT SHARED'
    assert 'PRIVATE ORIGINAL' not in str(messages)
    assert set(receipt['task_ids']) == {'a', 'b'}
    for task in api.tasks.list(project['id']):
        assert task['requirement_version'] == 1
        assert task['source_message_id'] == receipt['shared_message_id']
    assert api.tasks.dependencies(receipt['task_ids']['a'])['ready']
    assert not api.tasks.dependencies(receipt['task_ids']['b'])['ready']
    assert api.tasks.history(receipt['task_ids']['b'])[0]['dependency_task_ids'] == [receipt['task_ids']['a']]
    with store.connect() as db:
        for table in ('task_executions', 'runs'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert api.get(receipt['id']) == receipt and api.list(cid) == [receipt]
    assert receipt['approved_plan'] == payload


@pytest.mark.parametrize('mutation', ['cycle', 'self', 'duplicate_dependency', 'unknown', 'duplicate', 'member', 'disabled', 'source', 'archive', 'coordinator', 'extra', 'oversize', 'taskextra', 'surrogate'])
def test_reject_rolls_back_every_table(tmp_path, mutation):
    store, api, cid, payload = setup(tmp_path)
    if mutation == 'cycle': payload['tasks'][0]['depends_on'] = ['b']
    if mutation == 'self': payload['tasks'][0]['depends_on'] = ['a']
    if mutation == 'duplicate_dependency': payload['tasks'][1]['depends_on'] = ['a', 'a']
    if mutation == 'unknown': payload['tasks'][0]['depends_on'] = ['nope']
    if mutation == 'duplicate': payload['tasks'][1]['key'] = 'a'
    if mutation == 'member': payload['tasks'][1]['agent_id'] = 'missing'
    if mutation == 'disabled': store.save_agent({'enabled': False}, payload['tasks'][0]['agent_id'])
    if mutation == 'source': payload['source_message_id'] = store.send_message(cid, {'content': 'agent', 'request_id': 'agent'}, actor_id=payload['coordinator_id'])['id']
    if mutation == 'archive': store.save_conversation({'archived': True}, cid)
    if mutation == 'coordinator': payload['coordinator_id'] = payload['tasks'][0]['agent_id']
    if mutation == 'extra': payload['private_history'] = 'forbidden'
    if mutation == 'oversize': payload['tasks'] = [dict(payload['tasks'][0], key=f'x{i}', scope='中' * 16000) for i in range(3)]
    if mutation == 'taskextra': payload['tasks'][0]['tools'] = ['execute']
    if mutation == 'surrogate': payload['shared_brief'] = '\ud800'
    before = counts(store)
    with pytest.raises((ValueError, PermissionError)):
        api.create(cid, payload)
    assert counts(store) == before


def test_exact_replay_concurrent_restart_stale_authority(tmp_path):
    store, api, cid, payload = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: api.create(cid, payload), range(8)))
    assert all(row == rows[0] for row in rows)
    first = rows[0]
    task = api.tasks.get(first['task_ids']['a'])
    api.tasks.revise(task['id'], {**{key: task[key] for key in ('title', 'scope', 'acceptance', 'agent_id')}, 'expected_version': 1, 'title': 'changed'})
    store.save_conversation({'archived': True}, cid)
    store.save_conversation({'archived': True}, first['project_conversation_id'])
    store.save_agent({'enabled': False}, payload['coordinator_id'])
    assert Collaboration(Store(tmp_path)).create(cid, payload) == first
    assert api.get(first['id'])['approved_plan'] == payload
    assert api.list(cid)[0]['approved_plan'] == payload
    changed = copy.deepcopy(payload); changed['shared_brief'] += ' '
    with pytest.raises(ValueError, match='不同'):
        api.create(cid, changed)
    changed = copy.deepcopy(payload)
    changed['tasks'][0]['key'] = 'renamed'
    changed['tasks'][1]['depends_on'] = ['renamed']
    with pytest.raises(ValueError, match='不同'):
        api.create(cid, changed)
    assert counts(store)[0] == 2


def test_receipts_immutable_even_replace(tmp_path):
    store, api, cid, payload = setup(tmp_path)
    receipt = api.create(cid, payload)
    with store.connect() as db:
        for sql in ["UPDATE collaboration_receipts SET snapshot='{}'", 'DELETE FROM collaboration_receipts', 'INSERT OR REPLACE INTO collaboration_receipts SELECT * FROM collaboration_receipts']:
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                db.execute(sql)
    assert api.get(receipt['id']) == receipt


def test_late_task_insert_failure_atomic(tmp_path, monkeypatch):
    store, api, cid, payload = setup(tmp_path)
    original = api.tasks._create
    def fail(db, conversation, task):
        if task['request_id'] == 'b': raise RuntimeError('injected disk failure')
        return original(db, conversation, task)
    monkeypatch.setattr(api.tasks, '_create', fail)
    before = counts(store)
    with pytest.raises(RuntimeError): api.create(cid, payload)
    assert counts(store) == before

@pytest.mark.parametrize('size', [0, 17])
def test_task_count_rejected(tmp_path, size):
    store, api, cid, payload = setup(tmp_path)
    payload['tasks'] = [dict(payload['tasks'][0], key=f't{i}') for i in range(size)]
    before = counts(store)
    with pytest.raises(ValueError, match='1–16'):
        api.create(cid, payload)
    assert counts(store) == before


def test_sixteen_tasks_and_dependency_shape(tmp_path):
    store, api, cid, payload = setup(tmp_path)
    payload['tasks'] = [dict(payload['tasks'][0], key=f't{i}', depends_on=[] if i == 0 else [f't{i-1}']) for i in range(16)]
    invalid = copy.deepcopy(payload)
    invalid['tasks'][-1]['depends_on'] = 't14'
    before = counts(store)
    with pytest.raises(ValueError, match='依赖'):
        api.create(cid, invalid)
    assert counts(store) == before
    receipt = api.create(cid, payload)
    assert len(receipt['task_ids']) == 16
    assert all(api.tasks.get(task_id)['requirement_version'] == 1 for task_id in receipt['task_ids'].values())
    assert api.tasks.dependencies(receipt['task_ids']['t0'])['ready']
    assert not api.tasks.dependencies(receipt['task_ids']['t15'])['ready']
