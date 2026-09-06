"""Codex 0.153.3 event metadata: bounded partial observation, never raw tool content."""
import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_executions import setup, request
from workbench.tool_activities import ToolActivities, parse_tools, MAX_EVENTS, MAX_OUTPUT_BYTES


def event(kind='command_execution', phase='completed', **changes):
    item = {'id': 'item-secret-id', 'type': kind, 'status': 'completed'}
    item.update({
        'command_execution': {'command': 'SECRET command', 'aggregated_output': 'SECRET output', 'exit_code': 7},
        'file_change': {'changes': [{'path': 'SECRET/path', 'kind': 'update'}]},
        'mcp_tool_call': {'server': 'SECRET server', 'tool': 'SECRET tool', 'arguments': {'key': 'SECRET argument'}, 'result': {'content': ['SECRET response']}, 'error': None},
        'collab_tool_call': {'tool': 'spawn_agent', 'sender_thread_id': 'SECRET sender', 'receiver_thread_ids': ['SECRET receiver'], 'prompt': 'SECRET prompt', 'agents_states': {}},
        'web_search': {'query': 'SECRET query', 'action': {'type': 'search', 'query': 'SECRET action'}},
    }[kind])
    if kind == 'web_search': item.pop('status')
    item.update(changes)
    return {'type': 'item.' + phase, 'item': item}


def process(events=None, reason='exited'):
    return {'exit_code': 0, 'reason': reason, 'stdout': b'\n'.join(json.dumps(e).encode() for e in (events if events is not None else [event()])), 'stderr': b'SECRET stderr'}


def fixture(tmp_path):
    store, tasks, executions, task = setup(tmp_path)
    run = executions.create(task['id'], request())
    return store, executions, run, ToolActivities(store)


def test_five_real_tag_types_redact_all_free_text():
    kinds = ['command_execution', 'file_change', 'mcp_tool_call', 'collab_tool_call', 'web_search']
    result = parse_tools(process([event(kind) for kind in kinds]))
    assert [row['type'] for row in result['events']] == kinds
    assert [row['sequence'] for row in result['events']] == [1, 2, 3, 4, 5]
    assert result['events'][0]['exit_code'] == 7
    assert result['events'][-1]['status'] is None
    assert result['invalid_lines'] == result['unknown_items'] == result['dropped_events'] == 0
    serialized = json.dumps(result)
    for secret in ('SECRET', 'item-secret-id', 'spawn_agent'):
        assert secret not in serialized
    command = result['events'][0]['details']['command']
    assert command == {'chars': len('SECRET command'), 'sha256': hashlib.sha256(b'SECRET command').hexdigest()}
    assert result['events'][0]['item_sha256'] == hashlib.sha256(b'item-secret-id').hexdigest()


def test_lifecycle_order_not_falsely_unique_tool_calls():
    result = parse_tools(process([event(phase='started', status='in_progress', exit_code=None),
                                  event(phase='updated', status='in_progress', exit_code=None), event()]))
    assert [e['phase'] for e in result['events']] == ['started', 'updated', 'completed']
    assert len({e['item_sha256'] for e in result['events']}) == 1
    assert 'complete' not in result and 'tool_call_count' not in result


@pytest.mark.parametrize('reason', ['cancelled', 'timeout', 'output_limit', 'unknown', 'start_failed'])
def test_failed_or_unknown_process_retains_observed_tools(reason):
    data = process(reason=reason); data['exit_code'] = None
    result = parse_tools(data)
    assert len(result['events']) == 1 and result['process_reason'] == reason
    assert result['output_limited'] is (reason == 'output_limit')


@pytest.mark.parametrize('bad', [b'\xff', b'{unfinished', b'[]', b'null', b'42', b'{"type":null}',
    b'{"type":"item.completed","item":null}', b'{"type":"item.completed","item":[]}',
    b'{"type":"item.completed","type":"item.started"}', b'{"type":"item.completed","item":{"type":"command_execution","id":"x","status":[]}}',
    b'[' * 2000 + b']' * 2000, b'{"type":"bad","x":NaN}'])
def test_invalid_lines_do_not_destroy_other_observations(bad):
    valid = process()['stdout']
    result = parse_tools({'stdout': valid + b'\n' + bad + b'\n' + valid, 'reason': 'exited'})
    assert [row['sequence'] for row in result['events']] == [1, 3]
    assert result['invalid_lines'] == 1


def test_unknown_items_non_tools_and_limits_are_explicit():
    rows = [event() for _ in range(MAX_EVENTS + 2)]
    rows += [{'type': 'item.completed', 'item': {'type': 'future_tool'}}, {'type': 'future.event'},
             {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'SECRET reasoning'}}, {'type': 'turn.completed'}]
    result = parse_tools(process(rows))
    assert len(result['events']) == MAX_EVENTS and result['dropped_events'] == 2
    assert result['unknown_items'] == 2 and result['invalid_lines'] == 0
    limited = parse_tools({'stdout': process()['stdout'] + b'\n' + b'x' * MAX_OUTPUT_BYTES, 'reason': 'exited'})
    assert limited['output_limited'] and len(limited['events']) == 1 and limited['invalid_lines'] == 1


@pytest.mark.parametrize('status', ['in_progress', 'completed', 'failed', 'declined'])
def test_command_statuses_include_declined(status):
    assert parse_tools(process([event(status=status)]))['events'][0]['status'] == status


def test_no_stdout_and_bad_shape_are_not_zero_tool_proof():
    for data in (None, [], {}, {'stdout': 'text'}, {'stdout': b'', 'reason': 'SECRET reason'}):
        result = parse_tools(data)
        assert result['events'] == [] and result['process_reason'] == 'unknown'
        assert 'complete' not in result


def test_store_binding_readonly_reopen_and_immutable(tmp_path):
    store, executions, run, service = fixture(tmp_path)
    with store.connect() as db: before = list(db.iterdump())
    assert service.get(run['id']) is None
    with store.connect() as db: assert list(db.iterdump()) == before
    payload = parse_tools(process())
    with pytest.raises(ValueError): service.record(run['id'], run['attempt'], run['requirement_version'], payload)
    executions.claim(run['id'])
    receipt = service.record(run['id'], run['attempt'], run['requirement_version'], payload)
    assert receipt['agent_id'] == run['agent_id'] and receipt['payload'] == payload
    executions.recover()
    assert ToolActivities(store).get(run['id']) == receipt
    assert service.record(run['id'], run['attempt'], run['requirement_version'], payload) == receipt
    with pytest.raises(ValueError): service.record(run['id'], run['attempt'] + 1, run['requirement_version'], payload)
    changed = copy.deepcopy(payload); changed['invalid_lines'] += 1
    with pytest.raises(ValueError, match='冲突'): service.record(run['id'], run['attempt'], run['requirement_version'], changed)
    for sql in ('UPDATE tool_activities SET payload=payload', 'DELETE FROM tool_activities', 'INSERT OR REPLACE INTO tool_activities SELECT * FROM tool_activities'):
        with store.connect() as db, pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_stopping_record_and_atomic_concurrent_write(tmp_path):
    store, executions, run, service = fixture(tmp_path)
    executions.claim(run['id']); executions.cancel(run['id'])
    payload = parse_tools(process(reason='cancelled'))
    with store.connect() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON tool_activities BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError): service.record(run['id'], run['attempt'], run['requirement_version'], payload)
    assert service.get(run['id']) is None
    with store.connect() as db: db.execute('DROP TRIGGER injected')
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: service.record(run['id'], run['attempt'], run['requirement_version'], payload), range(8)))
    assert all(row == rows[0] for row in rows)


@pytest.mark.parametrize('change', ['raw', 'bad_hash', 'bad_status', 'bad_sequence', 'bad_exit', 'negative', 'version', 'many', 'unknown_detail'])
def test_store_rejects_invalid_or_raw_metadata(tmp_path, change):
    _, executions, run, service = fixture(tmp_path)
    executions.claim(run['id']); payload = parse_tools(process())
    if change == 'raw': payload['events'][0]['command'] = 'SECRET'
    elif change == 'bad_hash': payload['events'][0]['item_sha256'] = 'SECRET'
    elif change == 'bad_status': payload['events'][0]['status'] = []
    elif change == 'bad_sequence': payload['events'][0]['sequence'] = True
    elif change == 'bad_exit': payload['events'][0]['exit_code'] = True
    elif change == 'negative': payload['dropped_events'] = -1
    elif change == 'version': payload['version'] = True
    elif change == 'many': payload['events'] *= 501
    else: payload['events'][0]['details']['SECRET'] = {'chars': 1, 'sha256': 'a' * 64}
    with pytest.raises(ValueError): service.record(run['id'], run['attempt'], run['requirement_version'], payload)
    assert service.get(run['id']) is None
