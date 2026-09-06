"""Isolated browser fixture: loopback model + Python runner, never real CLI/Docker.

Requires a new empty data directory. All controls and evidence stay there, outside
the application repository. A shutdown file provides portable graceful teardown.
"""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from workbench.store import Store
from workbench.server import WorkbenchServer, Handler
from workbench.settings import Settings
from workbench.cli_settings import CLISettings
from workbench.cli import prepare_workspace
from workbench import cli_controller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--checkpoint', action='store_true')
    parser.add_argument('--budget', action='store_true')
    parser.add_argument('--fees', action='store_true')
    parser.add_argument('--context', action='store_true')
    parser.add_argument('--tools', action='store_true')
    parser.add_argument('--repository', action='store_true')
    parser.add_argument('--code-review', action='store_true')
    parser.add_argument('--code-integration', action='store_true')
    args = parser.parse_args()
    home = args.data_dir.resolve()
    home.mkdir(parents=True, exist_ok=True)
    if any(home.iterdir()):
        raise ValueError('Browser fixture requires an empty directory; never use real app data')
    control_path = home / 'control.json'
    evidence_path = home / 'evidence.jsonl'
    lock = threading.Lock()
    last_control = {}

    def control():
        nonlocal last_control
        try:
            value = json.loads(control_path.read_text(encoding='utf-8'))
            if isinstance(value, dict): last_control = value
        except (OSError, ValueError):
            pass
        return last_control

    def event(kind, **values):
        with lock, evidence_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(kind=kind, **values), ensure_ascii=False) + '\n')

    store = Store(home / 'state')
    people = store.agents()[:3]
    for person in people:
        store.save_agent({'enabled': True, 'tools': ['read', 'write', 'execute'], 'model': 'default'}, person['id'])
    source = store.save_conversation({'type': 'dm', 'title': 'F53 QA Secretary', 'member_ids': [people[0]['id']]})
    store.send_message(source['id'], {'content': 'F53_PRIVATE_HISTORY_DO_NOT_SHARE', 'request_id': 'private'})
    message = store.send_message(source['id'], {'content': 'F53_PRIVATE_TARGET: create a three-step deliverable; explicitly choose what to share', 'request_id': 'target'})
    proposal = dict(title='F53 自动协作成果', shared_brief='MODEL_SUMMARY_MUST_NOT_OVERRIDE_OWNER', tasks=[
        dict(key=key, title=title, scope='Write a small evidence file in artifacts/', acceptance='Captured text file is readable',
             agent_id=people[actor]['id'], depends_on=deps)
        for key, title, actor, deps in [('a', 'F53 起草', 1, []), ('b', 'F53 审阅', 2, ['a']), ('c', 'F53 汇总', 1, ['b'])]])

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            event('model_call', body=body)
            deadline = time.monotonic() + 20
            while control().get('pause_model') and time.monotonic() < deadline and not (home / 'shutdown').exists():
                time.sleep(0.02)
            result = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}],
                      'usage': {'prompt_tokens': 40, 'completion_tokens': 30}}
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except OSError:
                pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    os.environ['CORPPILOT_BROWSER_FIXTURE_KEY'] = 'fixture-only'
    Settings(store).save(dict(enabled=True, base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        model='browser-fixture', api_key_env='CORPPILOT_BROWSER_FIXTURE_KEY', max_output_tokens=1000,
        timeout_seconds=30, max_concurrency=1, rpm=100))
    dummy = home / 'never-executed.exe'
    dummy.write_bytes(b'fixture placeholder, never executed')
    CLISettings(store).save(dict(enabled=True, backend='local', executable=str(dummy), model='fixture',
        api_key_env='CORPPILOT_BROWSER_FIXTURE_KEY', timeout_seconds=60, max_concurrency=2))

    def runner(**kwargs):
        identity = kwargs['execution_id']
        paths = prepare_workspace(store.data_dir, identity, kwargs['input_artifacts'], kwargs.get('repository'))
        event('cli_call', execution_id=identity, input_ids=[a['id'] for a in kwargs['input_artifacts']],
              private_leaked='F53_PRIVATE_' in kwargs['prompt'])
        if args.code_review:
            event('code_review_inputs', execution_id=identity, repository=kwargs.get('repository'),
                  artifacts=[{k:a[k] for k in ('id','execution_id','path','size','sha256')} for a in kwargs['input_artifacts']],
                  base_text=(paths['work']/'repository/code.txt').read_text(encoding='utf-8'))
        deadline = time.monotonic() + 50
        while control().get('pause_runner') and time.monotonic() < deadline:
            if kwargs['cancel'].is_set():
                return dict(exit_code=130, success=False, reason='cancelled', summary='Fixture cancelled', workspace=str(paths['work']))
            time.sleep(0.02)
        directory = paths['work'] / 'artifacts'
        directory.mkdir(exist_ok=True)
        filename = 'review.md' if args.code_review or args.code_integration else 'result.txt'
        (directory / filename).write_text(f'F73 集成人建议，仍待 Owner 批准。{identity}' if args.code_review else f'F53 captured result {identity}; inputs={len(kwargs["input_artifacts"])}', encoding='utf-8')
        return dict(exit_code=0, success=True, reason='exited', summary='Fixture result captured', workspace=str(paths['work']),
                    usage={'input_tokens': 7, 'output_tokens': None, 'cached_input_tokens': 0})

    cli_controller.run_codex = runner

    checkpoint = None
    if args.checkpoint:
        from workbench.collaboration import Collaboration
        from workbench.project_executions import ProjectExecutions
        from workbench.reviews import Reviews
        from workbench import artifacts
        plan = Collaboration(store).create(source['id'], dict(request_id='checkpoint-plan', source_message_id=message['id'],
            title='F60 检查点项目', shared_brief='Explicit checkpoint fixture', coordinator_id=people[0]['id'], tasks=proposal['tasks']))
        batches = ProjectExecutions(store)
        batch = batches.create(plan['id'], {'request_id': 'original-batch', 'tasks': [
            dict(task_id=identity, expected_version=1, previous_execution_id=None, reconciliation_note='') for identity in plan['task_ids'].values()]})
        a, b, c = [item['execution_id'] for item in batch['tasks']]
        assert batches.executions.claim(a)
        batches.executions.report(a, 1, 1, 0, 'Retained checkpoint', success=True, artifacts=[{'path': 'retained.txt', 'data': b'F60 retained bytes'}])
        Reviews(store).save(a, dict(request_id='approve-checkpoint', expected_version=1, decision='approved', note='Inspected retained bytes',
            artifact_ids=[item['id'] for item in artifacts.list_for(store, a)]))
        assert batches.executions.claim(b)
        batches.executions.report(b, 1, 1, 1, 'Fixture interrupted')
        batches.executions.cancel(c)
        checkpoint = dict(plan=plan, batch=batch, retained_execution_id=a)

    fee_model = None
    if args.fees:
        from workbench.runs import Runs
        runs = Runs(store)
        fee_model = runs.create(source['id'], dict(agent_id=people[0]['id'], source_message_id=message['id'], request_id='fee-historical-model'))
        assert runs.claim(fee_model['id'])
        runs.fail(fee_model['id'], 'Historical ended fixture; unknown usage is not zero')
        fee_model = runs.get(fee_model['id'])

    budget = None
    if args.budget or args.fees:
        from workbench.collaboration import Collaboration
        from workbench.project_executions import ProjectExecutions
        from workbench.budgets import Budgets
        Budgets(store).save(dict(enabled=True, total_micro_usd=0,
            model_reserve_micro_usd=1000000, cli_reserve_micro_usd=1000000))
        plan = Collaboration(store).create(source['id'], dict(request_id='budget-plan', source_message_id=message['id'],
            title='F62 预算项目', shared_brief='Explicit budget fixture', coordinator_id=people[0]['id'],
            tasks=[{**task, 'depends_on': []} for task in proposal['tasks'][:3 if args.fees else 2]]))
        batch = ProjectExecutions(store).create(plan['id'], {'request_id': 'budget-batch', 'tasks': [
            dict(task_id=identity, expected_version=1, previous_execution_id=None, reconciliation_note='') for identity in plan['task_ids'].values()]})
        budget = dict(plan=plan, batch=batch)

    context = None
    if args.context:
        from workbench.runs import Runs
        from workbench.context_receipts import ContextReceipts
        from workbench.tasks import Tasks
        from workbench.executions import Executions
        from workbench.reviews import Reviews
        from workbench.memories import Memories
        from workbench import artifacts
        contexts, runs = ContextReceipts(store), Runs(store)
        latest = message
        for i in range(101):
            latest = store.send_message(source['id'], dict(content=f'F66_PRIVATE_INPUT_{i}', request_id=f'context-message-{i}'))
        model = runs.create(source['id'], dict(agent_id=people[0]['id'], source_message_id=latest['id'], request_id='context-model'))
        assert runs.claim(model['id'])
        contexts.record_model(model['id'], runs.snapshot(model['id']), 'fixture-context-model')
        runs.fail(model['id'], 'Fixture prepared snapshot; provider was not called')
        legacy = runs.create(source['id'], dict(agent_id=people[0]['id'], source_message_id=latest['id'], request_id='context-legacy'))
        runs.cancel(legacy['id'])
        room = store.save_conversation(dict(type='project', title='F66 上下文项目', member_ids=[p['id'] for p in people]))
        source_input = store.send_message(room['id'], dict(content='F66_PRIVATE_TASK_SOURCE', request_id='context-task-source'))
        tasks, executions, memory = Tasks(store), Executions(store), Memories(store)
        def task(title, key):
            return tasks.create(room['id'], dict(agent_id=people[1]['id'], source_message_id=source_input['id'],
                request_id=key, title=title, scope='F66_PRIVATE_SCOPE', acceptance='F66_PRIVATE_ACCEPTANCE'))
        parent = task('F66 已批准来源', 'context-parent')
        def create(task):
            return executions.create(task['id'], dict(expected_version=task['requirement_version'], request_id='context-execution',
                previous_execution_id=None, reconciliation_note=''))
        upstream = create(parent); assert executions.claim(upstream['id'])
        executions.report(upstream['id'],1,1,0,'Fixture inspected output',success=True,artifacts=[{'path':'context-proof.txt','data':b'F66_PRIVATE_ARTIFACT_BYTES'}])
        Reviews(store).save(upstream['id'], dict(request_id='context-approve', expected_version=1, decision='approved',
            note='Fixture Owner inspected result',artifact_ids=[a['id'] for a in artifacts.list_for(store,upstream['id'])]))
        scopes = [('agent',people[1]['id']),('project',room['id'])]
        for scope,identity in scopes:
            candidate=memory.propose(scope,identity,dict(request_id='context-'+scope,expected_version=0,
                source_execution_id=upstream['id'],content='F66_PRIVATE_MEMORY_'+scope))
            memory.decide(candidate['id'],dict(request_id='context-approve-'+scope,decision='approved',note='Fixture Owner approval'))
        child = task('F66 上下文消费者','context-child')
        tasks.set_dependencies(child['id'],dict(expected_version=1,task_ids=[parent['id']]))
        child=tasks.get(child['id']); execution=create(child); assert executions.claim(execution['id'])
        contexts.record_cli(execution['id'],executions.snapshot(execution['id'],include_artifacts=True),'fixture-context-cli','local')
        executions.report(execution['id'],execution['attempt'],execution['requirement_version'],1,'Fixture prepared snapshot; tool was not called')
        for scope,identity in scopes:
            memory.rollback(scope,identity,dict(request_id='context-rollback-'+scope,expected_version=1,target_version=0,note='Later memory changed'))
        context=dict(model=runs.get(model['id']),legacy=runs.get(legacy['id']),execution=executions.get(execution['id']),room=room)

    tool_samples = None
    if args.tools:
        from workbench.tasks import Tasks
        from workbench.executions import Executions
        from workbench.tool_activities import ToolActivities, parse_tools
        tasks, executions = Tasks(store), Executions(store)
        observations = ToolActivities(store)
        room = store.save_conversation(dict(type='project', title='F68 工具观察项目', member_ids=[p['id'] for p in people]))
        owner_message = store.send_message(room['id'], dict(content='F68_PRIVATE_SOURCE', request_id='tools-source'))
        def tool_event(kind, phase='completed', **data):
            return dict(type='item.'+phase, item=dict(id='F68_PRIVATE_ITEM_'+kind+data.get('command',''), type=kind, **data))
        rows = [
            tool_event('command_execution','started',status='in_progress',command='F68_PRIVATE_COMMAND',aggregated_output=''),
            tool_event('command_execution','updated',status='in_progress',command='F68_PRIVATE_COMMAND',aggregated_output='F68_PRIVATE_OUTPUT'),
            tool_event('command_execution',status='failed',exit_code=7,command='F68_PRIVATE_COMMAND',aggregated_output='F68_PRIVATE_OUTPUT'),
            tool_event('command_execution',status='declined',command='F68_PRIVATE_DECLINED',aggregated_output=''),
            tool_event('file_change',status='completed',changes=[dict(path='F68_PRIVATE_PATH',kind='update')]),
            tool_event('mcp_tool_call',status='completed',server='F68_PRIVATE_SERVER',tool='F68_PRIVATE_TOOL',arguments={'value':'F68_PRIVATE_ARGUMENT'},result={'content':[]}),
            tool_event('collab_tool_call',status='completed',tool='spawn_agent',sender_thread_id='F68_PRIVATE_THREAD',receiver_thread_ids=[],prompt='F68_PRIVATE_PROMPT',agents_states={}),
            tool_event('web_search',query='F68_PRIVATE_QUERY',action={'type':'search'}),
        ]
        tool_samples = dict(room=room)
        for key, actor in [('observed',0),('limited',1),('empty',1),('legacy',2)]:
            task = tasks.create(room['id'], dict(agent_id=people[actor]['id'],source_message_id=owner_message['id'],
                request_id='tools-'+key,title='F68 '+key,scope='Fixture observation',acceptance='Read-only metadata'))
            run = executions.create(task['id'],dict(expected_version=1,request_id='tools-execution',previous_execution_id=None,reconciliation_note=''))
            assert executions.claim(run['id'])
            if key != 'legacy':
                selected = rows if key == 'observed' else [rows[0]]*501 if key == 'limited' else []
                raw = b'\n'.join(json.dumps(row).encode() for row in selected)
                if key == 'limited':raw += b'\n{"type":"future.event"}\n{broken'
                observations.record(run['id'],1,1,parse_tools({'stdout':raw,'reason':'output_limit' if key=='limited' else 'exited'}))
            executions.report(run['id'],1,1,1,'Controlled JSONL fixture; no tool or provider was invoked')
            tool_samples[key] = executions.get(run['id'])

    repository_sample = None
    code_review_sample = None
    code_integration_sample = None
    if args.repository or args.code_review or args.code_integration:
        from workbench.tasks import Tasks
        from workbench.executions import Executions
        from workbench.repo_sources import RepositorySources
        repository_path = home / 'git-source'; repository_path.mkdir()
        env = {k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_SYSTEM=os.devnull,
            GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.test',GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.test')
        def git(*argv):
            result = subprocess.run([shutil.which('git'),'-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false',*argv],
                cwd=repository_path,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
            if result.returncode:raise RuntimeError('Repository fixture Git failed')
            return result.stdout.decode().strip()
        git('init','-q'); (repository_path/'code.txt').write_text('F70 fixed source\n',encoding='utf-8')
        git('add','code.txt'); git('commit','-qm','Browser source'); commit=git('rev-parse','HEAD')
        room=store.save_conversation(dict(type='project',title='F70 已绑定代码项目',member_ids=[p['id'] for p in people]))
        other_room=store.save_conversation(dict(type='project',title='F70 新代码项目',member_ids=[p['id'] for p in people]))
        owner_message=store.send_message(room['id'],dict(content='Repository fixture tasks',request_id='repository-source'))
        tasks=Tasks(store); executions=Executions(store); repositories=RepositorySources(store)
        def ended(key):
            task=tasks.create(room['id'],dict(agent_id=people[0]['id'],source_message_id=owner_message['id'],request_id=key,
                title='F70 '+key,scope='Inspect fixed binding',acceptance='No new worker'))
            run=executions.create(task['id'],dict(expected_version=1,request_id=key,previous_execution_id=None,reconciliation_note=''))
            executions.cancel(run['id']); return executions.get(run['id'])
        old_run=ended('before-binding')
        saved=repositories.save(room['id'],dict(request_id='fixture-first',expected_revision=0,source_path=str(repository_path),
            commit=commit,integration_agent_id=people[1]['id'],confirm=True))
        bound_run=ended('fixed-binding')
        repository_sample=dict(room=room,other_room=other_room,source_path=str(repository_path),commit=commit,
            initial_binding=saved,old_run=old_run,bound_run=bound_run)
        if args.code_review:
            from workbench.code_changes import capture_code
            from workbench.reviews import Reviews
            from workbench import artifacts
            samples = {}
            for key, title in [('primary','F73 原代码成果'),('stop','F73 可停止评审'),('unapproved','F73 未批准成果'),('other','F73 另一个来源'),('retry','F73 同键恢复')]:
                task=tasks.create(room['id'],dict(agent_id=people[0]['id'],source_message_id=owner_message['id'],request_id=key,
                    title=title,scope='Change independent code copy',acceptance='Captured code patch'))
                run=executions.create(task['id'],dict(expected_version=1,request_id=key,previous_execution_id=None,reconciliation_note=''))
                assert executions.claim(run['id'])
                paths=prepare_workspace(store.data_dir,run['id'],[],saved['snapshot'])
                (paths['work']/'repository/code.txt').write_text('F73 changed '+key+'\n',encoding='utf-8')
                items=capture_code(store.data_dir,run['id'],saved,'fixture-only')
                executions.report(run['id'],1,1,0,'Native Git fixture captured',success=True,artifacts=items)
                if key!='unapproved':
                    Reviews(store).save(run['id'],dict(request_id='approve-'+key,expected_version=1,decision='approved',note='Owner checked fixture code',
                        artifact_ids=[a['id'] for a in artifacts.list_for(store,run['id'])]))
                samples[key]=dict(task=task,run=executions.get(run['id']))
            code_review_sample=dict(room=room,samples=samples,repository=saved)

        if args.code_integration:
            from workbench.code_changes import capture_code
            from workbench.code_reviews import CodeReviews
            from workbench.reviews import Reviews
            from workbench import artifacts
            from workbench.git_checkout import PreparationUnknownError
            samples = {}
            reviews = CodeReviews(store)
            for key in ('alpha', 'beta'):
                task=tasks.create(room['id'],dict(agent_id=people[0]['id'],source_message_id=owner_message['id'],request_id='integrate-'+key,
                    title='F76 来源 '+key,scope='Add an independent file',acceptance='Native captured patch and complete approved review'))
                run=executions.create(task['id'],dict(expected_version=1,request_id=key,previous_execution_id=None,reconciliation_note=''))
                assert executions.claim(run['id'])
                paths=prepare_workspace(store.data_dir,run['id'],[],saved['snapshot'])
                (paths['work']/('repository/'+key+'.txt')).write_bytes(('F76 '+key+'\n').encode())
                executions.report(run['id'],1,1,0,'Real Git capture; controlled source author',success=True,
                    artifacts=capture_code(store.data_dir,run['id'],saved,'fixture-only'))
                def approve(identity):
                    return Reviews(store).save(identity,dict(request_id='fixture-approve',expected_version=1,decision='approved',
                        note='Fixture Owner inspected complete outputs',artifact_ids=[a['id'] for a in artifacts.list_for(store,identity)]))
                approve(run['id'])
                receipt=reviews.create(run['id'],dict(request_id='review-'+key,fingerprint=reviews.preview(run['id'])['fingerprint'],confirm=True))
                review_id=receipt['initial_execution_id']; assert executions.claim(review_id)
                executions.report(review_id,1,1,0,'Controlled reviewer report; no model invoked',success=True,
                    artifacts=[dict(path='review.md',data=('F76 review '+key+': inspected fixed patch; recommend acceptance.\n').encode())])
                approve(review_id)
                samples[key]=dict(task=task,run=executions.get(run['id']),review=receipt)
            code_integration_sample=dict(room=room,samples=samples,repository=saved)
            native = cli_controller.integrate_code
            def observed_integration(**kwargs):
                event('integration_call',destination=str(kwargs['destination']),source_ids=[c['manifest']['execution_id'] for c in kwargs['changes']])
                deadline=time.monotonic()+50
                while control().get('pause_integration') and time.monotonic()<deadline:
                    if kwargs['cancel'].is_set():raise ValueError('Fixture observed cancellation before native Git')
                    time.sleep(.02)
                result=native(**kwargs)
                event('integration_native_result',result=result)
                if control().get('integration_unknown'):raise PreparationUnknownError('Fixture lost native completion observation')
                return result
            cli_controller.integrate_code=observed_integration

    class FixtureHandler(Handler):
        rejected = set()

        def do_POST(self):
            if '/code-integrations' in self.path:
                raw=self.rfile.read(int(self.headers['Content-Length'])); self.rfile=io.BytesIO(raw)
                event('integration_post',payload=json.loads(raw),path=self.path)
            if self.path.endswith('/code-review'):
                raw = self.rfile.read(int(self.headers['Content-Length'])); self.rfile = io.BytesIO(raw)
                event('code_review_post',payload=json.loads(raw),path=self.path)
            if self.path.endswith('/repository'):
                raw = self.rfile.read(int(self.headers['Content-Length'])); self.rfile = io.BytesIO(raw)
                event('repository_post',payload=json.loads(raw),path=self.path)
            if '/budget-settlements/' in self.path:
                raw = self.rfile.read(int(self.headers['Content-Length']))
                self.rfile = io.BytesIO(raw)
                event('fee_post', payload=json.loads(raw), path=self.path)
            if self.path.endswith('/checkpoint-recoveries'):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                self.rfile = io.BytesIO(raw)
                event('checkpoint_post', payload=json.loads(raw), path=self.path)
            if '/goal-executions/' in self.path and self.path.endswith('/stop'):
                event('stop_post', path=self.path)
            if self.path.endswith('/goal-executions') and self.headers.get('Authorization') == 'Bearer ' + self.server.access_token:
                raw = self.rfile.read(int(self.headers['Content-Length']))
                self.rfile = io.BytesIO(raw)
                payload = json.loads(raw)
                event('goal_post', payload=payload)
                if control().get('reject_first') and payload['request_id'] not in self.rejected:
                    self.rejected.add(payload['request_id'])
                    return Handler.respond(self, 400, {'error': 'Fixture first request rejected before acceptance'})
            return Handler.do_POST(self)

        def do_PATCH(self):
            if self.path == '/api/workbench/budget-settings':
                raw = self.rfile.read(int(self.headers['Content-Length']))
                self.rfile = io.BytesIO(raw)
                event('budget_patch', payload=json.loads(raw))
            return Handler.do_PATCH(self)

        def respond(self, status, value):
            if '/code-integrations' in self.path:
                if self.command=='GET' and control().get('integration_get503'):
                    return Handler.respond(self,503,{'error':'Integration readback unavailable'})
                if self.command=='POST' and status in (200,201,202):
                    event('integration_accepted',receipt=value,path=self.path)
                    if control().get('drop_integration'):
                        self.close_connection=True
                        try:self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                        return
            if '/code-review' in self.path:
                if self.command == 'GET' and control().get('code_review_get503'):
                    return Handler.respond(self,503,{'error':'Code review readback unavailable'})
                if self.command == 'POST' and status == 202:
                    event('code_review_accepted',receipt=value)
                    if control().get('drop_code_review'):
                        self.close_connection=True
                        try:self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                        return
            if '/repository' in self.path:
                if self.command == 'GET' and control().get('repository_get503'):
                    return Handler.respond(self,503,{'error':'Repository readback unavailable'})
                if self.command == 'POST' and status == 201:
                    event('repository_accepted',receipt=value)
                    if control().get('drop_repository'):
                        self.close_connection=True
                        try:self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                        return
            if '/budget-settlements/' in self.path:
                if self.command == 'GET' and control().get('fee_get503'):
                    return Handler.respond(self, 503, {'error': 'Fee readback unavailable'})
                if self.command == 'POST' and status == 201:
                    event('fee_accepted', receipt=value)
                    if control().get('drop_fee'):
                        self.close_connection = True
                        try: self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return
            if '/budget-' in self.path:
                if self.command == 'GET' and control().get('budget_get503'):
                    return Handler.respond(self, 503, {'error': 'Budget readback unavailable'})
                if self.command == 'PATCH' and status == 200:
                    event('budget_accepted', config=value)
                    if control().get('drop_budget'):
                        self.close_connection = True
                        try: self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return
            if '/checkpoint' in self.path:
                if self.command == 'GET' and control().get('checkpoint_get503'):
                    return Handler.respond(self, 503, {'error': 'Checkpoint readback unavailable'})
                if self.command == 'POST' and status == 202 and self.path.endswith('/checkpoint-recoveries'):
                    event('checkpoint_accepted', recovery_id=value['id'], batch_id=value['batch_id'])
                    if control().get('drop_checkpoint'):
                        self.close_connection = True
                        try: self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return
            if '/goal-executions' in self.path:
                if self.command == 'GET' and control().get('get503'):
                    return Handler.respond(self, 503, {'error': 'Fixture readback unavailable'})
                if self.command == 'POST' and self.path.endswith('/goal-executions') and status == 202:
                    event('goal_accepted', goal_id=value['id'])
                    if control().get('drop_goal'):
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return
                if self.command == 'POST' and self.path.endswith('/stop') and status == 200:
                    dropped = bool(control().get('drop_stop'))
                    event('stop_accepted', goal_id=value['id'], dropped=dropped)
                    if dropped:
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return
            return Handler.respond(self, status, value)

    server = WorkbenchServer(store, port=0)
    server.RequestHandlerClass = FixtureHandler
    manifest = dict(port=server.server_port, access_token=server.access_token, source_conversation_id=source['id'],
        source_message_id=message['id'], coordinator_id=people[0]['id'], agent_ids=[p['id'] for p in people],
        agent_names=[p['name'] for p in people], data_dir=str(home), pid=os.getpid())
    if checkpoint is not None: manifest['checkpoint'] = checkpoint
    if budget is not None: manifest['budget'] = budget
    if fee_model is not None: manifest['fee_model'] = fee_model
    if context is not None: manifest['context'] = context
    if tool_samples is not None: manifest['tools'] = tool_samples
    if repository_sample is not None: manifest['repository'] = repository_sample
    if code_review_sample is not None: manifest['code_review'] = code_review_sample
    if code_integration_sample is not None: manifest['code_integration'] = code_integration_sample
    (home / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def shutdown_watcher():
        while not (home / 'shutdown').exists():
            time.sleep(0.05)
        server.shutdown()

    threading.Thread(target=shutdown_watcher, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(5)
        event('shutdown_complete')


if __name__ == '__main__':
    main()
