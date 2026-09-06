"""Single observed CLI turn receipts are evidence, never total cost or exit proof."""
import json
import sqlite3

import pytest

from test_workbench_executions import setup, request
from test_workbench_cli_controller import fixture, until, result
from workbench import cli_controller
from workbench.cli import parse_result
from workbench.executions import Executions
from workbench.activity import get as activity
from workbench.reviews import Reviews

USAGE = {'input_tokens':7,'output_tokens':None,'cached_input_tokens':0}


def process(reason='exited', code=0, tail=None):
    events = [{'type':'item.completed','item':{'type':'agent_message','text':'ok'}},
              {'type':'turn.completed','usage':USAGE}]
    if tail is not None: events.append(tail)
    return {'reason':reason,'exit_code':code,'stdout':'\n'.join(json.dumps(e) for e in events).encode()}


@pytest.mark.parametrize('reason,code',[('exited',0),('exited',1),('timeout',None),('cancelled',None),('unknown',None),('output_limit',None)])
def test_failed_process_keeps_observed_turn_without_success(reason,code):
    value = parse_result(process(reason,code),'synthetic-key')
    assert value['usage'] == USAGE
    assert value['success'] is (reason=='exited' and code==0)
    assert value['exit_code'] == code


def test_multiple_or_broken_turn_stream_is_not_aggregated():
    value = parse_result(process(tail={'type':'turn.completed','usage':USAGE}),'synthetic-key')
    assert value['usage'] is None and value['success'] is False
    broken = process('timeout',None)
    broken['stdout'] += b'\n{"unfinished"'
    assert parse_result(broken,'synthetic-key')['usage'] is None
    failed = parse_result(process(tail={'type':'turn.failed','error':'secret'}),'synthetic-key')
    assert failed['usage'] == USAGE and failed['success'] is False
    assert 'secret' not in failed['summary']


def test_receipt_identity_immutability_and_read_paths(tmp_path):
    store,_,executions,task = setup(tmp_path)
    Reviews(store)
    from workbench.runs import Runs
    Runs(store)
    run = executions.create(task['id'],request())
    assert run['usage'] is None
    with pytest.raises(ValueError): executions.record_usage(run['id'],1,1,USAGE)
    executions.claim(run['id'])
    executions.record_usage(run['id'],1,1,USAGE)
    executions.record_usage(run['id'],1,1,USAGE)
    with pytest.raises(ValueError): executions.record_usage(run['id'],2,1,USAGE)
    with pytest.raises(ValueError): executions.record_usage(run['id'],1,1,{**USAGE,'input_tokens':8})
    with pytest.raises(ValueError): executions.record_usage(run['id'],1,1,{**USAGE,'input_tokens':True})
    assert executions.create(task['id'],request())['usage'] == USAGE
    assert executions.list(task['id'])[0]['usage'] == USAGE
    assert activity(store,task['agent_id'])['executions']['items'][0]['usage'] == USAGE
    executions.recover()
    reopened = Executions(store)
    reopened.record_usage(run['id'],1,1,USAGE)
    assert reopened.get(run['id'])['state'] == 'unknown'
    with store.connect() as db:
        for sql in ("DELETE FROM execution_usage", "UPDATE execution_usage SET usage='{}'", "INSERT OR REPLACE INTO execution_usage SELECT * FROM execution_usage"):
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_old_database_initializes_empty_receipt_table_without_changing_run(tmp_path):
    store,_,executions,task = setup(tmp_path)
    run = executions.create(task['id'],request())
    with store.connect() as db:
        before = dict(db.execute('SELECT * FROM task_executions').fetchone())
        db.execute('DROP TABLE execution_usage')
    restored = Executions(store)
    assert restored.get(run['id'])['usage'] is None
    with store.connect() as db: assert dict(db.execute('SELECT * FROM task_executions').fetchone()) == before


@pytest.mark.parametrize('docker',[False,True])
def test_capture_failure_retains_usage(tmp_path,monkeypatch,docker):
    store,_,ctl,tasks,runs = fixture(tmp_path,monkeypatch)
    calls = []
    if docker:
        config = ctl.settings.resolve() | {'backend':'docker','docker_executable':'C:/fake/docker.exe','docker_image':'sha256:'+'a'*64,'docker_cpus':1,'docker_memory_mb':1024,'docker_pids_limit':128}
        monkeypatch.setattr(ctl.settings,'resolve',lambda:config)
    def runner(**kwargs): calls.append(kwargs['execution_id']); return result() | {'usage':USAGE}
    monkeypatch.setattr(cli_controller,'run_docker' if docker else 'run_codex',runner)
    def capture(*args):
        assert ctl.executions.get(runs[0]['id'])['usage'] == USAGE
        raise ValueError('capture rejected')
    monkeypatch.setattr(cli_controller,'capture',capture)
    try:
        until(ctl,lambda:ctl.executions.get(runs[0]['id'])['state']=='failed')
        assert ctl.executions.get(runs[0]['id'])['usage'] == USAGE
        assert len(calls)==1
    finally: ctl.close()


def test_receipt_write_retry_retains_future_reservation_and_never_reruns(tmp_path,monkeypatch):
    _,_,ctl,_,runs = fixture(tmp_path,monkeypatch)
    calls = []
    def runner(**kwargs): calls.append(kwargs['execution_id']); return result() | {'usage':USAGE}
    monkeypatch.setattr(cli_controller,'run_codex',runner)
    saved = ctl.executions.record_usage
    def failed(*args): raise sqlite3.OperationalError('temporary write failure')
    monkeypatch.setattr(ctl.executions,'record_usage',failed)
    try:
        until(ctl,lambda:bool(ctl.active) and next(iter(ctl.active.values()))[2].done())
        for _ in range(3): ctl.tick()
        assert runs[0]['id'] in ctl.active and runs[0]['id'] in ctl.reservations
        assert ctl.executions.get(runs[0]['id'])['usage'] is None
        assert len(calls)==1
        monkeypatch.setattr(ctl.executions,'record_usage',saved)
        until(ctl,lambda:not ctl.active)
        assert ctl.executions.get(runs[0]['id'])['usage'] == USAGE
        assert ctl.executions.get(runs[0]['id'])['state']=='failed'
        assert ctl.executions.get(runs[0]['id'])['exit_code']==0
        assert len(calls)==1
    finally:
        monkeypatch.setattr(ctl.executions,'record_usage',saved)
        ctl.close()


def test_cached_tokens_cannot_exceed_reported_input(tmp_path):
    value = process()
    text = value['stdout'].decode().replace('"cached_input_tokens": 0','"cached_input_tokens": 8')
    value['stdout'] = text.encode()
    assert parse_result(value,'synthetic-key')['usage']['cached_input_tokens'] is None
    _,_,executions,task = setup(tmp_path)
    run = executions.create(task['id'],request())
    executions.claim(run['id'])
    with pytest.raises(ValueError,match='缓存'):
        executions.record_usage(run['id'],1,1,{**USAGE,'cached_input_tokens':8})
    assert executions.get(run['id'])['usage'] is None


def test_report_usage_retains_late_callback_ignore_and_independent_commit(tmp_path):
    _,_,executions,task = setup(tmp_path)
    run = executions.create(task['id'],request())
    executions.claim(run['id'])
    assert executions.report(run['id'],2,1,0,'late',success=True,usage={'invalid':True})['usage'] is None
    with pytest.raises(ValueError):
        executions.report(run['id'],1,1,0,'captured',success=True,usage=USAGE,artifacts=[{'invalid':True}])
    assert executions.get(run['id'])['usage'] == USAGE
    assert executions.get(run['id'])['state'] == 'running'
    saved = executions.report(run['id'],1,1,1,'failed',usage=USAGE)
    assert saved['state'] == 'failed'
    assert executions.report(run['id'],1,1,0,'late',success=True,usage={'invalid':True}) == saved
