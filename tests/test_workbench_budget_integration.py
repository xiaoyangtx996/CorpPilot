"""Budget gates on real controllers/HTTP/backup; no paid provider or CLI."""
import time

from test_workbench_api import running, request
from test_workbench_controller import configure, until
from test_workbench_provider import endpoint
from test_workbench_cli_controller import fixture, result, until as cli_until
from workbench.budgets import Budgets, BudgetDenied
from workbench.controller import ReplyController
from workbench.settings import Settings
from workbench.store import Store
from workbench.runs import Runs
from workbench.executions import Executions
from workbench import cli_controller
from workbench.backup import backup, restore


def policy(total=1_000_000):
    return dict(enabled=True, total_micro_usd=total,
                model_reserve_micro_usd=1_000_000, cli_reserve_micro_usd=1_000_000)


def make_run(store, runs, key='run', actor=None):
    actor = actor or store.agents()[0]['id']
    room = store.save_conversation({'type': 'dm', 'title': 'Budget fixture', 'member_ids': [actor]})
    msg = store.send_message(room['id'], {'content': 'Local budget test', 'request_id': 'msg'})
    run = runs.create(room['id'], {'agent_id': actor, 'source_message_id': msg['id'], 'request_id': key})
    return run


def test_owner_http_budget_records_and_restart(tmp_path):
    with running(tmp_path) as port:
        assert request(port, 'GET', '/api/workbench/budget-settings', headers={'Authorization': ''})[0] == 401
        assert request(port, 'PATCH', '/api/workbench/budget-settings', policy(), headers={'Origin': 'https://foreign.example'})[0] == 400
        assert request(port, 'PATCH', '/api/workbench/budget-settings', {'enabled': True})[0] == 400
        status, saved = request(port, 'PATCH', '/api/workbench/budget-settings', policy())
        assert status == 200 and saved['reserved_micro_usd'] == 0
        store = Store(tmp_path); runs = Runs(store); row = make_run(store, runs)
        assert runs.claim(row['id'])
        status, records = request(port, 'GET', '/api/workbench/budget-reservations')
        assert status == 200 and len(records) == 1
        assert records[0]['run_id'] == row['id']
        assert request(port, 'GET', '/api/workbench/agents/' + row['agent_id'] + '/budget-reservations') == (200, records)
        other = next(a['id'] for a in store.agents() if a['id'] != row['agent_id'])
        assert request(port, 'GET', '/api/workbench/agents/' + other + '/budget-reservations') == (200, [])
        assert request(port, 'GET', '/api/workbench/agents/missing/budget-reservations')[0] == 404
        assert request(port, 'GET', '/api/workbench/budget-reservations', headers={'Authorization': ''})[0] == 401
        assert request(port, 'POST', '/api/workbench/budget-reservations', {})[0] in (404, 405)
        before = Budgets(store).get()
        assert before['reserved_micro_usd'] == 1_000_000 and before['available_micro_usd'] == 0
    with running(tmp_path) as port:
        assert request(port, 'GET', '/api/workbench/budget-reservations') == (200, records)
        assert request(port, 'GET', '/api/workbench/budget-settings') == (200, before)
        assert Runs(Store(tmp_path)).get(row['id'])['state'] == 'unknown'
        assert request(port, 'PATCH', '/api/workbench/budget-settings', {**policy(), 'enabled': False})[1]['reserved_micro_usd'] == 1_000_000


def test_real_model_queue_budget_denial_does_not_burn_rpm(tmp_path, monkeypatch):
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Local reply'}}]}
    with endpoint(response) as (config, calls):
        store = Store(tmp_path); settings = Settings(store)
        configure(settings, config, monkeypatch); settings.save({'rpm': 2})
        runs = Runs(store); budget = Budgets(store); budget.save(policy())
        first, second = make_run(store, runs, 'first'), make_run(store, runs, 'second')
        controller = ReplyController(store, settings)
        try:
            until(lambda: sum(runs.get(r['id'])['state'] == 'completed' for r in (first, second)) == 1)
            until(lambda: '预算' in controller.status()['error'])
            time.sleep(0.7)  # several actual dispatcher polls, not an assumed lack of dispatch
            assert len(calls) == 1 and len(controller.monitor._admissions) == 1
            assert budget.get()['reserved_micro_usd'] == 1_000_000
            budget.save(policy(2_000_000))
            until(lambda: all(runs.get(r['id'])['state'] == 'completed' for r in (first, second)))
            assert len(calls) == 2 and len(controller.monitor._admissions) == 2
            assert budget.get()['reserved_micro_usd'] == 2_000_000
        finally:
            controller.close()


def test_cli_and_model_share_budget_and_cli_failure_keeps_reservation(tmp_path, monkeypatch):
    store, _, controller, _, executions = fixture(tmp_path, monkeypatch)
    budget = Budgets(store); budget.save(policy())
    runs = Runs(store); model = make_run(store, runs)
    assert runs.claim(model['id'])
    runs.fail(model['id'], 'Local request ended without bill', state='failed')
    calls = []
    monkeypatch.setattr(cli_controller, 'run_codex', lambda **kw: calls.append(kw) or result(1, False))
    try:
        for _ in range(3): controller.tick()
        assert not calls and controller.executions.get(executions[0]['id'])['state'] == 'queued'
        assert '预算' in controller.status()['error']
        budget.save(policy(2_000_000))
        cli_until(controller, lambda: controller.executions.get(executions[0]['id'])['state'] == 'failed')
        assert len(calls) == 1 and budget.get()['reserved_micro_usd'] == 2_000_000
        assert {r['kind'] for r in budget.list()} == {'model', 'cli'}
    finally:
        controller.close()


def test_budget_reservations_survive_actual_backup_restore(tmp_path):
    source = tmp_path / 'source'; store = Store(source)
    runs = Runs(store); Executions(store); budget = Budgets(store); budget.save(policy())
    row = make_run(store, runs); assert runs.claim(row['id'])
    runs.fail(row['id'], 'Unknown external invoice', state='unknown')
    records, before = budget.list(), budget.get()
    backup(source, tmp_path / 'bundle'); restore(tmp_path / 'bundle', tmp_path / 'restored')
    restored = Store(tmp_path / 'restored'); restored_runs = Runs(restored); ledger = Budgets(restored)
    assert ledger.list() == records and ledger.get() == before
    new = make_run(restored, restored_runs, 'later', actor=restored.agents()[1]['id'])
    try:
        restored_runs.claim(new['id'])
    except BudgetDenied:
        pass
    else:
        raise AssertionError('Restored reservation must still block new dispatch')
    assert restored_runs.get(new['id'])['state'] == 'queued'


def test_rpm_callback_rejection_exception_and_commit_timestamp(tmp_path, monkeypatch):
    import pytest
    from runtime import traffic_monitor
    monitor = traffic_monitor.TrafficMonitor(tmp_path / 'unused.jsonl')
    now = [0.0]
    monkeypatch.setattr(traffic_monitor.time, 'monotonic', lambda: now[0])
    assert not monitor.reserve_call(1, lambda: False)
    def denied(): raise BudgetDenied('Budget denied')
    with pytest.raises(BudgetDenied): monitor.reserve_call(1, denied)
    assert not monitor._admissions
    def delayed_claim():
        now[0] = 30.0
        return True
    assert monitor.reserve_call(1, delayed_claim)
    assert list(monitor._admissions) == [30.0]
    now[0] = 60.0
    assert not monitor.reserve_call(1)
    now[0] = 90.0
    assert monitor.reserve_call(1)  # original API still works
