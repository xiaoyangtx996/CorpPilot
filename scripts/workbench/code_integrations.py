"""Owner-authorized native integration ledger; dispatch belongs to CLIController."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from . import artifacts, code_reviews, dependencies
from .code_reviews import encoded
from .executions import Executions
from .reviews import Reviews
from .store import _text
from .tasks import Tasks


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS code_integrations (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES conversations(id),
        request_id TEXT NOT NULL, authorization TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','stopping','completed','failed','cancelled','unknown')),
        result TEXT, completion TEXT, summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(project_id,request_id))''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS integration_fixed BEFORE UPDATE ON code_integrations
        WHEN NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.request_id IS NOT OLD.request_id
        OR NEW.authorization IS NOT OLD.authorization OR NEW.created_at IS NOT OLD.created_at
        OR OLD.state NOT IN ('running','stopping')
        BEGIN SELECT RAISE(ABORT,'Integration authorization and terminal result are immutable'); END''')
    db.execute("CREATE TRIGGER IF NOT EXISTS integration_no_delete BEFORE DELETE ON code_integrations BEGIN SELECT RAISE(ABORT,'Integration is immutable'); END")
    db.execute('''CREATE TRIGGER IF NOT EXISTS integration_no_replace BEFORE INSERT ON code_integrations
        WHEN EXISTS(SELECT 1 FROM code_integrations WHERE id=NEW.id OR (project_id=NEW.project_id AND request_id=NEW.request_id))
        BEGIN SELECT RAISE(ABORT,'Integration is immutable'); END''')
    db.execute('''CREATE TABLE IF NOT EXISTS code_integration_reconciliations (
        integration_id TEXT PRIMARY KEY REFERENCES code_integrations(id), receipt TEXT NOT NULL)''')
    for operation in ('UPDATE', 'DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS integration_reconciliation_no_{operation.lower()} BEFORE {operation} ON code_integration_reconciliations BEGIN SELECT RAISE(ABORT,'Declaration is immutable'); END")
    db.execute('''CREATE TRIGGER IF NOT EXISTS integration_reconciliation_no_replace BEFORE INSERT ON code_integration_reconciliations
        WHEN EXISTS(SELECT 1 FROM code_integration_reconciliations WHERE integration_id=NEW.integration_id)
        BEGIN SELECT RAISE(ABORT,'Declaration is immutable'); END''')


def _ids(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16 or any(not isinstance(v, str) or not v or len(v) > 120 for v in value) or len(set(value)) != len(value):
        raise ValueError('须选择 1–16 个不重复的来源执行')
    return list(value)


class CodeIntegrations:
    def __init__(self, store):
        self.store = store
        self.reviews = code_reviews.CodeReviews(store)
        with store.connect() as db: initialize(db)

    def _project(self, db, identity):
        room = self.store._conversation(db, identity)
        if room['type'] != 'project': raise ValueError('代码集成仅适用于项目群')
        return room

    @staticmethod
    def _get(db, identity):
        row = db.execute('SELECT * FROM code_integrations WHERE id=?', (identity,)).fetchone()
        if row is None: raise KeyError('代码集成不存在')
        declaration = db.execute('SELECT receipt FROM code_integration_reconciliations WHERE integration_id=?', (identity,)).fetchone()
        return dict(id=row['id'], project_id=row['project_id'], request_id=row['request_id'],
                    **json.loads(row['authorization']), state=row['state'], result=json.loads(row['result']) if row['result'] else None,
                    summary=row['summary'], created_at=row['created_at'], updated_at=row['updated_at'],
                    reconciliation=json.loads(declaration[0]) if declaration else None)

    def get(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN'); return self._get(db, identity)

    def request(self, project_id, request_id):
        with self.store.connect() as db:
            db.execute('BEGIN'); self._project(db, project_id)
            row = db.execute('SELECT id FROM code_integrations WHERE project_id=? AND request_id=?', (project_id, request_id)).fetchone()
            return self._get(db, row[0]) if row else None

    def list(self, project_id):
        with self.store.connect() as db:
            db.execute('BEGIN'); self._project(db, project_id)
            return [self._get(db, row[0]) for row in db.execute('SELECT id FROM code_integrations WHERE project_id=? ORDER BY rowid DESC LIMIT 100', (project_id,))]

    def _snapshot(self, db, project_id, identities):
        room = self._project(db, project_id)
        blockers = ['项目已归档'] if room['archived'] else []
        sources, common = [], None
        total = 0
        for identity in identities:
            observed = code_reviews._preview(self.store, db, identity)
            fixed = observed['snapshot']; blockers.extend(observed['blockers'])
            if fixed['source_task']['conversation_id'] != project_id: blockers.append('来源必须属于当前项目')
            manifest = fixed['manifest']
            if manifest:
                group = tuple(manifest[k] for k in ('conversation_id', 'base_commit', 'base_tree', 'integration_agent_id', 'repository_revision'))
                if common is not None and group != common: blockers.append('来源必须具有相同项目、基线、仓库版本及集成人')
                common = group
            total += sum(a['size'] for a in fixed['artifacts'] if a['path'] in ('corppilot-code/change.patch', 'corppilot-code/manifest.json'))
            receipt = self.reviews._get(db, identity)
            review_run, decision, entries = None, None, []
            if receipt is None:
                blockers.append('来源尚未创建指定集成人评审任务')
            else:
                latest = db.execute('SELECT id FROM task_executions WHERE task_id=? ORDER BY attempt DESC LIMIT 1', (receipt['task_id'],)).fetchone()
                if latest is None: raise ValueError('评审执行关联缺失')
                run = Executions._run(db, latest[0]); task = Tasks._task(db, run['task_id'])
                review_run = {k: run[k] for k in ('id', 'task_id', 'agent_id', 'attempt', 'requirement_version', 'state', 'exit_code')}
                decision = Reviews._review(db, run['id'])
                if run['state'] != 'awaiting_review' or run['exit_code'] != 0 or task['requirement_version'] != run['requirement_version']:
                    blockers.append('指定评审的最新执行须已成功完成当前需求')
                if decision is None or decision['decision'] != 'approved': blockers.append('须由 Owner 批准最新评审报告')
                try:
                    code_reviews.authorize(self.store, db, run)
                    dependencies.check_bound(db, run)
                except (ValueError, PermissionError): blockers.append('指定评审的固定授权或输入已经失效')
                rows = db.execute('SELECT * FROM execution_artifacts WHERE execution_id=? ORDER BY id LIMIT 101', (run['id'],)).fetchall()
                entries = [{k: row[k] for k in ('id', 'execution_id', 'path', 'size', 'sha256')} for row in rows]
                try:
                    if not rows or len(rows) > artifacts.MAX_FILES or sum(len(row['content']) for row in rows) > artifacts.MAX_TOTAL_BYTES: raise ValueError()
                    content = {row['path']: artifacts.verify_snapshot(row)['data'] for row in rows}
                    if not content.get('review.md', b'').strip(): raise ValueError()
                except (ValueError, TypeError): blockers.append('已保存评审报告缺失或校验失败')
                if decision and decision['artifact_ids'] != [a['id'] for a in entries]: blockers.append('评审成果与 Owner 批准清单不一致')
            sources.append(dict(source_snapshot=fixed, code_review_receipt=receipt, review_execution=review_run,
                                review_owner_decision=decision, review_artifacts=entries))
        if total > 16 * 1024 * 1024: blockers.append('所选补丁与清单合计超过 16 MiB')
        return dict(project_id=project_id, sources=sources), list(dict.fromkeys(blockers))

    def _preview(self, db, project_id, identities):
        snapshot, blockers = self._snapshot(db, project_id, identities)
        row = db.execute('SELECT id FROM code_integrations WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (project_id,)).fetchone()
        latest = self._get(db, row[0]) if row else None
        if latest and latest['state'] in ('running', 'stopping'): blockers.append('上次集成尚未结束')
        if latest and latest['state'] == 'unknown' and latest['reconciliation'] is None: blockers.append('须先核查上次未知集成的进程与影响')
        value = dict(snapshot=snapshot, latest_integration_id=latest['id'] if latest else None, blockers=blockers)
        return value | {'fingerprint': hashlib.sha256(encoded(value).encode()).hexdigest()}

    def preview(self, project_id, payload):
        if not isinstance(payload, dict) or set(payload) != {'source_execution_ids'}: raise ValueError('预览仅接受来源执行清单')
        identities = _ids(payload['source_execution_ids'])
        with self.store.connect() as db:
            db.execute('BEGIN'); return self._preview(db, project_id, identities)

    def create(self, project_id, payload):
        fields = {'request_id', 'source_execution_ids', 'fingerprint', 'previous_integration_id', 'reconciliation_note', 'confirm'}
        if not isinstance(payload, dict) or set(payload) != fields or payload['confirm'] is not True: raise ValueError('集成需要完整授权与明确确认')
        value = dict(payload, request_id=_text(payload['request_id'], '请求 ID', 120), source_execution_ids=_ids(payload['source_execution_ids']))
        if not isinstance(value['fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', value['fingerprint']): raise ValueError('预览指纹无效')
        previous = value['previous_integration_id']
        if previous is not None: value['previous_integration_id'] = _text(previous, '上次集成 ID', 120)
        if not isinstance(value['reconciliation_note'], str) or len(value['reconciliation_note'].strip()) > 2000: raise ValueError('核查说明无效')
        value['reconciliation_note'] = value['reconciliation_note'].strip()
        if (previous is None and value['reconciliation_note']) or (previous is not None and not value['reconciliation_note']): raise ValueError('首次无需核查说明；后续必须说明原集成影响')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM code_integrations WHERE project_id=? AND request_id=?', (project_id, value['request_id'])).fetchone()
            if old:
                receipt = self._get(db, old[0])
                if receipt['request_payload'] != value: raise ValueError('相同请求 ID 不能改变授权')
                return receipt
            preview = self._preview(db, project_id, value['source_execution_ids'])
            if preview['blockers']: raise ValueError('；'.join(preview['blockers']))
            if preview['fingerprint'] != value['fingerprint'] or preview['latest_integration_id'] != value['previous_integration_id']: raise ValueError('集成预览或上次集成已变化')
            identity = str(uuid.uuid4())
            authorization = dict(request_payload=value, authorization_snapshot=preview['snapshot'], fingerprint=value['fingerprint'],
                destination_relative=f'code-integrations/{identity}/repository', branch=f'corppilot/integration-{identity}',
                previous_integration_id=value['previous_integration_id'], reconciliation_note=value['reconciliation_note'])
            db.execute("INSERT INTO code_integrations(id,project_id,request_id,authorization,state) VALUES(?,?,?,?,'running')", (identity, project_id, value['request_id'], encoded(authorization)))
            return self._get(db, identity)

    def _authorize(self, db, receipt):
        snapshot, blockers = self._snapshot(db, receipt['project_id'], receipt['request_payload']['source_execution_ids'])
        if blockers or snapshot != receipt['authorization_snapshot']: raise ValueError('集成的固定来源、最新评审或权限已经变化')

    def authorize(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN'); receipt = self._get(db, identity)
            if receipt['state'] not in ('running', 'stopping'): raise ValueError('集成已结束')
            self._authorize(db, receipt)
            return receipt

    def inputs(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN'); receipt = self._get(db, identity)
            if receipt['state'] != 'running': raise ValueError('只有运行中的集成可准备输入')
            self._authorize(db, receipt)
            sources = receipt['authorization_snapshot']['sources']; changes = []
            for source in sources:
                items = source['source_snapshot']['artifacts']
                pair = {}
                for item in items:
                    if item['path'] in ('corppilot-code/change.patch', 'corppilot-code/manifest.json'):
                        row = db.execute('SELECT * FROM execution_artifacts WHERE id=?', (item['id'],)).fetchone()
                        pair[item['path']] = artifacts.verify_snapshot(row)['data']
                changes.append(dict(patch=pair['corppilot-code/change.patch'], manifest=json.loads(pair['corppilot-code/manifest.json'])))
            repository = sources[0]['source_snapshot']['repository']['snapshot']
            return dict(source_path=repository['source_path'], base_commit=repository['commit'],
                        destination=Path(self.store.data_dir) / receipt['destination_relative'], branch=receipt['branch'], changes=changes)

    @staticmethod
    def _result(receipt, result):
        fields = {'source_path', 'base_commit', 'base_tree', 'branch', 'commit', 'tree', 'files', 'total_bytes',
                  'conversation_id', 'integration_agent_id', 'repository_revision', 'source_execution_ids', 'patch_sha256s'}
        if not isinstance(result, dict) or set(result) != fields: raise ValueError('原生集成回执字段无效')
        sources = receipt['authorization_snapshot']['sources']; repo = sources[0]['source_snapshot']['repository']['snapshot']
        manifest = sources[0]['source_snapshot']['manifest']
        expected = dict(source_path=repo['source_path'], base_commit=repo['commit'], base_tree=repo['tree'], branch=receipt['branch'],
            conversation_id=receipt['project_id'], integration_agent_id=repo['integration_agent_id'], repository_revision=manifest['repository_revision'],
            source_execution_ids=receipt['request_payload']['source_execution_ids'], patch_sha256s=[s['source_snapshot']['manifest']['patch_sha256'] for s in sources])
        if any(result[k] != v for k, v in expected.items()): raise ValueError('原生集成回执与固定授权不一致')
        if any(not isinstance(result[k], str) or not re.fullmatch('[0-9a-f]{' + str(len(repo['commit'])) + '}', result[k]) for k in ('commit', 'tree')): raise ValueError('原生提交或树标识无效')
        if type(result['repository_revision']) is not int or any(type(result[k]) is not int or not 0 <= result[k] <= maximum for k, maximum in (('files', 10000), ('total_bytes', 256 * 1024 * 1024))): raise ValueError('原生集成范围无效')

    def finish(self, identity, state, result=None, summary=''):
        if state not in ('completed', 'failed', 'cancelled', 'unknown'): raise ValueError('集成结束状态无效')
        if not isinstance(summary, str) or len(summary) > 2000: raise ValueError('集成摘要无效')
        completion = encoded(dict(state=state, result=result, summary=summary))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); receipt = self._get(db, identity)
            if result is not None: self._result(receipt, result)
            if state == 'completed' and result is None: raise ValueError('成功必须有真实原生回执')
            if receipt['state'] not in ('running', 'stopping'):
                if db.execute('SELECT completion FROM code_integrations WHERE id=?', (identity,)).fetchone()[0] != completion:
                    raise ValueError('集成终态不可改写')
                return receipt
            if state == 'completed':
                try: self._authorize(db, receipt)
                except (ValueError, PermissionError, KeyError):
                    state, summary = 'failed', '原生集成已产出结果，但固定来源、评审或权限变化；保留实际结果供核查'
                else:
                    if receipt['state'] == 'stopping': summary = (summary[:1800] + '；原生集成已完成，停止请求没有回滚产物')
            db.execute("UPDATE code_integrations SET state=?,result=?,completion=?,summary=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (state, encoded(result) if result is not None else None, completion, summary, identity))
            return self._get(db, identity)

    def stop(self, identity):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); receipt = self._get(db, identity)
            if receipt['state'] == 'running':
                db.execute("UPDATE code_integrations SET state='stopping',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (identity,))
            return self._get(db, identity)

    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE code_integrations SET state='unknown',summary='服务重启，原生集成结果或进程状态尚未核查；不会自动重跑',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE state IN ('running','stopping')")

    def unresolved(self):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM code_integrations i WHERE state='unknown' AND NOT EXISTS(SELECT 1 FROM code_integration_reconciliations r WHERE r.integration_id=i.id) LIMIT 1").fetchone() is not None

    def reconcile(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'process_stopped', 'effects_checked', 'note'} or payload['process_stopped'] is not True or payload['effects_checked'] is not True: raise ValueError('须明确声明进程已停止并核查影响')
        value = dict(payload, request_id=_text(payload['request_id'], '请求 ID', 120), note=_text(payload['note'], '核查说明', 2000))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); receipt = self._get(db, identity)
            if receipt['reconciliation'] is not None:
                if receipt['reconciliation']['request_payload'] != value: raise ValueError('原核查声明不能覆盖')
                return receipt
            if receipt['state'] != 'unknown': raise ValueError('仅未知集成允许人工核查')
            declaration = dict(integration_id=identity, request_payload=value, source='owner_declared',
                created_at=db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0])
            db.execute('INSERT INTO code_integration_reconciliations VALUES(?,?)', (identity, encoded(declaration)))
            return self._get(db, identity)
