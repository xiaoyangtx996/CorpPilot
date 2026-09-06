"""Real host samples gate only new CLI claims; reservations follow actual ownership."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from test_workbench_cli_controller import fixture, result, until
from workbench import cli_controller, resource_admission
from workbench.cli_settings import CLISettings
from workbench.store import Store


def configured(controller, monkeypatch, **patch):
    config = {**controller.settings.resolve(), 'resource_admission_enabled': True,
              'host_reserve_memory_mb': 128, 'local_worker_memory_mb': 256, 'local_worker_cpus': 1, **patch}
    monkeypatch.setattr(controller.settings, 'resolve', lambda: dict(config))
    monkeypatch.setattr(controller.settings, 'get', lambda: {k:v for k,v in config.items() if k!='api_key'})
    return config


def held(monkeypatch):
    release, calls = Event(), []
    def runner(**kwargs):
        calls.append(kwargs['execution_id'])
        assert release.wait(5)
        return result()
    monkeypatch.setattr(cli_controller,'run_codex',runner)
    monkeypatch.setattr(cli_controller,'capture',lambda *args:[])
    return release, calls


@pytest.mark.parametrize('patch', [
    {'resource_admission_enabled':1}, {'resource_admission_enabled':'true'},
    {'host_reserve_memory_mb':-1}, {'host_reserve_memory_mb':1048577}, {'host_reserve_memory_mb':True},
    {'local_worker_memory_mb':127}, {'local_worker_memory_mb':1048577},
    {'local_worker_cpus':0}, {'local_worker_cpus':257}, {'local_worker_cpus':1.5}])
def test_strict_resource_settings_are_atomic(tmp_path, patch):
    settings = CLISettings(Store(tmp_path))
    before = settings.get()
    with pytest.raises(ValueError):settings.save(patch)
    assert settings.get() == before


def test_old_settings_default_off_and_new_settings_persist(tmp_path):
    settings = CLISettings(Store(tmp_path))
    assert settings.get()['resource_admission_enabled'] is False
    patch = {'resource_admission_enabled':True,'host_reserve_memory_mb':0,
             'local_worker_memory_mb':512,'local_worker_cpus':2}
    settings.save(patch)
    restored = CLISettings(Store(tmp_path)).get()
    assert all(restored[k]==v for k,v in patch.items())


@pytest.mark.skipif(os.name!='nt',reason='Native Windows host sample')
def test_native_host_sample_without_process_or_model_call():
    measured = resource_admission._sample_windows()
    assert measured['available_memory_mb'] > 0
    assert measured['cpu_count'] > 0


@pytest.mark.parametrize('metrics', [None, {'available_memory_mb':True,'cpu_count':8},
    {'available_memory_mb':1000,'cpu_count':None}, {'available_memory_mb':-1,'cpu_count':8}])
def test_unavailable_or_invalid_measurement_has_no_invented_capacity(monkeypatch, metrics):
    def sample():
        if metrics is None:raise OSError('private exception detail')
        return metrics
    monkeypatch.setattr(resource_admission,'_sample_windows',sample)
    value = resource_admission.snapshot({'resource_admission_enabled':True},{})
    assert value['available_memory_mb'] is value['cpu_count'] is None
    assert 'private exception' not in str(value)
    assert resource_admission.denial(value,{}, {'memory_mb':128,'cpus':1})


@pytest.mark.parametrize('constraint',['memory','cpu','probe'])
def test_resource_wait_preserves_original_queued_run_then_recovers(tmp_path,monkeypatch,constraint):
    _,_,controller,_,runs = fixture(tmp_path,monkeypatch)
    configured(controller,monkeypatch)
    release,calls = held(monkeypatch)
    metrics = {'available_memory_mb':128 if constraint=='memory' else 4096,
               'cpu_count':1}
    if constraint=='cpu':
        configured(controller,monkeypatch,local_worker_cpus=2)
    def sample():
        if constraint=='probe' and not metrics.get('recovered'):raise OSError('offline')
        return {k:metrics[k] for k in ('available_memory_mb','cpu_count')}
    monkeypatch.setattr(resource_admission,'_sample_windows',sample)
    try:
        for _ in range(3):controller.tick()
        assert controller.executions.get(runs[0]['id'])['state']=='queued'
        assert controller.reservations == {} and calls == []
        assert controller.status()['error']
        metrics.update(available_memory_mb=4096,cpu_count=4,recovered=True)
        until(controller,lambda:len(calls)==1)
        assert calls == [runs[0]['id']]
        assert controller.reservations[runs[0]['id']]['memory_mb']==256
    finally:
        release.set();controller.close()


def test_same_sample_cannot_overbook_memory_during_parallel_ticks(tmp_path,monkeypatch):
    _,_,controller,_,runs = fixture(tmp_path,monkeypatch,3)
    configured(controller,monkeypatch,max_concurrency=3)
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:{'available_memory_mb':640,'cpu_count':16})
    release,calls = held(monkeypatch)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:controller.tick(),range(8)))
        until(controller,lambda:len(calls)==2)
        assert len(controller.reservations)==2
        status=controller.status()['resource_admission']
        assert status['reserved_memory_mb']==512 and status['reserved_cpus']==2
        assert sum(controller.executions.get(r['id'])['state']=='queued' for r in runs)==1
    finally:
        release.set();controller.close()


def test_enabled_later_and_changed_config_do_not_shrink_active_reservations(tmp_path,monkeypatch):
    _,_,controller,_,runs = fixture(tmp_path,monkeypatch,3)
    config = configured(controller,monkeypatch,resource_admission_enabled=False,local_worker_cpus=2)
    metrics={'available_memory_mb':4096,'cpu_count':4}
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:dict(metrics))
    release,calls = held(monkeypatch)
    try:
        until(controller,lambda:len(calls)==2)
        waiting = next(run['id'] for run in runs if run['id'] not in calls)
        config.update(resource_admission_enabled=True,max_concurrency=3,local_worker_memory_mb=128,local_worker_cpus=1)
        controller.tick()
        assert len(calls)==2
        assert list(controller.reservations.values())==[{'memory_mb':256,'cpus':2}]*2
        metrics['cpu_count']=5
        until(controller,lambda:len(calls)==3)
        assert controller.reservations[waiting]=={'memory_mb':128,'cpus':1}
        assert controller.status()['resource_admission']['reserved_cpus']==5
    finally:
        release.set();controller.close()


def test_failed_claim_does_not_reserve_resources(tmp_path,monkeypatch):
    _,_,controller,_,_ = fixture(tmp_path,monkeypatch)
    configured(controller,monkeypatch)
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:{'available_memory_mb':4096,'cpu_count':8})
    monkeypatch.setattr(controller.executions,'claim',lambda _:False)
    try:
        controller.tick()
        assert not controller.reservations and not controller.active
    finally:controller.close()


def test_report_failure_keeps_reservation_until_durable_result(tmp_path,monkeypatch):
    _,_,controller,_,runs = fixture(tmp_path,monkeypatch)
    configured(controller,monkeypatch)
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:{'available_memory_mb':4096,'cpu_count':8})
    release,calls=held(monkeypatch)
    original=controller.executions.report
    def fail(*args,**kwargs):raise OSError('database unavailable')
    try:
        until(controller,lambda:len(calls)==1)
        monkeypatch.setattr(controller.executions,'report',fail)
        release.set()
        until(controller,lambda:controller.active[runs[0]['id']][2].done())
        controller.tick()
        assert controller.reservations[runs[0]['id']]=={'memory_mb':256,'cpus':1}
        assert controller.executions.get(runs[0]['id'])['state']=='running'
        monkeypatch.setattr(controller.executions,'report',original)
        until(controller,lambda:not controller.active)
        assert controller.reservations=={}
        assert controller.executions.get(runs[0]['id'])['state']=='awaiting_review'
    finally:
        release.set();monkeypatch.setattr(controller.executions,'report',original);controller.close()


def test_pressure_or_probe_failure_never_cancels_an_active_worker(tmp_path,monkeypatch):
    _,_,controller,_,runs=fixture(tmp_path,monkeypatch,2)
    config = configured(controller,monkeypatch,max_concurrency=1)
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:{'available_memory_mb':4096,'cpu_count':8})
    release,calls=held(monkeypatch)
    try:
        until(controller,lambda:len(calls)==1)
        config['max_concurrency'] = 2
        monkeypatch.setattr(resource_admission,'_sample_windows',lambda:(_ for _ in ()).throw(OSError()))
        controller.tick()
        assert controller.executions.get(runs[0]['id'])['state']=='running'
        assert not controller.active[runs[0]['id']][1].is_set()
        assert controller.executions.get(runs[1]['id'])['state']=='queued'
    finally:
        release.set();controller.close()


def test_docker_uses_existing_limits_and_idle_status_reads_host(tmp_path,monkeypatch):
    _,_,controller,_,runs=fixture(tmp_path,monkeypatch)
    config=configured(controller,monkeypatch,backend='docker',docker_cpus=3,docker_memory_mb=2048)
    monkeypatch.setattr(resource_admission,'_sample_windows',lambda:{'available_memory_mb':8192,'cpu_count':8})
    try:
        controller.executions.cancel(runs[0]['id'])
        assert controller.executions.pending()==[]
        assert resource_admission.requirements(config)=={'memory_mb':2048,'cpus':3}
        status=controller.status()['resource_admission']
        assert status['available_memory_mb']==8192 and status['cpu_count']==8
        assert status['reserved_memory_mb']==status['reserved_cpus']==0
        assert not controller.active
    finally:controller.close()
