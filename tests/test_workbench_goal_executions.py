"""Bounded mandates reuse one planner and fixed launch without expanding authority."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workbench_planning import setup
from workbench.cli_controller import CLIController
from workbench.goal_executions import GoalExecutions
from workbench.planning import PlanningError


@pytest.fixture
def goal(tmp_path, monkeypatch):
    store, planner, source, request, proposal = setup(tmp_path)
    worker = request['candidate_ids'][0]
    store.save_agent({'tools':['read','write','execute']}, worker)
    cli = CLIController(store)
    monkeypatch.setattr(cli.settings, 'resolve', lambda: {})
    service = GoalExecutions(store, cli)
    payload = {**request, 'candidate_ids':[request['agent_id'],worker], 'shared_brief':'ONLY AUTHORIZED BRIEF',
               'max_tasks':2, 'confirm_execution':True, 'confirm_handoff':True}
    try:
        yield store, service, source, payload, proposal
    finally:
        cli.close()


def complete(service, row, proposal):
    assert service.planning.runs.claim(row['planning_run_id'])
    return service.planning.runs.finish(row['planning_run_id'],json.dumps(proposal),'fixture',1,2)


def counts(store):
    with store.connect() as db:
        return {name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in
            ('runs','planning_requests','goal_executions','project_launches','task_executions','conversations','messages')}


def test_exact_goal_replay_and_brief_only_snapshot(goal):
    store, service, source, payload, proposal = goal
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _:service.create(source,copy.deepcopy(payload)), range(8)))
    assert len({r['id'] for r in receipts}) == 1
    row = receipts[0]
    assert service.planning.list(source) == []
    assert service.planning.runs.claim(row['planning_run_id'])
    snapshot = service.planning.runs.snapshot(row['planning_run_id'])
    assert [m['content'] for m in snapshot['messages']] == [payload['shared_brief']]
    assert 'TARGET ONLY' not in str(snapshot) and 'PRIVATE PRIOR' not in str(snapshot)
    assert '2' in snapshot['instructions']
    service.planning.runs.finish(row['planning_run_id'],json.dumps(proposal),'fixture',1,2)
    service.tick()
    final = service.get(row['id'])
    assert final['launch']['request_payload']['plan']['shared_brief'] == payload['shared_brief']
    assert final['launch']['request_payload']['confirm_handoff'] is True
    assert service.list(source)[0]['id'] == row['id']
    before = counts(store)
    for _ in range(3):
        service.tick()
        assert service.create(source,payload)['launch_id'] == final['launch_id']
    assert counts(store) == before
    assert before['runs'] == before['project_launches'] == before['task_executions'] == 1
    with pytest.raises(ValueError):
        service.create(source,{**payload,'confirm_handoff':False})


@pytest.mark.parametrize('delta', [
    {'confirm_execution':1},{'confirm_execution':False},{'confirm_handoff':'true'},
    {'max_tasks':True},{'max_tasks':0},{'max_tasks':17},{'candidate_ids':[]},
    {'candidate_ids':['unknown']},{'shared_brief':''},{'extra':True}])
def test_invalid_authority_has_no_side_effects(goal, delta):
    store, service, source, payload, _ = goal
    before = counts(store)
    with pytest.raises(ValueError):
        service.create(source,{**payload,**delta})
    assert counts(store) == before


def test_goal_insert_failure_rolls_back_planner_and_authority(goal):
    store, service, source, payload, _ = goal
    before = counts(store)
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_goal BEFORE INSERT ON goal_executions BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.create(source,payload)
    assert counts(store) == before


def test_immutable_authority_and_one_way_stop(goal):
    store, service, source, payload, _ = goal
    row = service.create(source,payload)
    service.stop_goal(row['id'],{'confirm':True})
    with store.connect() as db:
        for query in ('DELETE FROM goal_executions','INSERT OR REPLACE INTO goal_executions SELECT * FROM goal_executions',
                      "UPDATE goal_executions SET payload='{}'", "UPDATE goal_executions SET launch_request_id='other'",
                      'UPDATE goal_executions SET stop_requested=0'):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(query)
    assert service.get(row['id'])['state'] == 'stopped'
    assert service.get(row['id'])['planning']['state'] == 'cancelled'
    service.tick()
    assert counts(store)['project_launches'] == 0


@pytest.mark.parametrize('state',['failed','unknown','cancelled'])
def test_terminal_planning_does_not_launch_or_retry(goal,state):
    store, service, source, payload, _ = goal
    row = service.create(source,payload)
    if state == 'cancelled':service.planning.runs.cancel(row['planning_run_id'])
    else:service.planning.runs.fail(row['planning_run_id'],'test',state=state)
    for _ in range(3):service.tick()
    assert service.get(row['id'])['launch'] is None
    assert counts(store)['runs'] == 1 and counts(store)['project_launches'] == 0


def test_too_many_tasks_rejected_without_launch(goal):
    store, service, source, payload, proposal = goal
    row = service.create(source,{**payload,'max_tasks':1})
    proposal['tasks'].append({**proposal['tasks'][0],'key':'b'})
    with pytest.raises(PlanningError):complete(service,row,proposal)
    service.planning.runs.fail(row['planning_run_id'],'invalid')
    service.tick()
    assert service.get(row['id'])['launch'] is None


@pytest.mark.parametrize('change',['source-archived','unused-candidate-disabled','worker-disabled'])
def test_revocation_before_launch_blocks_once(goal,change):
    store, service, source, payload, proposal = goal
    if change == 'unused-candidate-disabled':
        extra = store.agents()[2]['id']
        payload['candidate_ids'].append(extra)
    row = service.create(source,payload)
    complete(service,row,proposal)
    if change == 'source-archived':store.save_conversation({'archived':True},source)
    elif change == 'unused-candidate-disabled':store.save_agent({'enabled':False},extra)
    else:store.save_agent({'enabled':False},proposal['tasks'][0]['agent_id'])
    service.tick()
    assert service.get(row['id'])['state'] == 'failed'
    before = counts(store)
    service.tick()
    assert counts(store) == before and before['project_launches'] == 0


def test_actual_launch_transaction_rechecks_unused_candidate(goal,monkeypatch):
    store, service, source, payload, proposal = goal
    extra = store.agents()[2]['id']
    payload['candidate_ids'].append(extra)
    row = service.create(source,payload)
    complete(service,row,proposal)
    launch = service.cli.launch_project
    def revoke_then_launch(*args):
        store.save_agent({'enabled':False},extra)
        return launch(*args)
    monkeypatch.setattr(service.cli,'launch_project',revoke_then_launch)
    service.tick()
    assert service.get(row['id'])['launch_id'] is None
    assert counts(store)['project_launches'] == counts(store)['task_executions'] == 0


def test_committed_launch_link_write_failure_recovers_and_stop_uses_fixed_batch(goal):
    store, service, source, payload, proposal = goal
    row = service.create(source,payload)
    complete(service,row,proposal)
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_link BEFORE UPDATE OF launch_id ON goal_executions BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):service.tick()
    visible = service.get(row['id'])
    assert visible['launch_id'] and counts(store)['project_launches'] == 1
    with store.connect() as db:db.execute('DROP TRIGGER fail_link')
    restored = GoalExecutions(store,service.cli)
    stopped = restored.stop_goal(row['id'],{'confirm':True})
    assert stopped['launch_id'] == visible['launch_id']
    assert all(i['execution']['state']=='cancelled' for i in stopped['batch']['items'])
    restored.tick()
    assert counts(store)['project_launches'] == counts(store)['runs'] == 1


def test_stop_after_model_started_preserves_truth_and_never_launches(goal):
    store, service, source, payload, proposal = goal
    row = service.create(source,payload)
    assert service.planning.runs.claim(row['planning_run_id'])
    stopped = service.stop_goal(row['id'],{'confirm':True})
    assert stopped['state']=='stop_requested' and stopped['planning']['state']=='running'
    with pytest.raises(PlanningError):
        service.planning.runs.finish(row['planning_run_id'],json.dumps(proposal),'fixture',1,2)
    service.planning.runs.fail(row['planning_run_id'],'Stopped authority')
    service.tick()
    assert service.get(row['id'])['state']=='stopped'
    assert counts(store)['project_launches'] == 0


def test_other_explicit_plan_using_visible_key_is_not_adopted_or_stopped(goal):
    store, service, source, payload, proposal = goal
    row = service.create(source,payload)
    complete(service,row,proposal)
    other = {'plan':{**proposal,'title':'Different explicitly approved plan',
                'shared_brief':payload['shared_brief'],'source_message_id':payload['source_message_id'],
                'coordinator_id':payload['agent_id'],'request_id':row['launch_request_id']},
             'confirm_execution':True,'confirm_handoff':True}
    launch = service.cli.launch_project(source,other)
    service.tick()
    assert service.get(row['id'])['state'] == 'unknown'
    stopped = service.stop_goal(row['id'],{'confirm':True})
    assert stopped['launch_id'] is None and stopped['launch'] is None
    assert all(i['execution']['state']=='queued' for i in service.cli.project_executions.get(launch['batch']['id'])['items'])


def test_mismatched_launch_stop_still_cancels_own_queued_planning(goal):
    store, service, source, payload, proposal = goal
    row = service.create(source, payload)
    launch = service.cli.launch_project(source, {
        'plan': {**proposal, 'request_id': row['launch_request_id'],
                 'source_message_id': payload['source_message_id'], 'coordinator_id': payload['agent_id']},
        'confirm_execution': True, 'confirm_handoff': True})
    stopped = service.stop_goal(row['id'], {'confirm': True})
    assert stopped['state'] == 'unknown' and stopped['launch'] is None
    assert stopped['planning']['state'] == 'cancelled'
    before = counts(store)
    service.tick()
    assert counts(store) == before
    assert all(i['execution']['state'] == 'queued'
               for i in service.cli.project_executions.get(launch['batch']['id'])['items'])
