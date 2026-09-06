"""An approved team plan creates one real project without exposing private history."""
from copy import deepcopy

from test_workbench_api import running, request
from workbench.store import Store
from workbench.executions import Executions


def setup_plan(path):
    store = Store(path)
    coordinator, builder, reviewer = store.agents()[:3]
    store.save_agent({'tools': ['read', 'delegate']}, coordinator['id'])
    source = store.save_conversation({'type': 'dm', 'title': 'Secretary private discussion',
                                      'member_ids': [coordinator['id']]})
    private = store.send_message(source['id'], {'content': 'PRIVATE-PREHISTORY-ONLY', 'request_id': 'private'})
    target = store.send_message(source['id'], {'content': 'PRIVATE-ORIGINAL-GOAL', 'request_id': 'goal'})
    payload = {'request_id': 'approved-team-plan', 'source_message_id': target['id'],
               'title': 'Approved focused project', 'shared_brief': 'Only this approved brief is shared.',
               'coordinator_id': coordinator['id'], 'tasks': [
                   {'key': 'build', 'title': 'Build', 'scope': 'Implement the agreed change',
                    'acceptance': 'Output a reviewable file', 'agent_id': builder['id'], 'depends_on': []},
                   {'key': 'review', 'title': 'Review', 'scope': 'Review the approved build',
                    'acceptance': 'Record concrete findings', 'agent_id': reviewer['id'], 'depends_on': ['build']},
               ]}
    return store, source, private, payload


def test_http_plan_creates_shared_project_and_stable_receipt_after_restart(tmp_path):
    store, source, private, payload = setup_plan(tmp_path)
    path = '/api/workbench/conversations/' + source['id'] + '/collaboration-plans'
    with running(tmp_path) as port:
        assert request(port, 'GET', path) == (200, [])
        status, receipt = request(port, 'POST', path, payload)
        assert status == 201
        assert receipt['approved_plan'] == payload
        assert request(port, 'POST', path, payload) == (201, receipt)
        assert request(port, 'GET', path) == (200, [receipt])
        project_id = receipt['project_conversation_id']
        project_path = '/api/workbench/conversations/' + project_id
        project = request(port, 'GET', project_path)[1]
        assert project['type'] == 'project'
        assert set(project['member_ids']) == {payload['coordinator_id'], *(t['agent_id'] for t in payload['tasks'])}
        messages = request(port, 'GET', project_path + '/messages')[1]
        assert len(messages) == 1 and messages[0]['content'] == payload['shared_brief']
        assert messages[0]['id'] == receipt['shared_message_id']
        assert messages[0]['sender_kind'] == 'owner'
        task_ids = receipt['task_ids']
        tasks = request(port, 'GET', project_path + '/tasks')[1]
        assert {t['id'] for t in tasks} == set(task_ids.values())
        assert all(t['source_message_id'] == messages[0]['id'] and t['requirement_version'] == 1 for t in tasks)
        deps = request(port, 'GET', '/api/workbench/tasks/' + task_ids['review'] + '/dependencies')[1]
        assert deps['task_ids'] == [task_ids['build']] and deps['ready'] is False
        assert request(port, 'GET', '/api/workbench/tasks/' + task_ids['build'] + '/dependencies')[1]['ready']
        for task in tasks:
            assert request(port, 'GET', '/api/workbench/tasks/' + task['id'] + '/executions') == (200, [])
        assert request(port, 'GET', project_path + '/runs') == (200, [])
        assert request(port, 'GET', '/api/workbench/cli-runtime')[1]['active_requests'] == 0
        # Creating the team never grants extra tools or source DM membership.
        builder = payload['tasks'][0]['agent_id']
        assert store.agent(builder)['tools'] == ['read']
        assert store.conversation(source['id'])['member_ids'] == [payload['coordinator_id']]
        assert private['id'] not in {m['id'] for m in messages}
        # Receipt remains the original creation acknowledgement after live state changes.
        assert request(port, 'PATCH', project_path, {'archived': True})[0] == 200
        assert request(port, 'PATCH', '/api/workbench/conversations/' + source['id'], {'archived': True})[0] == 200
        assert request(port, 'PATCH', '/api/workbench/agents/' + payload['coordinator_id'], {'enabled': False})[0] == 200
    with running(tmp_path) as port:
        assert request(port, 'GET', '/api/workbench/collaboration-plans/' + receipt['id']) == (200, receipt)
        assert request(port, 'POST', path, payload) == (201, receipt)
        changed = {**payload, 'shared_brief': 'Changed meaning'}
        assert request(port, 'POST', path, changed)[0] == 400
        assert len(store.conversations()) == 2


def test_http_invalid_plan_is_atomic_and_obeys_origin_guard(tmp_path):
    store, source, _, payload = setup_plan(tmp_path)
    path = '/api/workbench/conversations/' + source['id'] + '/collaboration-plans'
    with running(tmp_path) as port:
        status, denied = request(port, 'POST', path, headers={'Origin': 'https://foreign.example'})
        assert status == 400 and '跨来源' in denied['error']
        invalid = deepcopy(payload)
        invalid['tasks'][0]['depends_on'] = ['review']
        assert request(port, 'POST', path, invalid)[0] == 400
        foreign = store.save_conversation({'type': 'project', 'title': 'Other source', 'member_ids': [payload['coordinator_id']]})
        message = store.send_message(foreign['id'], {'content': 'Foreign goal', 'request_id': 'goal'})
        assert request(port, 'POST', path, {**payload, 'source_message_id': message['id']})[0] == 400
        assert request(port, 'GET', path) == (200, [])
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM conversations').fetchone()[0] == 2


def test_plan_tasks_use_existing_execution_authority_and_private_context_boundary(tmp_path):
    from workbench.collaboration import Collaboration
    store, source, _, payload = setup_plan(tmp_path)
    receipt = Collaboration(store).create(source['id'], payload)
    executions = Executions(store)
    builder_id = payload['tasks'][0]['agent_id']
    store.save_agent({'tools': ['read', 'write', 'execute']}, builder_id)
    run = executions.create(receipt['task_ids']['build'], {
        'request_id': 'separate-owner-execution-confirmation', 'expected_version': 1,
        'previous_execution_id': None, 'reconciliation_note': '',
    })
    assert executions.claim(run['id'])
    snapshot = executions.snapshot(run['id'], include_artifacts=True)
    assert snapshot['source_message']['content'] == payload['shared_brief']
    assert 'PRIVATE-PREHISTORY' not in str(snapshot) and 'PRIVATE-ORIGINAL-GOAL' not in str(snapshot)
    assert snapshot['memories'] == []
    # No actual process was started by this fixed record lifecycle.
    executions.report(run['id'], 1, 1, None, 'Fixed record only', not_started=True)
