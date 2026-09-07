"""Bounded, redacted Codex JSONL observations after process return, not live tool control."""
import hashlib
import json
import re

from .store import _text


MAX_EVENTS = 500
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
FIELDS = {
    'opencode_tool': ('tool', 'input', 'output', 'error'),
    'command_execution': ('command', 'aggregated_output'),
    'file_change': ('changes',),
    'mcp_tool_call': ('server', 'tool', 'arguments', 'result', 'error'),
    'collab_tool_call': ('tool', 'sender_thread_id', 'receiver_thread_ids', 'prompt', 'agents_states'),
    'web_search': ('query', 'action'),
}
STATUSES = {'in_progress', 'completed', 'failed'}
REASONS = {'exited', 'cancelled', 'timeout', 'output_limit', 'start_failed', 'unknown', 'protocol_error'}
PHASES = {'item.started': 'started', 'item.updated': 'updated', 'item.completed': 'completed'}
NON_TOOLS = {'agent_message', 'reasoning', 'todo_list', 'error'}
NON_ITEMS = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'}


def _digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {'chars': len(text), 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def _pairs(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError('Duplicate JSON key')
        row[key] = value
    return row


def _constant(value):
    raise ValueError('Non-finite JSON number')


def _status(item):
    if item['type'] == 'web_search':
        return None
    status = item.get('status')
    if status not in STATUSES and not (item['type'] == 'command_execution' and status == 'declined'):
        raise ValueError('Unsupported tool status')
    return status


def parse_tools(process):
    """Keep independently valid tool events, including those preceding a broken trailing line."""
    if not isinstance(process, dict):
        process = {}
    raw = process.get('stdout')
    reason = process.get('reason')
    reason = reason if isinstance(reason, str) and reason in REASONS else 'unknown'
    result = {'version': 1, 'source': 'codex_jsonl', 'observation': 'after_process', 'events': [],
              'invalid_lines': 0, 'unknown_items': 0, 'dropped_events': 0,
              'output_limited': reason == 'output_limit', 'process_reason': reason}
    if not isinstance(raw, bytes):
        result['invalid_lines'] = 1
        return result
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES]
        result['output_limited'] = True
    for sequence, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('Invalid event')
            if event['type'] not in PHASES:
                if event['type'] not in NON_ITEMS:
                    result['unknown_items'] += 1
                continue
            item = event.get('item')
            if not isinstance(item, dict) or not isinstance(item.get('type'), str):
                raise ValueError('Invalid item')
            if item['type'] not in FIELDS or item['type'] == 'opencode_tool':
                if item['type'] not in NON_TOOLS:
                    result['unknown_items'] += 1
                continue
            if not isinstance(item.get('id'), str) or not item['id']:
                raise ValueError('Missing tool item identity')
            exit_code = item.get('exit_code') if item['type'] == 'command_execution' else None
            if exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code < 2**31):
                raise ValueError('Invalid tool exit code')
            row = {'sequence': sequence, 'phase': PHASES[event['type']], 'type': item['type'],
                   'item_sha256': _digest(item['id'])['sha256'], 'status': _status(item), 'exit_code': exit_code,
                   'details': {key: _digest(item[key]) for key in FIELDS[item['type']] if key in item}}
            if len(result['events']) == MAX_EVENTS:
                result['dropped_events'] += 1
            else:
                result['events'].append(row)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            result['invalid_lines'] += 1
    return result


def parse_opencode_tools(process):
    """Native OpenCode terminal tool observations; keep only hashes, never tool text."""
    result = parse_tools({**process, 'stdout': b''})
    result['source'] = 'opencode_jsonl'
    raw = process.get('stdout')
    if not isinstance(raw, bytes):
        result['invalid_lines'] = 1
        return result
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES]
        result['output_limited'] = True
    session = None
    for sequence, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('Invalid event')
            identity = event.get('sessionID')
            if not isinstance(identity, str) or not identity or session is not None and identity != session:
                raise ValueError('Mixed session')
            session = identity
            if event['type'] != 'tool_use':
                if event['type'] not in ('step_start', 'step_finish', 'text', 'reasoning', 'error'):
                    result['unknown_items'] += 1
                continue
            part = event.get('part')
            if (not isinstance(part, dict) or part.get('type') != 'tool' or part.get('sessionID') != session
                    or not isinstance(part.get('id'), str) or not part['id']
                    or not isinstance(part.get('messageID'), str) or not part['messageID']
                    or not isinstance(part.get('tool'), str) or not part['tool']):
                raise ValueError('Invalid tool identity')
            state = part.get('state')
            if not isinstance(state, dict) or state.get('status') not in ('completed', 'error'):
                raise ValueError('Invalid terminal tool state')
            values = {'tool': part['tool'], **{key: state[key] for key in ('input', 'output', 'error') if key in state}}
            row = {'sequence': sequence, 'phase': 'completed', 'type': 'opencode_tool',
                   'item_sha256': _digest(part['id'])['sha256'],
                   'status': 'completed' if state['status'] == 'completed' else 'failed', 'exit_code': None,
                   'details': {key: _digest(value) for key, value in values.items()}}
            if len(result['events']) == MAX_EVENTS:
                result['dropped_events'] += 1
            else:
                result['events'].append(row)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            result['invalid_lines'] += 1
    return result


def _integer(value, minimum=0, maximum=9007199254740991):
    return type(value) is int and minimum <= value <= maximum


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _validate(payload):
    if (not isinstance(payload, dict) or set(payload) != {'version', 'source', 'observation', 'events', 'invalid_lines', 'unknown_items', 'dropped_events', 'output_limited', 'process_reason'}
            or type(payload['version']) is not int or payload['version'] != 1 or payload['source'] not in ('codex_jsonl', 'opencode_jsonl')
            or payload['observation'] != 'after_process' or type(payload['output_limited']) is not bool
            or not isinstance(payload['process_reason'], str) or payload['process_reason'] not in REASONS
            or any(not _integer(payload[key]) for key in ('invalid_lines', 'unknown_items', 'dropped_events'))
            or not isinstance(payload['events'], list) or len(payload['events']) > MAX_EVENTS):
        raise ValueError('工具活动摘要格式无效')
    previous = 0
    for row in payload['events']:
        if (not isinstance(row, dict) or set(row) != {'sequence', 'phase', 'type', 'item_sha256', 'status', 'exit_code', 'details'}
                or not _integer(row['sequence'], previous + 1) or row['phase'] not in PHASES.values()
                or not isinstance(row['type'], str) or row['type'] not in FIELDS or not _hash(row['item_sha256'])
                or not isinstance(row['details'], dict) or set(row['details']) - set(FIELDS[row['type']])):
            raise ValueError('工具活动事件格式无效')
        if (payload['source'] == 'opencode_jsonl') != (row['type'] == 'opencode_tool'):
            raise ValueError('工具活动来源与类型不一致')
        if row['type'] == 'opencode_tool' and (row['phase'] != 'completed' or row['status'] not in ('completed', 'failed')):
            raise ValueError('OpenCode 工具活动必须为终态通知')
        if row['type'] == 'web_search':
            valid_status = row['status'] is None
        else:
            valid_status = isinstance(row['status'], str) and (row['status'] in STATUSES or row['type'] == 'command_execution' and row['status'] == 'declined')
        if not valid_status or row['exit_code'] is not None and (row['type'] != 'command_execution' or not _integer(row['exit_code'], -(2**31), 2**31 - 1)):
            raise ValueError('工具活动状态或退出码无效')
        for value in row['details'].values():
            if not isinstance(value, dict) or set(value) != {'chars', 'sha256'} or not _integer(value['chars']) or not _hash(value['sha256']):
                raise ValueError('工具活动内容摘要无效')
        previous = row['sequence']


class ToolActivities:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS tool_activities (
                execution_id TEXT PRIMARY KEY REFERENCES task_executions(id), agent_id TEXT NOT NULL,
                attempt INTEGER NOT NULL, requirement_version INTEGER NOT NULL, payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''')
            for action in ('UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS tool_activities_no_{action.lower()} BEFORE {action}
                    ON tool_activities BEGIN SELECT RAISE(ABORT,'Tool activities are immutable'); END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS tool_activities_no_replace BEFORE INSERT ON tool_activities
                WHEN EXISTS(SELECT 1 FROM tool_activities WHERE execution_id=NEW.execution_id)
                BEGIN SELECT RAISE(ABORT,'Tool activities are immutable'); END''')

    @staticmethod
    def _run(db, identity):
        identity = _text(identity, '执行 ID')
        row = db.execute('SELECT * FROM task_executions WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError('执行不存在')
        return row

    @staticmethod
    def _get(db, identity):
        row = db.execute('SELECT * FROM tool_activities WHERE execution_id=?', (identity,)).fetchone()
        return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

    def get(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            run = self._run(db, identity)
            return self._get(db, run['id'])

    def record(self, identity, attempt, version, payload):
        if not _integer(attempt, 1) or not _integer(version, 1):
            raise ValueError('工具活动实例版本无效')
        _validate(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db, identity)
            if run['attempt'] != attempt or run['requirement_version'] != version:
                raise ValueError('工具活动与实际执行版本不一致')
            previous = self._get(db, run['id'])
            if previous:
                if previous['payload'] != payload:
                    raise ValueError('工具活动摘要与原记录冲突')
                return previous
            if run['state'] not in ('running', 'stopping'):
                raise ValueError('只有运行或停止中的执行可以首次记录工具活动')
            db.execute('INSERT INTO tool_activities(execution_id,agent_id,attempt,requirement_version,payload) VALUES(?,?,?,?,?)',
                       (run['id'], run['agent_id'], attempt, version, encoded))
            return self._get(db, run['id'])
