import json
import uuid

import pytest

from test_workbench_activity import fixture, add_execution
from workbench.backup import backup, verify, restore
from workbench.directory_lock import acquire


def seeded(tmp_path):
    source = tmp_path / 'source'
    store, tasks, cid, payload = fixture(source)
    task = tasks.create(cid,payload)
    identity = str(uuid.uuid4())
    with store.connect() as db:
        add_execution(db,task,identity)
        db.execute('INSERT INTO execution_artifacts VALUES(?,?,?,3,?,?)',('artifact',identity,'result.txt','x'*64,b'abc'))
    root = source / 'execution-workspaces' / identity
    root.mkdir(parents=True)
    token = 'a' * 32
    record = {'version':1,'execution_id':identity,'token':token,'name':f'corppilot-{token[:12]}-{identity}',
              'image':'sha256:'+'a'*64,'phase':'removed','container_id':None,'executable':'C:\\missing\\docker.exe'}
    (root / 'docker-worker.json').write_text(json.dumps(record),encoding='utf-8')
    (root / 'home').mkdir()
    (root / 'home' / 'auth.json').write_text('secret')
    (source / 'owner-access-secret.html').write_text('owner token')
    (source / 'reply-usage.jsonl').write_text('{"tokens":3}\n')
    return source,store,identity


def test_allowlist_round_trip_quarantine_and_original_untouched(tmp_path):
    source,store,identity = seeded(tmp_path)
    with store.connect() as db: before = list(db.iterdump())
    bundle,target = tmp_path/'bundle',tmp_path/'restored'
    manifest = backup(source,bundle)
    assert len(manifest['files']) == 3
    assert verify(bundle) == manifest
    restore(bundle,target)
    marker = json.loads((target/'restore-quarantine.json').read_text())
    assert marker == {'version':1,'backup':str(bundle),'source_data_dir':str(source)}
    assert (target/'execution-workspaces'/identity/'docker-worker.json').read_bytes() == (source/'execution-workspaces'/identity/'docker-worker.json').read_bytes()
    assert not (target/'owner-access-secret.html').exists()
    assert not (target/'execution-workspaces'/identity/'home').exists()
    import sqlite3
    with sqlite3.connect(target/'workbench.sqlite3') as db:
        assert db.execute('SELECT content FROM execution_artifacts').fetchone()[0] == b'abc'
    with store.connect() as db: assert list(db.iterdump()) == before


def test_live_lock_and_existing_target_rejected(tmp_path):
    source,_,_ = seeded(tmp_path)
    with acquire(source), pytest.raises(ValueError,match='已有运行'):
        backup(source,tmp_path/'bundle')
    assert not (tmp_path/'bundle').exists()
    backup(source,tmp_path/'bundle')
    with pytest.raises(ValueError): backup(source,tmp_path/'bundle')
    with pytest.raises(ValueError): restore(tmp_path/'bundle',source)


@pytest.mark.parametrize('damage',['hash','path','extra','version','database'])
def test_untrusted_bundle_rejected_before_target_created(tmp_path,damage):
    source,_,_ = seeded(tmp_path)
    bundle,target = tmp_path/'bundle',tmp_path/'target'
    backup(source,bundle)
    manifest = json.loads((bundle/'manifest.json').read_text())
    if damage == 'hash': manifest['files'][0]['sha256'] = '0'*64
    elif damage == 'path': manifest['files'][0]['path'] = '../outside'
    elif damage == 'version': manifest['version'] = True
    elif damage == 'extra': (bundle/'secret').write_text('secret')
    elif damage == 'database': (bundle/'workbench.sqlite3').write_bytes(b'invalid')
    (bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        restore(bundle,target)
    assert not target.exists()


def test_symlink_rejected(tmp_path):
    source,_,_ = seeded(tmp_path)
    bundle = tmp_path/'bundle'
    backup(source,bundle)
    link = bundle/'linked'
    try: link.symlink_to(source,target_is_directory=True)
    except OSError: pytest.skip('Symlink creation unavailable')
    with pytest.raises(ValueError,match='链接'): verify(bundle)


def test_matching_hash_does_not_bypass_database_integrity(tmp_path):
    import hashlib
    import sqlite3
    source,_,_ = seeded(tmp_path)
    bundle = tmp_path/'bundle'
    backup(source,bundle)
    content = b'not sqlite'
    (bundle/'workbench.sqlite3').write_bytes(content)
    manifest = json.loads((bundle/'manifest.json').read_text())
    item = next(row for row in manifest['files'] if row['path']=='workbench.sqlite3')
    item.update(size=len(content),sha256=hashlib.sha256(content).hexdigest())
    (bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises((ValueError,sqlite3.DatabaseError)): verify(bundle)


def test_copy_failure_keeps_quarantine_and_original(tmp_path,monkeypatch):
    import importlib
    module = importlib.import_module('workbench.backup')
    source,store,_ = seeded(tmp_path)
    bundle,target = tmp_path/'bundle',tmp_path/'target'
    backup(source,bundle)
    with store.connect() as db: before = list(db.iterdump())
    def failed(*args): raise OSError('copy interrupted')
    monkeypatch.setattr(module,'copy',failed)
    with pytest.raises(OSError,match='interrupted'): restore(bundle,target)
    assert (target/'restore-quarantine.json').is_file()
    assert not (target/'restore-complete.json').exists()
    assert not (target/'workbench.sqlite3').exists()
    with store.connect() as db: assert list(db.iterdump()) == before


@pytest.mark.parametrize('damage',['agents_only','unsupported_version','missing_reconciliation'])
def test_incomplete_or_unsupported_database_rejected(tmp_path,damage):
    import sqlite3
    source,store,_ = seeded(tmp_path)
    if damage == 'agents_only':
        source = tmp_path/'fake'
        source.mkdir()
        with sqlite3.connect(source/'workbench.sqlite3') as db:
            db.execute('CREATE TABLE agents(id TEXT)')
    else:
        with store.connect() as db:
            if damage == 'unsupported_version': db.execute('UPDATE runs_schema_version SET version=99')
            else: db.execute('DROP TABLE model_run_reconciliations')
    with pytest.raises(ValueError): backup(source,tmp_path/'bundle')
    assert not (tmp_path/'bundle').exists()


def test_complete_evidence_and_cli_error_no_traceback(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    source,_,_ = seeded(tmp_path)
    bundle,target = tmp_path/'bundle',tmp_path/'target'
    backup(source,bundle)
    restore(bundle,target)
    assert json.loads((target/'restore-complete.json').read_text()) == {'version':1,'backup':str(bundle)}
    result = subprocess.run([sys.executable,'-m','workbench.backup','verify',str(tmp_path/'missing')],
                            cwd=Path(__file__).resolve().parents[1]/'scripts',capture_output=True)
    assert result.returncode == 1
    assert b'Traceback' not in result.stderr
