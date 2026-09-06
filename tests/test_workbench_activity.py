"""Agent observation keeps current assignment separate from actual historical work."""
import json

import pytest

from test_workbench_tasks import setup, revision
from test_workbench_api import running, request
from workbench import activity
from workbench.executions import Executions
from workbench.runs import Runs
from workbench.reviews import Reviews


def fixture(tmp_path):
    store, tasks, cid, payload = setup(tmp_path)
    Executions(store)
    Runs(store)
    Reviews(store)
    return store, tasks, cid, payload


def add_execution(db, task, identity):
    db.execute('''INSERT INTO task_executions(id,task_id,agent_id,requirement_version,attempt,
               request_id,reconciliation_note,state) VALUES(?,?,?,?,1,?,'','failed')''',
               (identity, task['id'], task['agent_id'], task['requirement_version'], identity))


def add_run(db, cid, payload, identity):
    db.execute('''INSERT INTO runs(id,conversation_id,agent_id,source_message_id,request_id,state,usage,error)
               VALUES(?,?,?,?,?,'unknown',?,'unverified')''',
               (identity, cid, payload['agent_id'], payload['source_message_id'], identity,
                json.dumps({'input_tokens': 3})))


def test_assignment_history_disabled_archived_and_read_only(tmp_path):
    store, tasks, cid, payload = fixture(tmp_path)
    task = tasks.create(cid, payload)
    other = next(a['id'] for a in store.agents() if a['id'] != payload['agent_id'])
    with store.connect() as db:
        add_execution(db, task, 'old-execution')
        add_run(db, cid, payload, 'old-model')
    tasks.revise(task['id'], revision(payload, agent_id=other, title='New title'))
    store.save_agent({'enabled': False}, payload['agent_id'])
    with store.connect() as db:
        db.execute('UPDATE conversations SET archived=1 WHERE id=?', (cid,))
        before = list(db.iterdump())
    old = activity.get(store, payload['agent_id'])
    new = activity.get(store, other)
    assert old['tasks']['items'] == []
    assert new['tasks']['items'][0]['title'] == 'New title'
    execution = old['executions']['items'][0]
    assert execution['task_title'] == 'Title' and execution['requirement_version'] == 1
    assert execution['conversation_id'] == cid and execution['artifact_count'] == 0
    assert execution['review_decision'] is None and execution['state'] == 'failed'
    assert new['executions']['items'] == [] and new['model_runs']['items'] == []
    assert old['model_runs']['items'][0]['state'] == 'unknown'
    assert 'Owner requirement' not in json.dumps(old)
    with store.connect() as db:
        assert list(db.iterdump()) == before


def test_all_model_kinds_preserve_usage_without_private_snapshots(tmp_path):
    store, _, cid, payload = fixture(tmp_path)
    with store.connect() as db:
        for kind in ('reply', 'planning', 'retrospective', 'peer_review'):
            add_run(db, cid, payload, kind)
        db.execute("INSERT INTO planning_requests(run_id,payload,candidate_snapshot) VALUES('planning','private plan','private candidates')")
        db.execute("INSERT INTO retrospective_requests(run_id,scope,scope_id,payload,input_snapshot) VALUES('retrospective','agent',?,'private retro','private snapshot')", (payload['agent_id'],))
        db.execute("INSERT INTO peer_review_requests(run_id,payload,source_run_id,source_snapshot,target_instructions) VALUES('peer_review','private peer','reply','private source','private instructions')")
    result = activity.get(store, payload['agent_id'])
    assert {row['kind'] for row in result['model_runs']['items']} == {'reply', 'planning', 'retrospective', 'peer_review'}
    for row in result['model_runs']['items']:
        assert row['usage'] == {'input_tokens': 3} and row['error'] == 'unverified'
    assert 'private' not in json.dumps(result)


def test_recent_fifty_totals_and_stable_order(tmp_path):
    store, tasks, cid, payload = fixture(tmp_path)
    for index in range(52):
        task = tasks.create(cid, {**payload, 'request_id': f'task-{index}'})
        with store.connect() as db:
            add_execution(db, task, f'e-{index:03}')
            add_run(db, cid, payload, f'r-{index:03}')
    with store.connect() as db:
        for table in ('tasks', 'task_executions', 'runs'):
            db.execute(f"UPDATE {table} SET updated_at='2026-01-01T00:00:00Z'")
    result = activity.get(store, payload['agent_id'])
    for name in ('tasks', 'executions', 'model_runs'):
        group = result[name]
        assert group['total'] == 52 and group['has_more'] is True
        assert len(group['items']) == 50
        identifiers = [row['id'] for row in group['items']]
        assert identifiers == sorted(identifiers, reverse=True)
    assert result['executions']['items'][0]['id'] == 'e-051'
    with pytest.raises(KeyError):
        activity.get(store, 'missing')


@pytest.mark.parametrize('decision', ['approved', 'rejected'])
def test_exact_execution_artifact_counts_and_owner_review(tmp_path, decision):
    store, tasks, cid, payload = fixture(tmp_path)
    task = tasks.create(cid, payload)
    with store.connect() as db:
        add_execution(db, task, 'reviewed')
        db.execute("UPDATE task_executions SET state='awaiting_review' WHERE id='reviewed'")
        for index in range(2):
            db.execute("INSERT INTO execution_artifacts VALUES(?,'reviewed',?,0,?,?)",
                       (str(index), f'{index}.txt', '0' * 64, b''))
        db.execute("INSERT INTO execution_reviews(execution_id,request_id,requirement_version,decision,note,artifact_ids) VALUES('reviewed','review',1,?,'','[]')", (decision,))
    execution = activity.get(store, payload['agent_id'])['executions']['items'][0]
    assert execution['artifact_count'] == 2 and execution['review_decision'] == decision
    assert execution['state'] == 'awaiting_review'
    assert 'content' not in execution and 'artifact_ids' not in execution


def test_activity_http_owner_auth_and_missing(tmp_path):
    with running(tmp_path) as port:
        _, agents = request(port, 'GET', '/api/workbench/agents')
        route = '/api/workbench/agents/' + agents[0]['id'] + '/activity'
        assert request(port, 'GET', route, headers={'Authorization': ''})[0] == 401
        status, value = request(port, 'GET', route)
        assert status == 200 and value['agent_id'] == agents[0]['id']
        for name in ('tasks', 'executions', 'model_runs'):
            assert value[name] == {'items': [], 'total': 0, 'has_more': False}
        assert request(port, 'GET', '/api/workbench/agents/missing/activity')[0] == 404
