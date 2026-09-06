"""Delegation belongs to the coordinator and is checked only for new work."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench.store import Store
from workbench.planning import Planning
from workbench.collaboration import Collaboration
from workbench.executions import Executions
from workbench.project_launches import ProjectLaunches


def setup(tmp_path):
    store = Store(tmp_path)
    coordinator, worker = store.agents()[:2]
    store.save_agent({'tools': ['read', 'delegate']}, coordinator['id'])
    store.save_agent({'tools': ['read', 'write', 'execute']}, worker['id'])
    room = store.save_conversation({'type': 'dm', 'title': 'Owner', 'member_ids': [coordinator['id']]})
    source = store.send_message(room['id'], {'request_id': 'source', 'content': 'Owner goal'})
    planning = Planning(store)
    request = dict(request_id='plan', source_message_id=source['id'], agent_id=coordinator['id'], candidate_ids=[worker['id']])
    proposal = dict(title='Project', shared_brief='Explicit brief', tasks=[dict(
        key='a', title='Task', scope='Work', acceptance='Verified', agent_id=worker['id'], depends_on=[])])
    plan = dict(proposal, request_id='launch', source_message_id=source['id'], coordinator_id=coordinator['id'])
    return store, planning, room['id'], request, proposal, plan


def revoke(store, request):
    store.save_agent({'tools': ['read']}, request['agent_id'])


def test_new_planning_and_project_reject_without_partial_records(tmp_path):
    store, planning, room, request, _, plan = setup(tmp_path)
    collaboration = Collaboration(store)
    revoke(store, request)
    with pytest.raises(PermissionError, match='delegate'):
        planning.create(room, request)
    with pytest.raises(PermissionError, match='delegate'):
        collaboration.create(room, plan)
    assert planning.list(room) == [] and collaboration.list(room) == []
    with store.connect() as db:
        for table in ('runs', 'planning_requests', 'tasks', 'collaboration_receipts'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM conversations').fetchone()[0] == 1


def test_claim_rechecks_but_queued_receipt_remains_readable(tmp_path):
    store, planning, room, request, _, _ = setup(tmp_path)
    run = planning.create(room, request)
    revoke(store, request)
    assert planning.create(room, request) == run
    with pytest.raises(ValueError, match='不同规划'):
        planning.create(room, dict(request, candidate_ids=[request['agent_id']]))
    assert not planning.runs.claim(run['id'])
    assert planning.get(run['id'])['state'] == 'failed'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM skill_input_snapshots').fetchone()[0] == 0


@pytest.mark.parametrize('operation', ['snapshot', 'finish'])
def test_running_planner_rechecks_before_input_or_publication(tmp_path, operation):
    store, planning, room, request, proposal, _ = setup(tmp_path)
    run = planning.create(room, request)
    assert planning.runs.claim(run['id'])
    revoke(store, request)
    with pytest.raises(PermissionError, match='delegate'):
        if operation == 'snapshot':
            planning.runs.snapshot(run['id'])
        else:
            planning.runs.finish(run['id'], json.dumps(proposal), 'fixture', 1, 2)
    assert planning.get(run['id'])['proposal'] is None
    assert len(store.messages(room)) == 1


def test_finished_proposal_survives_revoke_but_new_launch_is_atomic_denial(tmp_path):
    store, planning, room, request, proposal, plan = setup(tmp_path)
    launch = ProjectLaunches(store)
    run = planning.create(room, request)
    assert planning.runs.claim(run['id'])
    planning.runs.finish(run['id'], json.dumps(proposal), 'fixture', 1, 2)
    saved = planning.get(run['id'])
    revoke(store, request)
    assert planning.create(room, request) == saved
    assert Planning(Store(tmp_path)).get(run['id']) == saved
    assert planning.runs.finish(run['id'], '{}', 'fixture', 1, 2)['state'] == 'completed'
    with pytest.raises(PermissionError, match='delegate'):
        launch.create(room, dict(plan=plan, confirm_execution=True))
    assert launch.list(room) == []
    with store.connect() as db:
        for table in ('tasks', 'collaboration_receipts', 'task_executions', 'project_launches'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0


def test_started_batch_and_original_receipts_survive_coordinator_revoke(tmp_path):
    store, _, room, request, _, plan = setup(tmp_path)
    launch = ProjectLaunches(store)
    payload = dict(plan=plan, confirm_execution=True)
    receipt = launch.create(room, payload)
    revoke(store, request)
    assert launch.create(room, payload) == receipt
    assert launch.get(receipt['id']) == receipt
    collaboration = Collaboration(store)
    assert collaboration.create(room, plan) == collaboration.get(receipt['collaboration']['id'])
    execution = receipt['batch']['tasks'][0]['execution_id']
    runs = Executions(store)
    assert runs.claim(execution)
    snapshot = runs.snapshot(execution, include_artifacts=True)
    assert snapshot['agent']['id'] == plan['tasks'][0]['agent_id']
    assert 'delegate' not in snapshot['agent']['tools']


def test_regular_reply_does_not_need_delegate(tmp_path):
    store, planning, room, request, _, _ = setup(tmp_path)
    revoke(store, request)
    reply = planning.runs.create(room, {key: request[key] for key in ('request_id', 'source_message_id', 'agent_id')})
    assert planning.runs.claim(reply['id'])
    assert planning.runs.snapshot(reply['id'])['agent']['tools'] == ['read']
    assert planning.runs.finish(reply['id'], 'A normal reply', 'fixture', 1, 2)['state'] == 'completed'


@pytest.mark.parametrize('after_planning', [False, True])
def test_goal_new_authority_or_launch_rechecks_coordinator(tmp_path, monkeypatch, after_planning):
    from workbench.cli_controller import CLIController
    from workbench.goal_executions import GoalExecutions
    store, _, room, request, proposal, _ = setup(tmp_path)
    cli = CLIController(store)
    monkeypatch.setattr(cli.settings, 'resolve', lambda: {})
    service = GoalExecutions(store, cli)
    payload = dict(request, candidate_ids=[request['agent_id'], *request['candidate_ids']],
                   shared_brief='Bounded goal', max_tasks=1, confirm_execution=True, confirm_handoff=False)
    try:
        if after_planning:
            goal = service.create(room, payload)
            assert service.planning.runs.claim(goal['planning_run_id'])
            service.planning.runs.finish(goal['planning_run_id'], json.dumps(proposal), 'fixture', 1, 2)
        revoke(store, request)
        if after_planning:
            service.tick()
            failed = service.get(goal['id'])
            assert failed['state'] == 'failed'
            assert failed['launch_id'] is None
            assert service.create(room, payload) == failed
        else:
            with pytest.raises(PermissionError, match='delegate'):
                service.create(room, payload)
            assert service.list(room) == []
        with store.connect() as db:
            for table in ('tasks', 'task_executions', 'project_launches'):
                assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    finally:
        cli.close()


def test_retrospective_does_not_need_delegate(tmp_path):
    from test_workbench_retrospectives import fixture, output
    store, _, task, _, _, service, payload = fixture(tmp_path)
    identity = task['agent_id']
    store.save_agent({'tools': ['read', 'write', 'execute']}, identity)
    run = service.create('agent', identity, payload)
    assert service.runs.claim(run['id'])
    assert 'delegate' not in service.runs.snapshot(run['id'])['agent']['tools']
    assert service.runs.finish(run['id'], output(payload), 'fixture', 1, 2)['state'] == 'completed'
