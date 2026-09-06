"""Approved direct inputs cross the controller/CLI boundary as isolated copies."""
import json
from pathlib import Path
import sys
from threading import Event

import pytest

from test_workbench_dependencies import sibling, link, execute
from test_workbench_executions import setup, request, revise
from workbench import artifacts, cli_controller, process_tree
from workbench.cli import InputPreparationError
from workbench.cli_controller import CLIController


def prepared(tmp_path):
    store, tasks, executions, first = setup(tmp_path)
    upstream, _ = execute(store, executions, first)
    second = sibling(tasks, first, 'consumer')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    controller = CLIController(store)
    run = executions.create(second['id'], request(version=second['requirement_version']))
    assert executions.claim(run['id'])
    config = {'executable': Path(sys.executable), 'model': 'qa-model', 'api_key': 'qa-placeholder', 'timeout_seconds': 10}
    return store, tasks, executions, first, upstream, second, run, controller, config


def test_controller_materializes_exact_snapshot_and_publishes_only_new_outputs(tmp_path, monkeypatch):
    store, tasks, executions, first, upstream, _, run, controller, config = prepared(tmp_path)
    unrelated = sibling(tasks, first, 'unrelated')
    execute(store, executions, unrelated)
    selected = artifacts.list_for(store, upstream['id'])[0]
    calls = []
    def process(argv, cwd, env, stdin, timeout, cancel):
        calls.append(argv)
        assert (cwd / 'inputs' / selected['id']).read_bytes() == b'output'
        assert [p.name for p in (cwd / 'inputs').iterdir()] == [selected['id']]
        payload = json.loads(stdin.decode().split('\n', 1)[1])
        assert payload['input_artifacts'][0]['workspace_path'] == 'inputs/' + selected['id']
        assert payload['input_artifacts'][0]['execution_id'] == upstream['id']
        assert 'data' not in payload['input_artifacts'][0]
        # Inputs are disposable copies; changing one never changes the authoritative artifact.
        (cwd / 'inputs' / selected['id']).write_bytes(b'locally edited')
        (cwd / 'artifacts').mkdir()
        (cwd / 'artifacts' / 'new-result.txt').write_bytes(b'consumer output')
        return {'exit_code': 0, 'reason': 'exited', 'stdout': (
            b'{"type":"item.completed","item":{"type":"agent_message","text":"QA complete"}}\n'
            b'{"type":"turn.completed"}\n')}
    monkeypatch.setattr(process_tree, 'run_process', process)
    try:
        result = controller._execute(run, config, Event())
        assert result['success'] and len(calls) == 1
        assert result['artifacts'] == [{'path': 'new-result.txt', 'data': b'consumer output'}]
        assert artifacts.get(store, selected['id'])['data'] == b'output'
        # Observation persistence belongs to the controller, not the execution callback.
        observed = result.pop('tool_activities')
        assert controller.tool_activities.get(run['id'])['payload'] == observed
        saved = executions.report(run['id'], run['attempt'], run['requirement_version'], **result)
        assert saved['state'] == 'awaiting_review'
        assert [a['path'] for a in artifacts.list_for(store, run['id'])] == ['new-result.txt']
    finally:
        controller.close()


@pytest.mark.parametrize('corruption', ['bytes', 'extra', 'empty_review', 'total'])
def test_invalid_approved_inputs_never_launch(tmp_path, monkeypatch, corruption):
    store, _, executions, _, upstream, _, run, controller, config = prepared(tmp_path)
    if corruption == 'bytes':
        with store.connect() as db:
            db.execute('DROP TRIGGER artifacts_no_update')
            db.execute("UPDATE execution_artifacts SET content=? WHERE execution_id=?", (b'broken', upstream['id']))
    elif corruption == 'extra':
        with store.connect() as db:
            artifacts.persist(db, upstream['id'], [{'path': 'unapproved.txt', 'data': b'not approved'}])
    elif corruption == 'empty_review':
        with store.connect() as db:
            db.execute('DROP TRIGGER reviews_no_update')
            db.execute("UPDATE execution_reviews SET artifact_ids='[]' WHERE execution_id=?", (upstream['id'],))
    else:
        monkeypatch.setattr(artifacts, 'MAX_TOTAL_BYTES', 1)
    def forbidden(**kwargs):
        pytest.fail('Invalid input must fail before any CLI invocation')
    monkeypatch.setattr(cli_controller, 'run_codex', forbidden)
    try:
        result = controller._execute(run, config, Event())
        assert result['not_started'] and result['exit_code'] is None
        saved = executions.report(run['id'], run['attempt'], run['requirement_version'], **result)
        assert saved['state'] == 'failed'
        assert artifacts.list_for(store, run['id']) == []
    finally:
        controller.close()


def test_only_direct_inputs_not_transitive_or_private_history(tmp_path):
    store, tasks, executions, first = setup(tmp_path)
    first_run, _ = execute(store, executions, first)
    second = sibling(tasks, first, 'middle')
    link(tasks, second, [first]); second = tasks.get(second['id'])
    second_run, _ = execute(store, executions, second)
    third = sibling(tasks, first, 'consumer')
    link(tasks, third, [second]); third = tasks.get(third['id'])
    run = executions.create(third['id'], request(version=third['requirement_version']))
    assert executions.claim(run['id'])
    # Lightweight polling does not repeatedly load all file bytes.
    assert 'input_artifacts' not in executions.snapshot(run['id'])
    snapshot = executions.snapshot(run['id'], include_artifacts=True)
    assert {a['execution_id'] for a in snapshot['input_artifacts']} == {second_run['id']}
    assert all(a['execution_id'] != first_run['id'] for a in snapshot['input_artifacts'])
    assert set(snapshot) == {'task', 'agent', 'instructions', 'source_message', 'dependency_inputs', 'input_artifacts', 'memories', 'skills'}
    assert snapshot['skills'] == []  # This identity did not authorize any Skill input.


def test_upstream_change_before_input_read_prevents_launch(tmp_path, monkeypatch):
    _, tasks, _, first, _, _, run, controller, config = prepared(tmp_path)
    revise(tasks, first)
    def forbidden(**kwargs):
        pytest.fail('Old approval cannot authorize input transfer')
    monkeypatch.setattr(cli_controller, 'run_codex', forbidden)
    try:
        assert controller._execute(run, config, Event())['not_started']
    finally:
        controller.close()


def test_preparation_error_is_known_unstarted_not_unknown_process(tmp_path, monkeypatch):
    _, _, executions, _, _, _, run, controller, config = prepared(tmp_path)
    def fail(**kwargs):
        raise InputPreparationError('QA pre-spawn failure')
    monkeypatch.setattr(cli_controller, 'run_codex', fail)
    try:
        result = controller._execute(run, config, Event())
        assert result['not_started'] and result['exit_code'] is None
        assert executions.report(run['id'], run['attempt'], run['requirement_version'], **result)['state'] == 'failed'
    finally:
        controller.close()
