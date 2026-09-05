"""Dependency gates use current approved inputs, never historical success alone."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from test_workbench_executions import setup, request, revise
from test_workbench_cli_controller import ready, until, result
from test_workbench_api import running, request as http
from workbench import artifacts, cli_controller
from workbench.cli_controller import CLIController
from workbench.reviews import Reviews
from workbench.store import Store
from workbench.tasks import Tasks, TaskVersionConflict


def sibling(tasks, task, name):
    return tasks.create(task['conversation_id'], {
        **{key: task[key] for key in ('agent_id', 'source_message_id', 'title', 'scope', 'acceptance')},
        'title': name, 'request_id': name})


def link(tasks, task, predecessors):
    return tasks.set_dependencies(task['id'], {'expected_version': tasks.get(task['id'])['requirement_version'],
        'task_ids': [item['id'] for item in predecessors]})


def execute(store, executions, task, decision='approved'):
    previous = executions.list(task['id'])
    run = executions.create(task['id'], request(version=task['requirement_version'],
        request_id=f"run-{len(previous)}", previous=previous[0]['id'] if previous else None,
        note='Checked previous effects' if previous else ''))
    assert executions.claim(run['id'])
    run = executions.report(run['id'], run['attempt'], run['requirement_version'], 0,
        'Explicit test output, no model call', success=True, artifacts=[{'path': 'report.txt', 'data': b'output'}])
    payload = {'request_id': 'decision', 'expected_version': task['requirement_version'],
        'decision': decision, 'note': 'Checked exact artifact snapshot',
        'artifact_ids': [item['id'] for item in artifacts.list_for(store, run['id'])]}
    if decision:
        Reviews(store).save(run['id'], payload)
    return run, payload


def test_versioned_dag_validation_and_revision_preserves_dependencies(tmp_path):
    store, tasks, _, first = setup(tmp_path)
    second, third = sibling(tasks, first, 'second'), sibling(tasks, first, 'third')
    old = tasks.get(third['id'])
    changed = link(tasks, third, [second, first])
    assert tasks.get(third['id'])['requirement_version'] == old['requirement_version'] + 1
    status = tasks.dependencies(third['id'])
    assert status['task_ids'] == sorted([first['id'], second['id']])
    assert not status['ready'] and status['blocked_reason']
    assert link(tasks, third, [first, second]) == changed
    modified = revise(tasks, tasks.get(third['id']))
    assert tasks.dependencies(third['id'])['task_ids'] == status['task_ids']
    assert Tasks(Store(tmp_path)).dependencies(third['id']) == tasks.dependencies(third['id'])
    for predecessor in (first, second):
        with pytest.raises(ValueError):
            link(tasks, predecessor, [third])
    with pytest.raises(TaskVersionConflict):
        tasks.set_dependencies(third['id'], {'expected_version': old['requirement_version'], 'task_ids': []})
    link(tasks, third, [])
    assert tasks.dependencies(third['id'])['ready']
    assert tasks.dependencies(third['id'])['task_ids'] == []
    assert tasks.get(third['id'])['requirement_version'] == modified['requirement_version'] + 1
    assert [row['dependency_task_ids'] for row in tasks.history(third['id'])] == [
        [], status['task_ids'], status['task_ids'], []]


def test_validation_same_conversation_self_duplicate_and_payload(tmp_path):
    store, tasks, _, first = setup(tmp_path)
    room = store.save_conversation({'type': 'board', 'title': 'Other', 'member_ids': [first['agent_id']]})
    source = store.send_message(room['id'], {'content': 'Other source', 'request_id': 'other'})
    foreign = tasks.create(room['id'], {**{key: first[key] for key in ('agent_id', 'title', 'scope', 'acceptance')},
        'source_message_id': source['id'], 'request_id': 'foreign'})
    second = sibling(tasks, first, 'second')
    base = {'expected_version': 1, 'task_ids': []}
    invalids = [None, {}, {**base, 'actor': 'agent'}, {**base, 'expected_version': True},
        {**base, 'expected_version': 0}, {**base, 'task_ids': None}, {**base, 'task_ids': 'id'},
        {**base, 'task_ids': [first['id']]}, {**base, 'task_ids': [foreign['id']]},
        {**base, 'task_ids': [second['id'], second['id']]}, {**base, 'task_ids': ['missing']},
        {**base, 'task_ids': [None]}, {**base, 'task_ids': [' ']},
        {**base, 'task_ids': [str(i) for i in range(33)]}]
    for payload in invalids:
        with pytest.raises((ValueError, KeyError)):
            tasks.set_dependencies(first['id'], payload)
    assert tasks.get(first['id'])['requirement_version'] == 1
    assert tasks.dependencies(first['id'])['task_ids'] == []


def test_concurrent_opposing_edges_cannot_create_cycle(tmp_path):
    _, tasks, _, first = setup(tmp_path)
    second = sibling(tasks, first, 'second')
    def edit(pair):
        try:
            return link(tasks, *pair)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, [(first, [second]), (second, [first])]))
    assert sum(outcome is not None for outcome in outcomes) == 1


@pytest.mark.parametrize('decision', [None, 'rejected', 'approved'])
def test_claim_waits_for_current_latest_approved_predecessor(tmp_path, decision):
    store, tasks, executions, first = setup(tmp_path)
    second = sibling(tasks, first, 'second')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    run = executions.create(second['id'], request(version=second['requirement_version']))
    assert not executions.claim(run['id'])
    assert executions.get(run['id'])['state'] == 'queued'
    execute(store, executions, first, decision)
    assert executions.claim(run['id']) is (decision == 'approved')
    assert executions.get(run['id'])['state'] == ('running' if decision == 'approved' else 'queued')


@pytest.mark.parametrize('phase', ['queued', 'running', 'review'])
@pytest.mark.parametrize('change', ['version', 'attempt'])
def test_upstream_changes_block_downstream_result(tmp_path, phase, change):
    store, tasks, executions, first = setup(tmp_path)
    upstream, _ = execute(store, executions, first)
    second = sibling(tasks, first, 'second')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    run = executions.create(second['id'], request(version=second['requirement_version']))
    if phase != 'queued':
        assert executions.claim(run['id'])
        assert executions.snapshot(run['id'])['dependency_inputs']
    if phase == 'review':
        run = executions.report(run['id'], 1, second['requirement_version'], 0, 'Result', success=True,
            artifacts=[{'path': 'result', 'data': b'valid'}])
    if change == 'version':
        revise(tasks, first)
    else:
        executions.create(first['id'], request(request_id='newer', previous=upstream['id'], note='Checked previous effects'))
    assert not tasks.dependencies(second['id'])['ready']
    if phase == 'queued':
        assert not executions.claim(run['id'])
        assert executions.get(run['id'])['state'] == 'queued'
    elif phase == 'running':
        with pytest.raises(ValueError):
            executions.snapshot(run['id'])
        assert executions.report(run['id'], 1, second['requirement_version'], 0, 'Stale result', success=True)['state'] != 'awaiting_review'
    else:
        with pytest.raises(ValueError):
            Reviews(store).save(run['id'], {'request_id': 'owner', 'expected_version': second['requirement_version'],
                'decision': 'approved', 'note': 'Attempt approval',
                'artifact_ids': [item['id'] for item in artifacts.list_for(store, run['id'])]})
        assert Reviews(store).get(run['id']) is None


def test_transitive_changed_input_and_replacement_approval_do_not_revalidate_old_output(tmp_path):
    store, tasks, executions, first = setup(tmp_path)
    execute(store, executions, first)
    second = sibling(tasks, first, 'second')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    execute(store, executions, second)
    third = sibling(tasks, first, 'third')
    link(tasks, third, [second])
    third = tasks.get(third['id'])
    assert tasks.dependencies(third['id'])['ready']
    # A new fully approved attempt of A cannot retroactively replace B's frozen input.
    execute(store, executions, first)
    assert not tasks.dependencies(third['id'])['ready']
    run = executions.create(third['id'], request(version=third['requirement_version']))
    assert not executions.claim(run['id'])
    execute(store, executions, second)
    assert tasks.dependencies(third['id'])['ready']
    assert executions.claim(run['id'])


def test_dependency_edit_supersedes_queued_execution(tmp_path):
    _, tasks, executions, first = setup(tmp_path)
    second = sibling(tasks, first, 'second')
    run = executions.create(second['id'], request())
    link(tasks, second, [first])
    assert not executions.claim(run['id'])
    assert executions.get(run['id'])['state'] == 'superseded'


def test_waiting_queue_does_not_starve_independent_task(tmp_path, monkeypatch):
    store, tasks, executions, first = setup(tmp_path)
    second, third = sibling(tasks, first, 'second'), sibling(tasks, first, 'third')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    waiting = executions.create(second['id'], request(version=second['requirement_version']))
    independent = executions.create(third['id'], request())
    controller = CLIController(store)
    ready(monkeypatch, controller, concurrency=1)
    calls, release = [], Event()
    def runner(**kwargs):
        calls.append(kwargs['execution_id'])
        assert release.wait(4)
        return result()
    monkeypatch.setattr(cli_controller, 'run_codex', runner)
    try:
        until(controller, lambda: bool(calls))
        assert calls == [independent['id']]
        assert executions.get(waiting['id'])['state'] == 'queued'
    finally:
        release.set()
        controller.close()


def test_http_dependencies_persist_and_conflict(tmp_path):
    _, tasks, _, first = setup(tmp_path)
    second = sibling(tasks, first, 'second')
    path = f"/api/workbench/tasks/{second['id']}/dependencies"
    with running(tmp_path) as port:
        assert http(port, 'GET', path)[1]['task_ids'] == []
        status, _ = http(port, 'PATCH', path, {'expected_version': 1, 'task_ids': [first['id']]})
        assert status == 200
        assert http(port, 'PATCH', path, {'expected_version': 1, 'task_ids': []})[0] == 409
        assert http(port, 'GET', path)[1]['task_ids'] == [first['id']]
    with running(tmp_path) as port:
        assert http(port, 'GET', path)[1]['task_ids'] == [first['id']]


def test_graph_bound_accepts_1000_nodes_and_rejects_1001(tmp_path):
    store, tasks, _, first = setup(tmp_path)
    # Construct a persisted long chain directly to isolate the graph boundary from API runtime.
    with store.connect() as db:
        for index in range(1, 1001):
            identity = f'bounded-node-{index}'
            db.execute('''INSERT INTO tasks(id,conversation_id,source_message_id,request_id,creation_payload,requirement_version)
                VALUES(?,?,?,?,?,1)''', (identity, first['conversation_id'], first['source_message_id'], identity, '{}'))
            db.execute('''INSERT INTO task_revisions(task_id,requirement_version,title,scope,acceptance,agent_id)
                VALUES(?,1,?,?,?,?)''', (identity, 'Bound', 'Bound', 'Bound', first['agent_id']))
            if index < 1000:
                dependency = first['id'] if index == 1 else f'bounded-node-{index - 1}'
                db.execute('INSERT INTO task_dependencies VALUES(?,1,?)', (identity, dependency))
    # First plus nodes 1..998 plus node 1000 gives exactly 1000 reachable nodes.
    accepted = tasks.set_dependencies('bounded-node-1000', {'expected_version': 1, 'task_ids': ['bounded-node-998']})
    assert accepted['requirement_version'] == 2
    with pytest.raises(ValueError, match='1000'):
        tasks.set_dependencies('bounded-node-1000', {'expected_version': 2, 'task_ids': ['bounded-node-999']})
    assert tasks.dependencies('bounded-node-1000')['task_ids'] == ['bounded-node-998']


@pytest.mark.parametrize('change', ['disabled', 'removed', 'archived'])
def test_dependency_mutation_rechecks_owner_conversation_authority(tmp_path, change):
    store, tasks, _, first = setup(tmp_path)
    second = sibling(tasks, first, 'second')
    if change == 'disabled':
        store.save_agent({'enabled': False}, first['agent_id'])
    elif change == 'removed':
        store.set_member(first['conversation_id'], first['agent_id'], False)
    else:
        store.save_conversation({'archived': True}, first['conversation_id'])
    with pytest.raises((ValueError, PermissionError)):
        link(tasks, second, [first])
    assert tasks.get(second['id'])['requirement_version'] == 1


def test_versioned_dependencies_and_claim_inputs_cannot_be_replaced(tmp_path):
    import sqlite3
    store, tasks, executions, first = setup(tmp_path)
    upstream, _ = execute(store, executions, first)
    second = sibling(tasks, first, 'second')
    link(tasks, second, [first])
    second = tasks.get(second['id'])
    run = executions.create(second['id'], request(version=second['requirement_version']))
    assert executions.claim(run['id'])
    with store.connect() as db:
        for table in ('task_dependencies', 'execution_inputs'):
            row = tuple(db.execute(f'SELECT * FROM {table}').fetchone())
            for query, parameters in (
                (f'DELETE FROM {table}', ()),
                (f'UPDATE {table} SET dependency_task_id=dependency_task_id', ()),
                (f'INSERT OR REPLACE INTO {table} VALUES(?,?,?)', row)):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(query, parameters)
    assert executions.snapshot(run['id'])['dependency_inputs'] == [
        {'dependency_task_id': first['id'], 'upstream_execution_id': upstream['id']}]



def test_exact_32_direct_dependencies_allowed_but_33_rejected(tmp_path):
    _, tasks, _, first = setup(tmp_path)
    predecessors = [sibling(tasks, first, f'predecessor-{index}') for index in range(33)]
    status = link(tasks, first, predecessors[:32])
    assert len(status['task_ids']) == 32 and status['requirement_version'] == 2
    with pytest.raises(ValueError):
        link(tasks, first, predecessors)
    assert tasks.dependencies(first['id']) == status
