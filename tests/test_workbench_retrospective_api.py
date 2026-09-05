"""Owner HTTP → real local provider subprocess → candidate, never automatic approval."""
import json

from test_workbench_api import running, request
from test_workbench_reviews import ready
from test_workbench_provider import endpoint
from test_workbench_controller import configure, until
from workbench.settings import Settings
from workbench.memories import Memories
from workbench import artifacts


def source(tmp_path):
    store, _, _, task, execution, reviews, approval = ready(tmp_path)
    reviews.save(execution['id'], approval)
    payload = dict(request_id='reflection', expected_version=0,
                   source_execution_id=execution['id'], artifact_ids=approval['artifact_ids'])
    path = '/api/workbench/memories/agent/' + task['agent_id']
    return store, task, payload, path


def test_owner_http_retrospective_approval_and_restart(tmp_path, monkeypatch):
    store, task, payload, path = source(tmp_path)
    store.send_message(task['conversation_id'], dict(content='UNRELATED PRIVATE HISTORY', request_id='private'))
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
        'content': '经验：保留验收证据。适用范围：当前工具链；不推断未执行结果。',
        'evidence_artifact_ids': payload['artifact_ids']})}}]}
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            assert request(port, 'POST', path + '/retrospectives', payload,
                           headers={'Authorization': ''})[0] == 401
            status, run = request(port, 'POST', path + '/retrospectives', payload)
            assert status == 202
            result_path = '/api/workbench/retrospectives/' + run['id']
            get = lambda: request(port, 'GET', result_path)[1]
            until(lambda: get()['state'] == 'completed')
            completed = get()
            assert completed['candidate_id']
            assert request(port, 'POST', path + '/retrospectives', payload)[1]['id'] == run['id']
            assert len(calls) == 1
            assert 'UNRELATED PRIVATE HISTORY' not in json.dumps(calls, ensure_ascii=False)
            assert 'verified output' in json.dumps(calls, ensure_ascii=False)
            assert request(port, 'GET', path)[1]['version'] == 0
            candidate_path = '/api/workbench/memory-candidates/' + completed['candidate_id']
            candidate = request(port, 'GET', candidate_path)[1]
            assert candidate['source_execution_id'] == payload['source_execution_id']
            assert candidate['decision'] is None
            assert request(port, 'GET', '/api/workbench/conversations/' + task['conversation_id'] + '/runs')[1] == []
            assert len(store.messages(task['conversation_id'])) == 2
            decision = dict(request_id='approve-reflection', decision='approved', note='Owner checked evidence and replacement')
            assert request(port, 'POST', candidate_path + '/decision', decision)[1]['result_version'] == 1
    with running(tmp_path) as port:
        assert request(port, 'GET', result_path)[1] == completed
        assert request(port, 'GET', path + '/retrospectives')[1][0]['id'] == run['id']
        assert request(port, 'POST', candidate_path + '/decision', decision)[1]['result_version'] == 1
        rollback = dict(request_id='restore', expected_version=1, target_version=0, note='Restore original memory')
        assert request(port, 'POST', path + '/rollback', rollback)[1]['version'] == 2
        assert request(port, 'GET', path)[1]['content'] == ''


def test_cancelled_retrospective_has_no_model_or_candidate(tmp_path):
    store, _, payload, path = source(tmp_path)
    with running(tmp_path) as port:
        assert request(port, 'POST', path + '/retrospectives', {**payload, 'artifact_ids': []})[0] == 400
        status, run = request(port, 'POST', path + '/retrospectives', payload)
        assert status == 202 and run['state'] == 'queued'
        assert request(port, 'POST', '/api/workbench/runs/' + run['id'] + '/cancel', {})[1]['state'] == 'cancelled'
        assert request(port, 'POST', path + '/retrospectives', payload)[1]['state'] == 'cancelled'
        assert request(port, 'GET', path + '/candidates')[1] == []
        assert request(port, 'GET', path)[1]['version'] == 0


def test_invalid_model_output_fails_without_candidate_or_retry(tmp_path, monkeypatch):
    store, task, payload, path = source(tmp_path)
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"content":"unverified"}'}}]}
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            status, run = request(port, 'POST', path + '/retrospectives', payload)
            assert status == 202
            get = lambda: request(port, 'GET', '/api/workbench/retrospectives/' + run['id'])[1]
            until(lambda: get()['state'] == 'failed')
            assert get()['candidate_id'] is None
            assert request(port, 'POST', path + '/retrospectives', payload)[1]['state'] == 'failed'
            assert len(calls) == 1
            assert request(port, 'GET', path + '/candidates')[1] == []
            assert request(port, 'GET', path)[1]['version'] == 0
            assert len(store.messages(task['conversation_id'])) == 1


def test_project_retrospective_excludes_private_memory_and_unselected_artifact(tmp_path, monkeypatch):
    store, _, _, task, execution, reviews, approval = ready(tmp_path, [
        {'path': 'selected.txt', 'data': b'SELECTED EVIDENCE'},
        {'path': 'omitted.txt', 'data': b'UNSELECTED CONTENT'}])
    reviews.save(execution['id'], approval)
    memory = Memories(store)
    candidate = memory.propose('agent', task['agent_id'], dict(request_id='private-memory',
        expected_version=0, source_execution_id=execution['id'], content='PRIVATE AGENT KNOWLEDGE'))
    memory.decide(candidate['id'], dict(request_id='approve-private', decision='approved', note='Private scope only'))
    selected = next(a['id'] for a in artifacts.list_for(store, execution['id']) if a['path'] == 'selected.txt')
    payload = dict(request_id='project-retro', expected_version=0,
                   source_execution_id=execution['id'], artifact_ids=[selected])
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
        'content': 'Scoped project learning from selected evidence', 'evidence_artifact_ids': [selected]})}}]}
    path = '/api/workbench/memories/project/' + task['conversation_id']
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            status, run = request(port, 'POST', path + '/retrospectives', payload)
            assert status == 202
            get = lambda: request(port, 'GET', '/api/workbench/retrospectives/' + run['id'])[1]
            until(lambda: get()['state'] == 'completed')
            assert len(calls) == 1
            transmitted = json.dumps(calls, ensure_ascii=False)
            assert 'SELECTED EVIDENCE' in transmitted
            assert 'UNSELECTED CONTENT' not in transmitted and 'PRIVATE AGENT KNOWLEDGE' not in transmitted
            assert 'SELECTED EVIDENCE' not in json.dumps(get(), ensure_ascii=False)
            assert request(port, 'GET', path)[1]['version'] == 0
