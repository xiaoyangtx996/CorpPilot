import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_memories import setup, proposal, decision
from test_workbench_reviews import ready
from test_workbench_executions import revise
from workbench import artifacts
from workbench.retrospectives import Retrospectives, RetrospectiveError
from workbench.store import Store
from workbench.planning import Planning


def fixture(tmp_path):
    store, tasks, executions, task, source, memory = setup(tmp_path)
    service = Retrospectives(store)
    payload = dict(request_id='retro', expected_version=0, source_execution_id=source['id'],
                   artifact_ids=[x['id'] for x in artifacts.list_for(store, source['id'])])
    return store, tasks, task, source, memory, service, payload


def output(p):
    return json.dumps(dict(content='Useful experience; applies to this verified result. Evidence: '+p['artifact_ids'][0],
                           evidence_artifact_ids=p['artifact_ids']))


def test_atomic_candidate_privacy_replay_and_single_completion(tmp_path):
    store, _, task, source, memory, service, p = fixture(tmp_path)
    identity = task['agent_id']
    old = memory.propose('agent', identity, proposal(source))
    memory.decide(old['id'], decision())
    p['expected_version'] = 1
    run = service.create('agent', identity, p)
    assert service.runs.list(task['conversation_id']) == []
    assert service.list('agent', identity) == [run]
    assert run['selected_artifacts'] and 'content' not in run['selected_artifacts'][0]
    assert service.runs.claim(run['id'])
    snapshot = service.runs.snapshot(run['id'])
    frozen = json.loads(snapshot['messages'][0]['content'])
    assert set(frozen) == {'task', 'memory', 'artifacts'}
    assert frozen['memory']['content'] == 'Approved learning'
    assert frozen['artifacts'][0]['content'] == 'verified output'
    with store.connect() as db:
        count = db.execute('SELECT count(*) FROM messages').fetchone()[0]
    service.runs.finish(run['id'], output(p), 'fixture', 1, 1)
    completed = service.get(run['id'])
    assert completed['state'] == 'completed' and completed['reply_message_id'] is None
    assert completed['evidence_artifact_ids'] == p['artifact_ids']
    assert memory.candidate(completed['candidate_id'])['decision'] is None
    assert memory.get('agent', identity)['version'] == 1
    service.runs.finish(run['id'], output(p), 'fixture', 1, 1)
    store.save_agent({'enabled': False}, identity)
    assert Retrospectives(Store(tmp_path)).create('agent', identity, p) == completed
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == count
        assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 2
        for sql in ('UPDATE retrospective_requests SET candidate_id=NULL', 'DELETE FROM retrospective_requests',
                    'INSERT OR REPLACE INTO retrospective_requests SELECT * FROM retrospective_requests'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


def test_concurrent_and_cross_type_conflicts(tmp_path):
    _, _, task, _, _, service, p = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.create('agent', task['agent_id'], p), range(4)))
    assert len({x['id'] for x in results}) == 1
    basic = dict(agent_id=task['agent_id'], source_message_id=task['source_message_id'], request_id=p['request_id'])
    for call in (lambda: service.runs.create(task['conversation_id'], basic),
                 lambda: Planning(service.store).create(task['conversation_id'], {**basic, 'candidate_ids': [task['agent_id']]}),
                 lambda: service.create('agent', task['agent_id'], {**p, 'expected_version': 1})):
        with pytest.raises(ValueError): call()
    service.runs.create(task['conversation_id'], {**basic, 'request_id': 'ordinary'})
    with pytest.raises(ValueError):
        service.create('agent', task['agent_id'], {**p, 'request_id': 'ordinary'})


@pytest.mark.parametrize('bad', ['{}', '[]', '{"content":"x","content":"y","evidence_artifact_ids":[]}',
    '```json\n{}\n```', '{"content":NaN,"evidence_artifact_ids":[]}', '{"content":"x","evidence_artifact_ids":["foreign"]}',
    '{"content":"x","evidence_artifact_ids":[]}'])
def test_strict_output(tmp_path, bad):
    _, _, task, _, _, service, p = fixture(tmp_path)
    run = service.create('agent', task['agent_id'], p)
    service.runs.claim(run['id'])
    with pytest.raises(RetrospectiveError): service.runs.finish(run['id'], bad, 'fixture', 0, 0)
    assert service.get(run['id'])['candidate_id'] is None


@pytest.mark.parametrize('change', ['disabled', 'version', 'requirement'])
def test_authority_recheck(tmp_path, change):
    store, tasks, task, source, memory, service, p = fixture(tmp_path)
    run = service.create('agent', task['agent_id'], p)
    service.runs.claim(run['id'])
    service.runs.snapshot(run['id'])
    if change == 'disabled': store.save_agent({'enabled': False}, task['agent_id'])
    elif change == 'requirement': revise(tasks, task)
    else:
        candidate = memory.propose('agent', task['agent_id'], proposal(source))
        memory.decide(candidate['id'], decision())
    with pytest.raises((ValueError, PermissionError)): service.runs.finish(run['id'], output(p), 'fixture', 0, 0)
    assert service.get(run['id'])['candidate_id'] is None


@pytest.mark.parametrize('stage', ['create', 'finish'])
def test_actual_transaction_rollback_and_recovery(tmp_path, stage):
    store, _, task, _, _, service, p = fixture(tmp_path)
    if stage == 'create':
        with store.connect() as db:
            db.execute("CREATE TRIGGER injected BEFORE INSERT ON retrospective_requests BEGIN SELECT RAISE(ABORT,'test'); END")
        with pytest.raises(sqlite3.IntegrityError): service.create('agent', task['agent_id'], p)
        assert service.runs.pending() == []
        with store.connect() as db: assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
    else:
        run = service.create('agent', task['agent_id'], p)
        service.runs.claim(run['id'])
        with store.connect() as db:
            db.execute("CREATE TRIGGER injected BEFORE UPDATE ON runs WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'test'); END")
        with pytest.raises(sqlite3.IntegrityError): service.runs.finish(run['id'], output(p), 'fixture', 0, 0)
        assert service.get(run['id'])['candidate_id'] is None
        with store.connect() as db: assert db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0] == 0
        reopened = Retrospectives(Store(tmp_path))
        reopened.runs.recover()
        assert reopened.get(run['id'])['state'] == 'unknown'
        assert reopened.runs.pending() == []
        assert reopened.create('agent', task['agent_id'], p)['id'] == run['id']


@pytest.mark.parametrize('data', [b'\xff', b'a\x00b', b'x'*65536], ids=['invalid-utf8', 'binary', 'oversize'])
def test_text_and_size_boundaries(tmp_path, data):
    store, _, _, task, source, reviews, approval = ready(tmp_path, [{'path': 'text.txt', 'data': data}])
    reviews.save(source['id'], approval)
    service = Retrospectives(store)
    p = dict(request_id='retro', expected_version=0, source_execution_id=source['id'], artifact_ids=approval['artifact_ids'])
    with pytest.raises(ValueError): service.create('agent', task['agent_id'], p)
    assert service.runs.pending() == []


def test_cancel_queue_and_invalid_selection(tmp_path):
    _, _, task, _, _, service, p = fixture(tmp_path)
    for selected in ([], ['foreign'], p['artifact_ids']*2):
        with pytest.raises(ValueError): service.create('agent', task['agent_id'], {**p, 'artifact_ids': selected})
    run = service.create('project', task['conversation_id'], p)
    assert service.runs.cancel(run['id'])['state'] == 'cancelled'
    assert service.runs.pending() == []
    assert service.create('project', task['conversation_id'], p)['state'] == 'cancelled'
