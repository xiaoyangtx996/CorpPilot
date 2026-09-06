"""One explicit goal authorization across real local HTTP and captured CLI inputs.

The provider is a local fixture and the CLI runner is controlled Python; no paid
model, external executable, or Docker worker is invoked.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

from test_workbench_api import running, request
from test_workbench_collaboration import setup
from test_workbench_controller import configure as configure_model, until
from test_workbench_project_execution_api import configure as configure_cli
from test_workbench_provider import endpoint
from test_workbench_cli_controller import result
from workbench.settings import Settings
from workbench.executions import Executions
from workbench.cli import prepare_workspace
from workbench import cli_controller
from workbench.cli_controller import CLIController


def goal_input(tmp_path):
    store, _, source, plan = setup(tmp_path)
    coordinator = plan['coordinator_id']
    worker = plan['tasks'][0]['agent_id']
    store.save_agent({'tools': ['read', 'write', 'execute']}, worker)
    plan['tasks'].append({**plan['tasks'][1], 'key': 'c', 'title': 'final', 'depends_on': ['b']})
    payload = dict(request_id='goal-once', source_message_id=plan['source_message_id'],
                   agent_id=coordinator, candidate_ids=[coordinator, worker],
                   shared_brief='EXPLICIT SHARED', max_tasks=3,
                   confirm_execution=True, confirm_handoff=True)
    proposal = {k: plan[k] for k in ('title', 'shared_brief', 'tasks')}
    proposal['shared_brief'] = 'MODEL SUMMARY MUST NOT REPLACE OWNER SHARING'
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}]}
    return store, source, payload, output


def test_goal_http_single_authorization_executes_chain_and_recovers(tmp_path, monkeypatch):
    store, source, payload, output = goal_input(tmp_path)
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        paths = prepare_workspace(store.data_dir, kwargs['execution_id'], kwargs['input_artifacts'])
        directory = paths['work'] / 'artifacts'
        directory.mkdir(exist_ok=True)
        (directory / 'result.txt').write_bytes(f'goal-proof-{len(calls)}'.encode())
        return result()

    monkeypatch.setattr(cli_controller, 'run_codex', runner)
    path = f'/api/workbench/conversations/{source}/goal-executions'
    with endpoint(output) as (config, provider_calls):
        with running(tmp_path) as port:
            configure_cli(port, tmp_path, monkeypatch)
            configure_model(Settings(store), config, monkeypatch)
            assert request(port, 'POST', path, payload, headers={'Authorization': ''})[0] == 401
            with ThreadPoolExecutor(max_workers=4) as pool:
                replies = list(pool.map(lambda _: request(port, 'POST', path, payload), range(8)))
            assert all(status == 202 for status, _ in replies)
            assert len({row['id'] for _, row in replies}) == 1
            identity = replies[0][1]['id']
            detail = f'/api/workbench/goal-executions/{identity}'
            assert request(port, 'GET', detail, headers={'Authorization': ''})[0] == 401
            receipt = until(lambda: (row if (row := request(port, 'GET', detail)[1])['launch_id'] else None))
            assert receipt['request_payload'] == payload
            launch = receipt['launch']
            ids = [item['execution_id'] for item in launch['batch']['tasks']]
            executions = Executions(store)
            until(lambda: all(executions.get(identity)['state'] == 'awaiting_review' for identity in ids))
            assert [c['execution_id'] for c in calls] == ids
            assert [len(c['input_artifacts']) for c in calls] == [0, 1, 1]
            assert calls[1]['input_artifacts'][0]['data'] == b'goal-proof-1'
            assert calls[2]['input_artifacts'][0]['data'] == b'goal-proof-2'
            assert len(provider_calls) == 1
            assert 'EXPLICIT SHARED' in str(provider_calls[0][2])
            assert 'PRIVATE ORIGINAL' not in str(provider_calls[0][2])
            assert all('PRIVATE ORIGINAL' not in c['prompt'] for c in calls)
            assert launch['request_payload']['plan']['shared_brief'] == payload['shared_brief']
            assert store.messages(launch['collaboration']['project_conversation_id'])[0]['content'] == payload['shared_brief']
            with store.connect() as db:
                assert db.execute('SELECT count(*) FROM execution_reviews').fetchone()[0] == 0
                assert db.execute('SELECT count(*) FROM execution_artifacts').fetchone()[0] == 3
                assert db.execute('SELECT count(*) FROM project_launches').fetchone()[0] == 1
                assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            status, final_artifacts = request(port, 'GET', f'/api/workbench/executions/{ids[-1]}/artifacts')
            assert status == 200 and len(final_artifacts) == 1
            status, review = request(port, 'POST', f'/api/workbench/executions/{ids[-1]}/review', {
                'request_id': 'final-owner-review', 'expected_version': 1, 'decision': 'approved',
                'note': 'Controlled fixture final artifact accepted',
                'artifact_ids': [a['id'] for a in final_artifacts]})
            assert status == 201 and review['decision'] == 'approved'
            with store.connect() as db:
                assert db.execute('SELECT execution_id FROM execution_reviews').fetchall()[0][0] == ids[-1]
                assert db.execute('SELECT count(*) FROM execution_reviews').fetchone()[0] == 1
            changed = copy.deepcopy(payload)
            changed['max_tasks'] = 2
            assert request(port, 'POST', path, changed)[0] == 400
            Settings(store).save({'enabled': False})
            request(port, 'PATCH', '/api/workbench/cli-settings', {'enabled': False})
            assert request(port, 'POST', path, payload)[1]['launch_id'] == launch['id']
        with running(tmp_path) as port:
            restored = request(port, 'GET', detail)[1]
            assert restored['launch_id'] == launch['id']
            assert request(port, 'POST', path, payload)[1]['planning_run_id'] == receipt['planning_run_id']
            assert len(request(port, 'GET', path)[1]) == 1
        assert len(provider_calls) == 1 and len(calls) == 3


def test_goal_http_stop_during_model_prevents_late_launch(tmp_path, monkeypatch):
    store, source, payload, output = goal_input(tmp_path)
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kwargs: (_ for _ in ()).throw(AssertionError('Stopped goal dispatched CLI')))
    path = f'/api/workbench/conversations/{source}/goal-executions'
    with endpoint(output, delay=0.8) as (config, provider_calls):
        with running(tmp_path) as port:
            configure_cli(port, tmp_path, monkeypatch)
            configure_model(Settings(store), config, monkeypatch)
            status, receipt = request(port, 'POST', path, payload)
            assert status == 202
            detail = f"/api/workbench/goal-executions/{receipt['id']}"
            until(lambda: provider_calls)
            assert request(port, 'POST', detail + '/stop', {'confirm': True}, headers={'Authorization': ''})[0] == 401
            assert request(port, 'POST', detail + '/stop', {'confirm': False})[0] == 400
            status, stopped = request(port, 'POST', detail + '/stop', {'confirm': True})
            assert status == 200 and stopped['stop_requested']
            run_path = f"/api/workbench/collaboration-proposals/{receipt['planning_run_id']}"
            until(lambda: request(port, 'GET', run_path)[1]['state'] not in ('queued', 'running'))
            assert request(port, 'GET', detail)[1]['launch_id'] is None
        with running(tmp_path) as port:
            row = request(port, 'POST', path, payload)[1]
            assert row['stop_requested'] and row['launch_id'] is None
        assert len(provider_calls) == 1
        with store.connect() as db:
            assert db.execute('SELECT count(*) FROM project_launches').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM task_executions').fetchone()[0] == 0


def test_restart_applies_durable_stop_before_cli_dispatch(tmp_path, monkeypatch):
    store, source, payload, output = goal_input(tmp_path)
    original_tick = CLIController.tick
    monkeypatch.setattr(CLIController, 'tick', lambda self: None)
    path = f'/api/workbench/conversations/{source}/goal-executions'
    with endpoint(output) as (config, provider_calls):
        with running(tmp_path) as port:
            configure_cli(port, tmp_path, monkeypatch)
            configure_model(Settings(store), config, monkeypatch)
            status, receipt = request(port, 'POST', path, payload)
            assert status == 202
            detail = f"/api/workbench/goal-executions/{receipt['id']}"
            until(lambda: request(port, 'GET', detail)[1]['launch_id'])
            with store.connect() as db:
                db.execute("""CREATE TRIGGER injected_cancel BEFORE UPDATE ON task_executions
                    WHEN NEW.state='cancelled' BEGIN SELECT RAISE(ABORT,'stop write failed'); END""")
            assert request(port, 'POST', detail + '/stop', {'confirm': True})[0] == 500
            assert request(port, 'GET', detail)[1]['stop_requested']
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM task_executions WHERE state='queued'").fetchone()[0] == 3
            db.execute('DROP TRIGGER injected_cancel')
        observed = []

        def inspect_then_tick(self):
            with store.connect() as db:
                observed.append(db.execute("SELECT count(*) FROM task_executions WHERE state='queued'").fetchone()[0])
            return original_tick(self)

        monkeypatch.setattr(CLIController, 'tick', inspect_then_tick)
        with running(tmp_path) as port:
            until(lambda: observed)
            assert observed[0] == 0, 'CLI dispatch ran before durable goal stop recovery'
            stopped = request(port, 'GET', detail)[1]
            assert stopped['stop_requested']
            assert {i['execution']['state'] for i in stopped['batch']['items']} == {'cancelled'}
        assert len(provider_calls) == 1
