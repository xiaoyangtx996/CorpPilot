"""Prepared metadata is immutable evidence, never a reconstruction of old inputs."""
import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_executions import setup, request
from test_workbench_goal_executions import goal
from workbench.runs import Runs
from workbench.context_receipts import ContextReceipts


def fixture(tmp_path):
    store, tasks, executions, task = setup(tmp_path)
    runs = Runs(store)
    model = runs.create(task['conversation_id'], {'agent_id': task['agent_id'], 'source_message_id': task['source_message_id'], 'request_id': 'model'})
    cli = executions.create(task['id'], request())
    return store, tasks, executions, task, runs, model, cli, ContextReceipts(store)


def digest(value):
    return {'chars': len(value), 'sha256': hashlib.sha256(value.encode()).hexdigest()}


@pytest.mark.parametrize('kind', ['model', 'cli'])
def test_old_missing_record_readonly_and_inactive_first_write(tmp_path, kind):
    store, _, executions, _, runs, model, cli, service = fixture(tmp_path)
    run, api = (model, runs) if kind == 'model' else (cli, executions)
    with store.connect() as db:
        before = list(db.iterdump())
    assert service.get(kind, run['id']) is None
    with store.connect() as db:
        assert list(db.iterdump()) == before
    api.claim(run['id'])
    snapshot = api.snapshot(run['id'], include_artifacts=True) if kind == 'cli' else api.snapshot(run['id'])
    if kind == 'model': runs.fail(run['id'], 'failure')
    else: executions.report(run['id'], run['attempt'], run['requirement_version'], 1, 'failure')
    with pytest.raises(ValueError, match='运行中'):
        service.record_model(run['id'], snapshot, 'fixture') if kind == 'model' else service.record_cli(run['id'], snapshot, 'fixture', 'local')
    assert service.get(kind, run['id']) is None


@pytest.mark.parametrize('kind', ['model', 'cli'])
def test_metadata_only_actual_binding_and_late_replay(tmp_path, kind):
    store, _, executions, _, runs, model, cli, service = fixture(tmp_path)
    run, api = (model, runs) if kind == 'model' else (cli, executions)
    api.claim(run['id'])
    snapshot = api.snapshot(run['id'], include_artifacts=True) if kind == 'cli' else api.snapshot(run['id'])
    snapshot['api_key'] = snapshot['agent']['api_key'] = 'secret-key-not-recorded'
    snapshot['agent']['skills'] = ['not-a-loaded-skill']
    def record(data=snapshot):
        return service.record_model(run['id'], data, 'selected-model') if kind == 'model' else service.record_cli(run['id'], data, 'selected-model', 'docker')
    receipt = record()
    assert receipt['instructions'] == digest(snapshot['instructions'])
    assert receipt['agent_id'] == run['agent_id'] and receipt['attempt'] == run['attempt']
    assert receipt['requirement_version'] == run['requirement_version']
    assert receipt['phase'] == 'prepared' and receipt['model'] == 'selected-model'
    encoded = json.dumps(receipt)
    assert 'secret-key-not-recorded' not in encoded and 'not-a-loaded-skill' not in encoded
    assert 'Owner scope' not in encoded and snapshot['instructions'] not in encoded
    if kind == 'model':
        assert receipt['messages'][0]['id'] == model['source_message_id']
        assert receipt['messages'][0]['chars'] == len('Owner scope')
        runs.fail(run['id'], 'failure', 'unknown')
    else:
        assert receipt['memories'] == receipt['input_artifacts'] == []
        assert receipt['task']['scope'] == digest('Bounded scope')
        executions.recover()
    store.save_agent({'name': 'changed', 'enabled': False}, run['agent_id'])
    assert ContextReceipts(store).get(kind, run['id']) == receipt
    assert record() == receipt
    changed = copy.deepcopy(snapshot); changed['instructions'] += ' different'
    with pytest.raises(ValueError, match='冲突'): record(changed)
    for sql in ('UPDATE context_receipts SET payload=payload', 'DELETE FROM context_receipts',
                'INSERT OR REPLACE INTO context_receipts SELECT * FROM context_receipts'):
        with store.connect() as db, pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_concurrent_same_record_and_atomic_failure(tmp_path):
    store, _, _, _, runs, model, _, service = fixture(tmp_path)
    runs.claim(model['id']); snapshot = runs.snapshot(model['id'])
    with store.connect() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON context_receipts BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError): service.record_model(model['id'], snapshot, 'fixture')
    assert service.get('model', model['id']) is None
    with store.connect() as db: db.execute('DROP TRIGGER injected')
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: service.record_model(model['id'], snapshot, 'fixture'), range(8)))
    assert all(row == rows[0] for row in rows)
    with store.connect() as db: assert db.execute('SELECT count(*) FROM context_receipts').fetchone()[0] == 1


@pytest.mark.parametrize('change', ['agent', 'missing_messages', 'many_messages', 'truncated', 'invalid_text', 'sequence'])
def test_invalid_model_metadata_never_writes(tmp_path, change):
    _, _, _, _, runs, model, _, service = fixture(tmp_path)
    runs.claim(model['id']); snapshot = runs.snapshot(model['id'])
    if change == 'agent': snapshot['agent']['id'] = 'other'
    elif change == 'missing_messages': snapshot['messages'] = []
    elif change == 'many_messages': snapshot['messages'] *= 101
    elif change == 'truncated': snapshot['context_truncated'] = 1
    elif change == 'invalid_text': snapshot['messages'][0]['content'] = '\ud800'
    else: snapshot['messages'][0]['sequence'] = True
    with pytest.raises(ValueError): service.record_model(model['id'], snapshot, 'fixture')
    assert service.get('model', model['id']) is None


def test_model_last_100_and_source_boundary(tmp_path):
    store, _, _, task, runs, _, _, service = fixture(tmp_path)
    for i in range(102):
        source = store.send_message(task['conversation_id'], {'content': f'message-{i}', 'request_id': f'm{i}'})
    row = runs.create(task['conversation_id'], {'agent_id': task['agent_id'], 'source_message_id': source['id'], 'request_id': 'bounded'})
    store.send_message(task['conversation_id'], {'content': 'AFTER SOURCE', 'request_id': 'later'})
    runs.claim(row['id']); snapshot = runs.snapshot(row['id'])
    result = service.record_model(row['id'], snapshot, 'fixture')
    assert len(result['messages']) == 100 and result['context_truncated'] is True
    assert result['messages'][-1]['id'] == source['id'] and result['source_sequence'] == source['sequence']


def test_real_retrospective_synthetic_message(tmp_path):
    from test_workbench_retrospectives import fixture as retrospective_fixture
    store, _, task, _, _, retro, payload = retrospective_fixture(tmp_path)
    row = retro.create('agent', task['agent_id'], payload)
    retro.runs.claim(row['id']); snapshot = retro.runs.snapshot(row['id'])
    result = ContextReceipts(store).record_model(row['id'], snapshot, 'fixture')
    message = result['messages'][0]
    assert result['run_kind'] == 'retrospective' and result['source_sequence'] == 0
    assert message['id'] is None and message['sequence'] is None
    assert message['content_source'] == 'retrospective_snapshot'
    assert {k: message[k] for k in ('chars', 'sha256')} == digest(snapshot['messages'][0]['content'])
    assert 'verified output' not in json.dumps(result)


def test_real_peer_single_source(tmp_path):
    from test_workbench_peer_reviews import fixture as peer_fixture
    store, runs, cid, original, peer, payload = peer_fixture(tmp_path)
    row = peer.create(cid, payload); runs.claim(row['id'])
    result = ContextReceipts(store).record_model(row['id'], runs.snapshot(row['id']), 'fixture')
    assert result['run_kind'] == 'peer_review' and len(result['messages']) == 1
    assert result['messages'][0]['id'] == original['reply_message_id']


def test_real_planning_source_and_candidate_instructions_hash(tmp_path):
    from workbench.planning import Planning
    store, _, _, task, runs, _, _, service = fixture(tmp_path)
    row = Planning(store).create(task['conversation_id'], {'agent_id': task['agent_id'], 'source_message_id': task['source_message_id'], 'request_id': 'planning', 'candidate_ids': [task['agent_id']]})
    runs.claim(row['id']); snapshot = runs.snapshot(row['id'])
    result = service.record_model(row['id'], snapshot, 'fixture')
    assert result['run_kind'] == 'planning' and result['instructions'] == digest(snapshot['instructions'])
    assert result['messages'][0]['content_source'] == 'conversation_message'


@pytest.mark.parametrize('change', ['agent', 'version', 'source', 'memory_scope', 'memory_count', 'backend', 'absolute_path'])
def test_invalid_cli_metadata_never_writes(tmp_path, change):
    _, _, executions, _, _, _, cli, service = fixture(tmp_path)
    executions.claim(cli['id']); snapshot = executions.snapshot(cli['id'], include_artifacts=True)
    backend = 'local'
    if change == 'agent': snapshot['task']['agent_id'] = 'other'
    elif change == 'version': snapshot['task']['requirement_version'] += 1
    elif change == 'source': snapshot['source_message']['id'] = 'other'
    elif change == 'memory_scope': snapshot['memories'] = [{'scope': 'agent', 'scope_id': 'other', 'version': 1, 'content': 'private'}]
    elif change == 'memory_count': snapshot['memories'] = [{}] * 3
    elif change == 'backend': backend = 'unknown'
    else: snapshot['input_artifacts'] = [{'id': cli['id'], 'execution_id': cli['id'], 'path': 'C:/secret', 'size': 1, 'sha256': 'a' * 64}]
    with pytest.raises(ValueError): service.record_cli(cli['id'], snapshot, 'fixture', backend)
    assert service.get('cli', cli['id']) is None


def test_invalid_kind_and_missing_instance(tmp_path):
    service = fixture(tmp_path)[-1]
    with pytest.raises(ValueError): service.get('any', 'missing')
    for kind in ('model', 'cli'):
        with pytest.raises(KeyError): service.get(kind, 'missing')


def test_real_goal_hashes_authorized_brief_not_original_private_text(goal):
    store, service, source, payload, _ = goal
    row = service.create(source, payload)
    runs = service.planning.runs
    runs.claim(row['planning_run_id'])
    snapshot = runs.snapshot(row['planning_run_id'])
    receipt = ContextReceipts(store).record_model(row['planning_run_id'], snapshot, 'fixture')
    message = receipt['messages'][0]
    assert message['id'] == payload['source_message_id']
    assert message['content_source'] == 'authorized_shared_brief'
    assert {k: message[k] for k in ('chars', 'sha256')} == digest(payload['shared_brief'])
    assert 'ONLY AUTHORIZED BRIEF' not in json.dumps(receipt)
    assert 'TARGET ONLY' not in json.dumps(receipt)


def test_real_cli_frozen_memory_and_artifact_metadata_survive_current_changes(tmp_path, monkeypatch):
    from test_workbench_memories import setup as memory_setup, proposal, decision
    from test_workbench_dependencies import sibling, link
    from workbench import artifacts
    store, tasks, executions, task, upstream, memory = memory_setup(tmp_path)
    for scope, scope_id in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        candidate = memory.propose(scope, scope_id, proposal(upstream, scope))
        memory.decide(candidate['id'], decision('approve-' + scope))
    child = sibling(tasks, task, 'consumer')
    link(tasks, child, [task]); child = tasks.get(child['id'])
    run = executions.create(child['id'], request(version=child['requirement_version']))
    executions.claim(run['id']); snapshot = executions.snapshot(run['id'], include_artifacts=True)
    service = ContextReceipts(store)
    receipt = service.record_cli(run['id'], snapshot, 'fixture', 'local')
    assert len(receipt['memories']) == 2
    assert all(row['version'] == 1 and row['sha256'] == digest('Approved learning')['sha256'] for row in receipt['memories'])
    selected = artifacts.list_for(store, upstream['id'])[0]
    assert receipt['input_artifacts'] == [{k: selected[k] for k in ('id', 'execution_id', 'path', 'size', 'sha256')}]
    for scope, scope_id in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        memory.rollback(scope, scope_id, {'request_id': 'rollback-' + scope, 'expected_version': 1, 'target_version': 0, 'note': 'Owner restored baseline'})
    def forbidden(*args, **kwargs): raise AssertionError('Observation must not reconstruct input')
    monkeypatch.setattr(executions, 'snapshot', forbidden)
    with store.connect() as db: before = list(db.iterdump())
    assert service.get('cli', run['id']) == receipt
    with store.connect() as db: assert list(db.iterdump()) == before
    assert service.record_cli(run['id'], snapshot, 'fixture', 'local') == receipt
