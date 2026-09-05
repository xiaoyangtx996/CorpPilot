"""Actual local HTTP/provider subprocess integration, without paid model calls."""
import json

from test_workbench_api import running, request
from test_workbench_provider import endpoint
from test_workbench_controller import configure, until
from test_workbench_collaboration_api import setup_plan
from workbench.settings import Settings


def test_generated_proposal_is_reviewable_and_never_auto_creates_project(tmp_path, monkeypatch):
    store, source, private, approved = setup_plan(tmp_path)
    proposal = {key: approved[key] for key in ('title', 'shared_brief', 'tasks')}
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}],
              'usage': {'prompt_tokens': 31, 'completion_tokens': 17}}
    payload = {'agent_id': approved['coordinator_id'], 'source_message_id': approved['source_message_id'],
               'request_id': 'generate-once', 'candidate_ids': [t['agent_id'] for t in approved['tasks']]}
    path = '/api/workbench/conversations/' + source['id']
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            status, queued = request(port, 'POST', path + '/collaboration-proposals', payload)
            assert status == 202
            result_path = '/api/workbench/collaboration-proposals/' + queued['id']
            get_result = lambda: request(port, 'GET', result_path)[1]
            result = until(lambda: (value if (value := get_result())['state'] == 'completed' else None))
            assert result['proposal'] == proposal
            assert result['reply_message_id'] is None
            assert result['usage'] == {'prompt_tokens': 31, 'completion_tokens': 17}
            assert len(calls) == 1
            sent = json.dumps(calls[0][2], ensure_ascii=False)
            assert 'PRIVATE-ORIGINAL-GOAL' in sent and private['content'] not in sent
            assert all(t['agent_id'] in sent for t in approved['tasks'])
            assert len(store.messages(source['id'])) == 2
            assert request(port, 'GET', path + '/runs') == (200, [])
            assert request(port, 'GET', path + '/collaboration-plans') == (200, [])
            assert request(port, 'GET', path + '/tasks') == (200, [])
            assert len(request(port, 'GET', '/api/workbench/conversations')[1]) == 1
            assert request(port, 'GET', '/api/workbench/cli-runtime')[1]['active_requests'] == 0
            # Review/edit and explicit Owner approval use the existing atomic project API.
            status, receipt = request(port, 'POST', path + '/collaboration-plans', approved)
            assert status == 201 and receipt['approved_plan'] == approved
            Settings(store).save({'enabled': False})
            store.save_conversation({'archived': True}, source['id'])
            store.save_agent({'enabled': False}, payload['agent_id'])
            assert request(port, 'POST', path + '/collaboration-proposals', payload) == (202, result)
        with running(tmp_path) as port:
            assert request(port, 'GET', result_path) == (200, result)
            assert request(port, 'GET', path + '/collaboration-proposals') == (200, [result])
            assert request(port, 'POST', path + '/collaboration-proposals', payload) == (202, result)
            assert len(calls) == 1


def test_invalid_model_plan_fails_once_without_publishing_or_creating(tmp_path, monkeypatch):
    store, source, _, approved = setup_plan(tmp_path)
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"title":"invalid"}'}}]}
    payload = {'agent_id': approved['coordinator_id'], 'source_message_id': approved['source_message_id'],
               'request_id': 'invalid-output', 'candidate_ids': [approved['tasks'][0]['agent_id']]}
    path = '/api/workbench/conversations/' + source['id']
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            _, run = request(port, 'POST', path + '/collaboration-proposals', payload)
            result_path = '/api/workbench/collaboration-proposals/' + run['id']
            until(lambda: request(port, 'GET', result_path)[1]['state'] == 'failed')
            assert request(port, 'GET', result_path)[1]['proposal'] is None
            assert request(port, 'POST', path + '/collaboration-proposals', payload)[1]['id'] == run['id']
            assert len(calls) == 1 and len(store.messages(source['id'])) == 2
            assert request(port, 'GET', path + '/collaboration-plans') == (200, [])


def test_unconfigured_proposal_stays_cancellable_and_cross_origin_cannot_enqueue(tmp_path):
    store, source, _, approved = setup_plan(tmp_path)
    payload = {'agent_id': approved['coordinator_id'], 'source_message_id': approved['source_message_id'],
               'request_id': 'waiting-config', 'candidate_ids': [approved['tasks'][0]['agent_id']]}
    path = '/api/workbench/conversations/' + source['id'] + '/collaboration-proposals'
    with running(tmp_path) as port:
        # Header-only rejection avoids Windows TCP reset when unread request bytes remain.
        assert request(port, 'POST', path, headers={'Origin': 'https://foreign.invalid'})[0] == 400
        assert request(port, 'GET', path) == (200, [])
        status, queued = request(port, 'POST', path, payload)
        assert status == 202 and queued['state'] == 'queued'
        assert request(port, 'POST', '/api/workbench/runs/' + queued['id'] + '/cancel', {})[1]['state'] == 'cancelled'
        assert request(port, 'POST', path, payload)[1]['state'] == 'cancelled'


def test_coordinator_revocation_during_provider_request_cannot_publish_plan(tmp_path, monkeypatch):
    store, source, _, approved = setup_plan(tmp_path)
    proposal = {key: approved[key] for key in ('title', 'shared_brief', 'tasks')}
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}]}
    payload = {'agent_id': approved['coordinator_id'], 'source_message_id': approved['source_message_id'],
               'request_id': 'revoke-during-flight', 'candidate_ids': [t['agent_id'] for t in approved['tasks']]}
    path = '/api/workbench/conversations/' + source['id'] + '/collaboration-proposals'
    with endpoint(output, delay=0.5) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            _, queued = request(port, 'POST', path, payload)
            until(lambda: len(calls) == 1)
            store.save_agent({'enabled': False}, payload['agent_id'])
            result_path = '/api/workbench/collaboration-proposals/' + queued['id']
            until(lambda: request(port, 'GET', result_path)[1]['state'] == 'failed')
            assert request(port, 'GET', result_path)[1]['proposal'] is None
            assert len(store.messages(source['id'])) == 2 and len(calls) == 1
            assert request(port, 'POST', path, payload)[1]['id'] == queued['id']
