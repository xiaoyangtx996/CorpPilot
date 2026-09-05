"""Real HTTP persistence and execution-to-CLI approved memory boundaries."""
import json
import sys
from pathlib import Path
from threading import Event

from test_workbench_api import running, request as http
from test_workbench_memories import setup, proposal, decision
from test_workbench_dependencies import sibling
from test_workbench_executions import request
from workbench import cli_controller
from workbench.cli_controller import CLIController


def test_http_memory_lifecycle_replay_and_restart(tmp_path):
    _, _, _, task, run, _ = setup(tmp_path)
    path = '/api/workbench/memories/agent/' + task['agent_id']
    p = proposal(run)
    with running(tmp_path) as port:
        assert http(port, 'GET', path)[1]['version'] == 0
        assert http(port, 'POST', path + '/candidates', p,
                    headers={'Origin': 'https://foreign.example'})[0] == 400
        status, candidate = http(port, 'POST', path + '/candidates', p)
        assert status == 201
        target = '/api/workbench/memory-candidates/' + candidate['id']
        assert http(port, 'GET', target)[1]['decision'] is None
        assert http(port, 'GET', path + '/candidates')[1][0]['id'] == candidate['id']
        assert http(port, 'GET', path)[1]['version'] == 0
        status, approved = http(port, 'POST', target + '/decision', decision())
        assert status == 201 and approved['result_version'] == 1
        assert http(port, 'POST', path + '/candidates', proposal(run, 'stale'))[0] == 409
        rollback = dict(request_id='rollback', expected_version=1, target_version=0, note='Revert')
        assert http(port, 'POST', path + '/rollback', rollback)[1]['version'] == 2
        assert http(port, 'PATCH', path, {'content': 'bypass approval'})[0] == 404
    with running(tmp_path) as port:
        assert http(port, 'GET', path)[1]['content'] == ''
        assert len(http(port, 'GET', path + '/history')[1]) == 2
        assert http(port, 'POST', path + '/candidates', p) == (201, candidate)
        assert http(port, 'POST', target + '/decision', decision()) == (201, approved)
        assert http(port, 'POST', path + '/rollback', rollback)[1]['version'] == 2


def test_claim_freezes_approved_context_through_cli_and_rollback(tmp_path, monkeypatch):
    store, tasks, executions, task, source, memory = setup(tmp_path)
    for scope, identity in [('agent', task['agent_id']), ('project', task['conversation_id'])]:
        candidate = memory.propose(scope, identity, proposal(source, scope, content=scope + ' approved'))
        memory.decide(candidate['id'], decision(scope + '-approved'))
    pending = memory.propose('agent', task['agent_id'], proposal(source, 'pending', 1, 'UNAPPROVED'))
    controller = CLIController(store)
    consumer = sibling(tasks, task, 'consumer')
    run = executions.create(consumer['id'], request())
    assert executions.claim(run['id'])
    before = executions.snapshot(run['id'], include_artifacts=True)['memories']
    assert {item['content'] for item in before} == {'agent approved', 'project approved'}
    assert 'memories' not in executions.snapshot(run['id'])
    memory.decide(pending['id'], decision('new-approved'))
    memory.rollback('project', task['conversation_id'], dict(request_id='rollback',
                    expected_version=1, target_version=0, note='Restore empty'))
    assert executions.snapshot(run['id'], include_artifacts=True)['memories'] == before
    calls = []
    def runner(**kwargs):
        calls.append(json.loads(kwargs['prompt'].split('\n', 1)[1]))
        return dict(exit_code=1, summary='Injected process failure', success=False, reason='exited')
    monkeypatch.setattr(cli_controller, 'run_codex', runner)
    try:
        result = controller._execute(run, dict(executable=Path(sys.executable), model='qa-model',
                                    api_key='qa-placeholder', timeout_seconds=10), Event())
        assert result['exit_code'] == 1 and calls[0]['memories'] == before
        later = sibling(tasks, task, 'later')
        next_run = executions.create(later['id'], request())
        assert executions.claim(next_run['id'])
        after = executions.snapshot(next_run['id'], include_artifacts=True)['memories']
        assert {(item['scope'], item['version'], item['content']) for item in after} == {
            ('agent', 2, 'UNAPPROVED'), ('project', 2, '')}
    finally:
        controller.close()
