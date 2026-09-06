"""Persistent Owner integration authorization; no native or paid process is invoked."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from test_workbench_code_reviews import setup, send
from workbench import artifacts
from workbench.code_integrations import CodeIntegrations
from workbench.reviews import Reviews


def ready(tmp_path, monkeypatch, *, approved=True, report=True, retry=False):
    values = setup(tmp_path, monkeypatch)
    store, tasks, executions, room, agents, task, source, repository, reviews = values
    review = send(reviews, source['id']); identity = review['initial_execution_id']
    assert executions.claim(identity)
    if retry:
        executions.report(identity, 1, 1, 1, 'Failed review')
        identity = executions.create(review['task_id'], dict(request_id='retry-review', expected_version=1,
            previous_execution_id=identity, reconciliation_note='Owner authorizes review retry'))['id']
        assert executions.claim(identity)
    run = executions.get(identity)
    executions.snapshot(identity, include_artifacts=True)
    executions.report(identity, run['attempt'], 1, 0, 'Review report', success=True,
        artifacts=[dict(path='review.md' if report else 'other.md', data=b'Reviewed exact patch')])
    if approved:
        Reviews(store).save(identity, dict(request_id='approve-review', expected_version=1, decision='approved', note='Allow integration',
            artifact_ids=[a['id'] for a in artifacts.list_for(store, identity)]))
    return (*values, identity, CodeIntegrations(store))


def payload(service, project, source, **changes):
    preview = service.preview(project, dict(source_execution_ids=[source]))
    return dict(request_id='integrate', source_execution_ids=[source], fingerprint=preview['fingerprint'],
        previous_integration_id=None, reconciliation_note='', confirm=True) | changes


def native(receipt):
    sources = receipt['authorization_snapshot']['sources']; fixed = sources[0]['source_snapshot']
    repo = fixed['repository']['snapshot']
    return dict(source_path=repo['source_path'], base_commit=repo['commit'], base_tree=repo['tree'], branch=receipt['branch'],
        commit='c'*40, tree=repo['tree'], files=1, total_bytes=6, conversation_id=receipt['project_id'],
        integration_agent_id=repo['integration_agent_id'], repository_revision=fixed['repository']['revision'],
        source_execution_ids=receipt['request_payload']['source_execution_ids'],
        patch_sha256s=[s['source_snapshot']['manifest']['patch_sha256'] for s in sources])


def test_preview_inputs_readonly_and_latest_review_retry_frozen(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, review_id, service = ready(tmp_path, monkeypatch, retry=True)
    with store.connect() as db: before = list(db.iterdump())
    preview = service.preview(room['id'], dict(source_execution_ids=[source['id']]))
    assert not preview['blockers'] and preview['latest_integration_id'] is None
    assert preview['snapshot']['sources'][0]['review_execution']['id'] == review_id
    assert preview['snapshot']['sources'][0]['review_execution']['attempt'] == 2
    with store.connect() as db: assert list(db.iterdump()) == before
    receipt = service.create(room['id'], payload(service, room['id'], source['id']))
    with store.connect() as db: before = list(db.iterdump())
    inputs = service.inputs(receipt['id'])
    assert not inputs['destination'].exists()
    assert inputs['destination'] == store.data_dir / 'code-integrations' / receipt['id'] / 'repository'
    assert inputs['changes'][0]['patch'] == b''
    assert service.authorize(receipt['id']) == receipt
    with store.connect() as db: assert list(db.iterdump()) == before
    assert service.request(room['id'], 'integrate') == receipt
    assert service.request(room['id'], 'absent') is None
    assert service.list(room['id']) == [receipt]


@pytest.mark.parametrize('option', [dict(approved=False), dict(report=False)])
def test_review_must_be_approved_with_real_report(tmp_path, monkeypatch, option):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch, **option)
    assert service.preview(room['id'], dict(source_execution_ids=[source['id']]))['blockers']
    with pytest.raises(ValueError): service.create(room['id'], payload(service, room['id'], source['id']))
    assert service.list(room['id']) == []


def test_exact_concurrent_replay_and_changed_key_cas(tmp_path, monkeypatch):
    store, _, _, room, agents, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    value = payload(service, room['id'], source['id'])
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: service.create(room['id'], value), range(2)))
    assert results[0] == results[1]
    store.save_agent({'enabled': False}, agents[1]['id'])
    assert CodeIntegrations(store).create(room['id'], value) == results[0]
    with pytest.raises(ValueError): service.create(room['id'], value | {'fingerprint': 'f'*64})
    with pytest.raises(ValueError): service.create(room['id'], value | {'request_id': 'other'})
    assert len(service.list(room['id'])) == 1


@pytest.mark.parametrize('change', ['disabled', 'archive', 'source_revision', 'review_retry'])
def test_authorization_rechecked_before_native_and_completion(tmp_path, monkeypatch, change):
    store, tasks, executions, room, agents, task, source, _, reviews, review_id, service = ready(tmp_path, monkeypatch)
    receipt = service.create(room['id'], payload(service, room['id'], source['id']))
    result = native(receipt)
    if change == 'disabled': store.save_agent({'enabled': False}, agents[1]['id'])
    elif change == 'archive': store.save_conversation({'archived': True}, room['id'])
    elif change == 'source_revision':
        tasks.revise(task['id'], dict(expected_version=1, **{k: task[k] for k in ('title','scope','acceptance','agent_id')}) | {'title': 'Changed'})
    else:
        review = reviews.get(source['id'])
        executions.create(review['task_id'], dict(request_id='new-review', expected_version=1, previous_execution_id=review_id, reconciliation_note='Owner retries review'))
    with pytest.raises(ValueError): service.authorize(receipt['id'])
    with pytest.raises(ValueError): service.inputs(receipt['id'])
    ended = service.finish(receipt['id'], 'completed', result, 'Native completed')
    assert ended['state'] == 'failed' and ended['result'] == result
    assert service.finish(receipt['id'], 'completed', result, 'Native completed') == ended
    with pytest.raises(ValueError): service.finish(receipt['id'], 'failed', result, 'Changed callback')


def test_recover_unknown_no_replay_declaration_then_explicit_new_request(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    value = payload(service, room['id'], source['id']); first = service.create(room['id'], value)
    assert service.stop(first['id'])['state'] == 'stopping'
    with pytest.raises(ValueError): service.inputs(first['id'])
    restarted = CodeIntegrations(store)
    assert restarted.get(first['id'])['state'] == 'stopping'  # Constructor does not recover/dispatch.
    restarted.recover(); assert restarted.unresolved()
    unknown = restarted.get(first['id']); assert unknown['state'] == 'unknown'
    assert restarted.create(room['id'], value) == unknown
    assert restarted.preview(room['id'], dict(source_execution_ids=[source['id']]))['blockers']
    declaration = dict(request_id='check', process_stopped=True, effects_checked=True, note='Checked native processes and retained directory')
    checked = restarted.reconcile(first['id'], declaration)
    assert checked['state'] == 'unknown' and checked['reconciliation']['source'] == 'owner_declared'
    assert not restarted.unresolved() and restarted.reconcile(first['id'], declaration) == checked
    with pytest.raises(ValueError): restarted.reconcile(first['id'], declaration | {'note': 'different'})
    second = restarted.create(room['id'], payload(restarted, room['id'], source['id'], request_id='again', previous_integration_id=first['id'], reconciliation_note='Authorized independent new checkout'))
    assert second['id'] != first['id'] and second['destination_relative'] != first['destination_relative']
    assert restarted.preview(room['id'], dict(source_execution_ids=[source['id']]))['latest_integration_id'] == second['id']


def test_stop_late_success_idempotent_and_terminal_immutable(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    receipt = service.create(room['id'], payload(service, room['id'], source['id']))
    service.stop(receipt['id']); result = native(receipt)
    ended = service.finish(receipt['id'], 'completed', result, 'Done')
    assert ended['state'] == 'completed' and '没有回滚' in ended['summary']
    assert service.finish(receipt['id'], 'completed', result, 'Done') == ended
    assert service.stop(receipt['id']) == ended
    with pytest.raises(ValueError): service.finish(receipt['id'], 'cancelled')
    with store.connect() as db:
        for query in ("UPDATE code_integrations SET state='running'", 'DELETE FROM code_integrations', 'INSERT OR REPLACE INTO code_integrations SELECT * FROM code_integrations'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(query)


@pytest.mark.parametrize('change', [{'confirm':1}, {'source_execution_ids':[]}, {'source_execution_ids':['x']*2}, {'source_execution_ids':['x']*17},
    {'fingerprint':'bad'}, {'request_id':''}, {'previous_integration_id':True}, {'reconciliation_note':'unexpected'}, {'extra':1}])
def test_invalid_payload_no_record(tmp_path, monkeypatch, change):
    _, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    with pytest.raises(ValueError): service.create(room['id'], payload(service, room['id'], source['id']) | change)
    assert service.list(room['id']) == []


@pytest.mark.parametrize('change', [{'branch':'other'}, {'source_execution_ids':['wrong']}, {'patch_sha256s':[]}, {'commit':'wrong'},
    {'repository_revision':True}, {'files':True}, {'tree':'f'*64}, {'extra':1}])
def test_native_result_binding_rejected_without_losing_running(tmp_path, monkeypatch, change):
    _, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    receipt = service.create(room['id'], payload(service, room['id'], source['id']))
    with pytest.raises(ValueError): service.finish(receipt['id'], 'completed', native(receipt) | change)
    assert service.get(receipt['id'])['state'] == 'running'


def test_save_failure_rolls_back_and_does_not_repeat_native(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    receipt = service.create(room['id'], payload(service, room['id'], source['id'])); result = native(receipt)
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_finish BEFORE UPDATE ON code_integrations BEGIN SELECT RAISE(ABORT,'disk injected'); END")
    with pytest.raises(sqlite3.IntegrityError): service.finish(receipt['id'], 'completed', result)
    assert service.get(receipt['id'])['state'] == 'running'
    with store.connect() as db: db.execute('DROP TRIGGER fail_finish')
    assert service.finish(receipt['id'], 'completed', result)['result'] == result


def test_unknown_declaration_immutable_and_nonunknown_rejected(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    receipt = service.create(room['id'], payload(service, room['id'], source['id']))
    value = dict(request_id='check', process_stopped=True, effects_checked=True, note='Verified')
    with pytest.raises(ValueError): service.reconcile(receipt['id'], value)
    service.finish(receipt['id'], 'unknown', summary='Cannot establish stop')
    with pytest.raises(ValueError): service.reconcile(receipt['id'], value | {'process_stopped':1})
    service.reconcile(receipt['id'], value)
    with store.connect() as db:
        for query in ('DELETE FROM code_integration_reconciliations', "UPDATE code_integration_reconciliations SET receipt='{}'", 'INSERT OR REPLACE INTO code_integration_reconciliations SELECT * FROM code_integration_reconciliations'):
            with pytest.raises(sqlite3.IntegrityError): db.execute(query)


def test_selected_sources_order_and_revision_mismatch(tmp_path, monkeypatch):
    from workbench.repo_sources import RepositorySources
    from test_workbench_executions import request
    store, tasks, executions, room, agents, task, source, repository, reviews, _, service = ready(tmp_path, monkeypatch)
    manifest = json.loads(next(artifacts.get(store, a['id'])['data'] for a in artifacts.list_for(store, source['id']) if a['path'].endswith('manifest.json')))
    created = []
    for number in range(2):
        if number:
            RepositorySources(store).save(room['id'], dict(request_id='rev2', expected_revision=1, confirm=True,
                **{k: repository['snapshot'][k] for k in ('source_path','commit','integration_agent_id')}))
        task2 = tasks.create(room['id'], {k: task[k] for k in ('source_message_id','title','scope','acceptance','agent_id')} | {'request_id':f'source-{number}'})
        run = executions.create(task2['id'], request()); assert executions.claim(run['id'])
        data = dict(manifest, execution_id=run['id'], repository_revision=number+1)
        executions.report(run['id'], 1, 1, 0, 'Code', success=True, artifacts=[dict(path='corppilot-code/change.patch',data=b''),dict(path='corppilot-code/manifest.json',data=json.dumps(data).encode())])
        Reviews(store).save(run['id'], dict(request_id='approve', expected_version=1, decision='approved', note='Approved', artifact_ids=[a['id'] for a in artifacts.list_for(store,run['id'])]))
        reviewed = send(reviews,run['id']); rid=reviewed['initial_execution_id']; assert executions.claim(rid)
        executions.snapshot(rid,include_artifacts=True)
        executions.report(rid,1,1,0,'Reviewed',success=True,artifacts=[dict(path='review.md',data=b'Checked')])
        Reviews(store).save(rid,dict(request_id='approve',expected_version=1,decision='approved',note='Approved',artifact_ids=[a['id'] for a in artifacts.list_for(store,rid)]))
        created.append(run['id'])
    order = [created[0],source['id']]
    preview = service.preview(room['id'],dict(source_execution_ids=order)); assert not preview['blockers']
    value = payload(service,room['id'],source['id']) | {'source_execution_ids':order,'fingerprint':preview['fingerprint']}
    receipt = service.create(room['id'],value)
    assert [c['manifest']['execution_id'] for c in service.inputs(receipt['id'])['changes']] == order
    assert service.preview(room['id'],dict(source_execution_ids=[source['id'],created[1]]))['blockers']


def test_artifact_tamper_invalidates_preview_and_authorization(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, review_id, service = ready(tmp_path, monkeypatch)
    receipt=service.create(room['id'],payload(service,room['id'],source['id']))
    with store.connect() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='execution_artifacts'")]
        for name in names: db.execute('DROP TRIGGER "'+name+'"')
        db.execute("UPDATE execution_artifacts SET content=? WHERE execution_id=?",(b'corrupt',review_id))
    assert service.preview(room['id'],dict(source_execution_ids=[source['id']]))['blockers']
    with pytest.raises(ValueError): service.inputs(receipt['id'])


def test_same_timestamp_latest_uses_insertion_order_and_list_is_bounded(tmp_path, monkeypatch):
    store, _, _, room, _, _, source, _, _, _, service = ready(tmp_path, monkeypatch)
    receipt=service.create(room['id'],payload(service,room['id'],source['id']))
    service.finish(receipt['id'],'failed',summary='No native call')
    with store.connect() as db:
        authorization=db.execute('SELECT authorization FROM code_integrations WHERE id=?',(receipt['id'],)).fetchone()[0]
        for number in range(101):
            db.execute("INSERT INTO code_integrations(id,project_id,request_id,authorization,state,created_at) VALUES(?,?,?,?,'failed',?)",
                (f'prior-{100-number:03}',room['id'],f'request-{number}',authorization,receipt['created_at']))
    latest=service.preview(room['id'],dict(source_execution_ids=[source['id']]))['latest_integration_id']
    assert latest=='prior-000'
    history=service.list(room['id']); assert len(history)==100 and history[0]['id']==latest
