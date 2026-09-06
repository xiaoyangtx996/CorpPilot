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
    args = parser.parse_args()
    home = args.data_dir.resolve()
    home.mkdir(parents=True, exist_ok=True)
    if any(home.iterdir()):
        raise ValueError('Browser fixture requires an empty directory; never use real app data')
    control_path = home / 'control.json'
    evidence_path = home / 'evidence.jsonl'
    lock = threading.Lock()

    def control():
        try:
            return json.loads(control_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

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
        paths = prepare_workspace(store.data_dir, identity, kwargs['input_artifacts'])
        event('cli_call', execution_id=identity, input_ids=[a['id'] for a in kwargs['input_artifacts']],
              private_leaked='F53_PRIVATE_' in kwargs['prompt'])
        deadline = time.monotonic() + 50
        while control().get('pause_runner') and time.monotonic() < deadline:
            if kwargs['cancel'].is_set():
                return dict(exit_code=130, success=False, reason='cancelled', summary='Fixture cancelled', workspace=str(paths['work']))
            time.sleep(0.02)
        directory = paths['work'] / 'artifacts'
        directory.mkdir(exist_ok=True)
        (directory / 'result.txt').write_text(f'F53 captured result {identity}; inputs={len(kwargs["input_artifacts"])}', encoding='utf-8')
        return dict(exit_code=0, success=True, reason='exited', summary='Fixture result captured', workspace=str(paths['work']),
                    usage={'input_tokens': 7, 'output_tokens': None, 'cached_input_tokens': 0})

    cli_controller.run_codex = runner

    class FixtureHandler(Handler):
        rejected = set()

        def do_POST(self):
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

        def respond(self, status, value):
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
