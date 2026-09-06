"""Explicit task checkpoint recovery, reusing batches and exact approved input gates."""
import hashlib
import json
import uuid

from . import artifacts, dependencies
from .tasks import Tasks
from .reviews import Reviews
from .reconciliations import Reconciliations
from .store import _text


def encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class Checkpoints:
    def __init__(self, store, project_executions):
        self.store, self.batches = store, project_executions
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS checkpoint_recoveries (
                id TEXT PRIMARY KEY, source_batch_id TEXT NOT NULL REFERENCES project_execution_batches(id),
                request_id TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(source_batch_id,request_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS checkpoint_dependency_pins (
                execution_id TEXT NOT NULL REFERENCES task_executions(id),
                dependency_task_id TEXT NOT NULL REFERENCES tasks(id),
                upstream_execution_id TEXT NOT NULL REFERENCES task_executions(id),
                recovery_id TEXT NOT NULL REFERENCES checkpoint_recoveries(id),
                PRIMARY KEY(execution_id,dependency_task_id))''')
            for table in ('checkpoint_recoveries','checkpoint_dependency_pins'):
                for operation in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Checkpoint records are immutable'); END")
                condition = 'id=NEW.id OR (source_batch_id=NEW.source_batch_id AND request_id=NEW.request_id)' if table == 'checkpoint_recoveries' else 'execution_id=NEW.execution_id AND dependency_task_id=NEW.dependency_task_id'
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'Checkpoint records are immutable'); END")

    def _preview(self, db, source_batch_id):
        source = self.batches._receipt(db,source_batch_id)
        fixed = {item['task_id']:item['execution_id'] for item in source['tasks']}
        pending, nodes, blockers = sorted(fixed), {}, []
        while pending:
            identity = pending.pop()
            if identity in nodes: continue
            if len(nodes) >= 1000:
                blockers.append('依赖闭包超过1000项'); break
            task = Tasks._task(db,identity)
            latest = db.execute('SELECT id FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1',(identity,)).fetchone()
            run_id = fixed.get(identity) or (latest[0] if latest else None)
            run = self.batches.executions._run(db,run_id) if run_id else None
            deps = dependencies.ids(db,identity,task['requirement_version'])
            actor = db.execute('SELECT enabled,tools FROM agents WHERE id=?',(task['agent_id'],)).fetchone()
            room = self.store._conversation(db,task['conversation_id'])
            review = Reviews._review(db,run_id) if run_id else None
            reconciliation = Reconciliations._get(db,run_id) if run_id else None
            entries, artifact_valid = [], True
            if run_id:
                rows = db.execute('SELECT * FROM execution_artifacts WHERE execution_id=? ORDER BY id LIMIT 101',(run_id,)).fetchall()
                if len(rows)>artifacts.MAX_FILES: artifact_valid=False
                for row in rows:
                    entries.append({key:row[key] for key in ('id','path','size','sha256')})
                    try: artifacts.verify_snapshot(row)
                    except ValueError: artifact_valid=False
            node = {'task_id':identity,'requirement_version':task['requirement_version'],'execution_id':run_id,
                    'latest_execution_id':latest[0] if latest else None,'state':run['state'] if run else None,
                    'execution_requirement_version':run['requirement_version'] if run else None,
                    'attempt':run['attempt'] if run else None,'action':'blocked','external':identity not in fixed,
                    'dependencies':deps,'review':review,'reconciliation':reconciliation,'artifacts':entries,
                    'inputs':dependencies.bound_inputs(db,run_id) if run_id else [],
                    'agent_id':task['agent_id'],'agent_enabled':bool(actor['enabled']), 'agent_tools':json.loads(actor['tools']),
                    'conversation_archived':room['archived'],'member_ids':sorted(room['member_ids'])}
            nodes[identity]=node
            pending.extend(deps)
            prefix = f'{identity}: '
            if run is None or run_id != node['latest_execution_id'] or run['requirement_version'] != task['requirement_version']:
                blockers.append(prefix+'原需求版本或最新执行已变化'); continue
            approved = run['state']=='awaiting_review' and review and review['decision']=='approved'
            if approved:
                if not entries or not artifact_valid or review['requirement_version'] != run['requirement_version'] or sorted(review['artifact_ids']) != [e['id'] for e in entries]:
                    blockers.append(prefix+'批准成果缺失或不完整'); continue
                try: dependencies.check_bound(db,run)
                except dependencies.DependencyBlocked as exc:
                    blockers.append(prefix+str(exc)); continue
                node['action']='reuse'
            elif identity in fixed and (run['state'] in ('failed','cancelled')
                    or run['state']=='awaiting_review' and review and review['decision']=='rejected'
                    or run['state']=='unknown' and reconciliation and reconciliation['attempt']==run['attempt']
                    and reconciliation['requirement_version']==run['requirement_version']
                    and reconciliation['process_stopped'] and reconciliation['external_effects_checked']):
                try: self.batches.executions.tasks._authorize(db,task['conversation_id'],task['agent_id'])
                except (ValueError,PermissionError,KeyError) as exc:
                    blockers.append(prefix+str(exc)); continue
                if 'execute' not in node['agent_tools']:
                    blockers.append(prefix+'负责人缺少 execute 权限'); continue
                node['action']='retry'
            else:
                blockers.append(prefix+'存在活动实例、未验收成果或未核查结果')
        for node in nodes.values():
            if node['action']=='retry' and node['inputs']:
                expected = [{'dependency_task_id':parent,'upstream_execution_id':nodes[parent]['execution_id']}
                            for parent in node['dependencies'] if parent in nodes]
                if node['inputs'] != expected:
                    blockers.append(node['task_id']+': 原实例绑定的前置成果已变化')
        if not any(node['action']=='retry' for node in nodes.values()): blockers.append('没有可重新授权的失败节点')
        snapshot = {'source_batch_id':source_batch_id,'collaboration_id':source['collaboration_id'],
                    'project_conversation_id':source['project_conversation_id'],'nodes':[nodes[k] for k in sorted(nodes)]}
        return {'source_batch_id':source_batch_id,'snapshot':snapshot,
                'fingerprint':hashlib.sha256(encoded(snapshot).encode()).hexdigest(),'blockers':sorted(set(blockers))}

    def preview(self, source_batch_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._preview(db,_text(source_batch_id,'源批次 ID'))

    def recover(self, source_batch_id, payload):
        source_batch_id = _text(source_batch_id,'源批次 ID')
        if not isinstance(payload,dict) or set(payload) != {'request_id','checkpoint_fingerprint','reconciliation_note','confirm'} or payload['confirm'] is not True:
            raise ValueError('检查点恢复须明确确认，且仅包含四个授权字段')
        request = _text(payload['request_id'],'请求 ID',120)
        note = _text(payload['reconciliation_note'],'核查说明',2000)
        fingerprint = payload['checkpoint_fingerprint']
        if request != payload['request_id'] or not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):
            raise ValueError('检查点指纹或请求 ID 无效')
        original = encoded(payload)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload,snapshot FROM checkpoint_recoveries WHERE source_batch_id=? AND request_id=?',(source_batch_id,request)).fetchone()
            if previous:
                if previous['payload'] != original: raise ValueError('request_id 已用于不同检查点恢复')
                return json.loads(previous['snapshot'])
            preview = self._preview(db,source_batch_id)
            if preview['fingerprint'] != fingerprint: raise ValueError('检查点已变化，请重新读取并授权')
            if preview['blockers']: raise ValueError('检查点不能恢复：'+'；'.join(preview['blockers']))
            snapshot = preview['snapshot']
            retry = [node for node in snapshot['nodes'] if node['action']=='retry']
            batch = self.batches._create(db,snapshot['collaboration_id'],{'request_id':str(uuid.uuid4()),'tasks':[
                {'task_id':node['task_id'],'expected_version':node['requirement_version'],
                 'previous_execution_id':node['execution_id'],'reconciliation_note':note} for node in retry]})
            identity = str(uuid.uuid4())
            created = db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            receipt = {'id':identity,'source_batch_id':source_batch_id,'request_id':request,'request_payload':payload,
                       'checkpoint_fingerprint':fingerprint,'snapshot':snapshot,'batch_id':batch['id'],'batch':batch,'created_at':created}
            db.execute('INSERT INTO checkpoint_recoveries VALUES(?,?,?,?,?,?)',(identity,source_batch_id,request,original,encoded(receipt),created))
            bindings = {node['task_id']:node['execution_id'] for node in snapshot['nodes']}
            bindings.update({row['task_id']:row['execution_id'] for row in batch['tasks']})
            for node in retry:
                for parent in node['dependencies']:
                    db.execute('INSERT INTO checkpoint_dependency_pins VALUES(?,?,?,?)',(bindings[node['task_id']],parent,bindings[parent],identity))
            return receipt

    def list(self, source_batch_id):
        with self.store.connect() as db:
            self.batches._receipt(db,source_batch_id)
            return [json.loads(row[0]) for row in db.execute('SELECT snapshot FROM checkpoint_recoveries WHERE source_batch_id=? ORDER BY created_at DESC,id DESC LIMIT 100',(source_batch_id,))]

    def get(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT snapshot FROM checkpoint_recoveries WHERE id=?',(identity,)).fetchone()
            if row is None: raise KeyError('检查点恢复不存在')
            receipt = json.loads(row[0])
            return {**receipt,'batch':self.batches._get(db,receipt['batch_id'])}
