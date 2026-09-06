"""Actual files and SQLite verify bounded identity-specific Skill inputs without model calls."""
import copy
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workbench import skill_inputs as skills


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'skills'
    root.mkdir()
    (root / 'coding.md').write_bytes('# 编码\nFirst version\r\n'.encode())
    (root / 'demo-generator.md').write_bytes(b'# Demo\nMock workflow\n')
    db = sqlite3.connect(tmp_path / 'state.db')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE agents(id TEXT PRIMARY KEY, skills TEXT NOT NULL)')
    for table in ('runs', 'task_executions'):
        db.execute(f'''CREATE TABLE {table}(id TEXT PRIMARY KEY,agent_id TEXT REFERENCES agents(id),
            attempt INTEGER,requirement_version INTEGER,state TEXT)''')
    db.execute('INSERT INTO agents VALUES(?,?)', ('alice', '["demo-generator","coding"]'))
    db.execute('INSERT INTO agents VALUES(?,?)', ('bob', '[]'))
    run = dict(id='run', agent_id='alice', attempt=1, requirement_version=1, state='queued')
    for table in ('runs', 'task_executions'):
        db.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?)', tuple(run.values()))
    skills.initialize(db)
    yield db, root, run
    db.close()


def test_catalog_reads_exact_utf8_and_deterministic_sources(setup):
    _, root, _ = setup
    catalog = skills.catalog(root)
    assert [x['id'] for x in catalog] == ['coding', 'demo-generator']
    for item in catalog:
        raw = (root / (item['id'] + '.md')).read_bytes()
        assert item['bytes'] == len(raw)
        assert item['content'].encode() == raw
        assert item['version'] == hashlib.sha256(raw).hexdigest()
        assert item['source'] == 'skills/' + item['id'] + '.md'
    (root / 'extra.md').write_text('not selectable')
    assert skills.catalog(root) == catalog


@pytest.mark.parametrize('kind,table', [('model', 'runs'), ('cli', 'task_executions')])
def test_freeze_is_ordered_immutable_and_new_run_gets_new_version(setup, kind, table):
    db, root, run = setup
    first = skills.freeze(db, kind, run, root)
    assert [x['id'] for x in first['skills']] == ['demo-generator', 'coding']
    db.execute(f"UPDATE {table} SET state='running'")
    (root / 'coding.md').write_text('# New\nNew workflow', encoding='utf-8')
    db.execute("UPDATE agents SET skills='[\"coding\"]' WHERE id='alice'")
    assert skills.snapshot(db, kind, run) == first
    assert skills.freeze(db, kind, run, root / 'missing') == first
    newer = {**run, 'id': 'next'}
    db.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?)', tuple(newer.values()))
    second = skills.freeze(db, kind, newer, root)
    assert [x['id'] for x in second['skills']] == ['coding']
    assert second['skills'][0]['version'] != first['skills'][1]['version']
    for sql in (
        'UPDATE skill_input_snapshots SET snapshot=snapshot',
        'DELETE FROM skill_input_snapshots',
        'INSERT OR REPLACE INTO skill_input_snapshots SELECT * FROM skill_input_snapshots',
    ):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute(sql)
    assert skills.get(db, kind, run) == first


def test_empty_selection_is_frozen_without_reading_files_and_model_cli_do_not_collide(setup):
    db, root, run = setup
    original = skills.freeze(db, 'model', run, root)
    db.execute("UPDATE agents SET skills='[]'")
    empty = skills.freeze(db, 'cli', run, root / 'absent')
    assert empty['skills'] == []
    assert skills.snapshot(db, 'model', run) == original
    assert skills.snapshot(db, 'cli', run) == empty
    assert skills.augment('original', empty) == 'original'


def test_scope_identity_attempt_version_and_missing_history_are_not_forged(setup):
    db, root, run = setup
    assert skills.get(db, 'model', run) is None
    with pytest.raises(ValueError, match='没有固定'):
        skills.snapshot(db, 'model', run)
    db.execute("UPDATE runs SET state='running'")
    with pytest.raises(ValueError, match='尚未启动'):
        skills.freeze(db, 'model', run, root)
    for change in ({'id': 'missing'}, {'agent_id': 'bob'}, {'attempt': 2}, {'requirement_version': 2}, {'attempt': True}):
        for action in (skills.get, skills.snapshot, skills.freeze):
            with pytest.raises(ValueError):
                action(db, 'model', run | change)
    with pytest.raises(ValueError):
        skills.freeze(db, 'invented', run)


@pytest.mark.parametrize('selected', [['unknown'], ['../coding'], ['C:\\coding'], ['https://x'], ['coding', 'coding'], ['coding'] * 17, [False], {}, None])
def test_unknown_paths_duplicates_and_selection_limit_fail_without_partial_write(setup, selected):
    db, root, run = setup
    db.execute('UPDATE agents SET skills=?', (json.dumps(selected),))
    with pytest.raises(ValueError):
        skills.freeze(db, 'model', run, root)
    assert db.execute('SELECT count(*) FROM skill_input_snapshots').fetchone()[0] == 0


def test_only_selected_document_is_read_and_invalid_utf8_or_size_is_explicit(setup, monkeypatch):
    db, root, run = setup
    db.execute("UPDATE agents SET skills='[\"coding\"]'")
    (root / 'demo-generator.md').write_bytes(b'\xff')
    assert skills.freeze(db, 'model', run, root)['skills'][0]['id'] == 'coding'
    with pytest.raises(ValueError):
        skills.catalog(root)
    for raw in (b'\xff', b'', b' \n', b'x\0y', b'x\x01y', b'x\x1by', b'x\x7fy', b'x' * 16001, b'x' * 64001):
        (root / 'coding.md').write_bytes(raw)
        with pytest.raises(ValueError):
            skills.load_selected(['coding'], root)
    (root / 'coding.md').write_bytes(b'# Good\n')
    monkeypatch.setattr(skills, 'MAX_TOTAL_CHARS', 3)
    with pytest.raises(ValueError):
        skills.load_selected(['coding'], root)


def test_hard_link_directory_and_missing_file_are_rejected(setup):
    _, root, _ = setup
    path = root / 'coding.md'
    os.link(path, root / 'second-link')
    with pytest.raises(ValueError):
        skills.load_selected(['coding'], root)
    (root / 'second-link').unlink()
    path.unlink()
    with pytest.raises(ValueError):
        skills.load_selected(['coding'], root)
    path.mkdir()
    with pytest.raises(ValueError):
        skills.load_selected(['coding'], root)


def test_symlink_file_and_parent_are_rejected(setup, tmp_path):
    _, root, _ = setup
    target = tmp_path / 'target.md'
    target.write_text('foreign data')
    (root / 'coding.md').unlink()
    try:
        (root / 'coding.md').symlink_to(target)
    except OSError as error:
        pytest.skip(f'OS denied symlink creation: {error}')
    with pytest.raises(ValueError):
        skills.load_selected(['coding'], root)
    linked = tmp_path / 'linked'
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        skills.load_selected(['demo-generator'], linked)


@pytest.mark.parametrize('corruption', ['hash', 'scope', 'bytes', 'source', 'shape', 'name', 'version_type', 'control'])
def test_stored_snapshot_readback_validates_hash_scope_and_shape(setup, corruption):
    db, root, run = setup
    frozen = skills.freeze(db, 'model', run, root)
    bad = copy.deepcopy(frozen)
    if corruption == 'hash': bad['skills'][0]['content'] += 'tampered'
    if corruption == 'scope': bad['agent_id'] = 'bob'
    if corruption == 'bytes': bad['skills'][0]['bytes'] = True
    if corruption == 'source': bad['skills'][0]['source'] = 'outside/private.md'
    if corruption == 'shape': bad['skills'][0] = None
    if corruption == 'name': bad['skills'][0]['name'] = 'Misleading title'
    if corruption == 'version_type': bad['attempt'] = True
    if corruption == 'control':
        bad['skills'][0]['content'] += '\x1b'
        raw = bad['skills'][0]['content'].encode()
        bad['skills'][0]['version'] = hashlib.sha256(raw).hexdigest()
        bad['skills'][0]['bytes'] = len(raw)
    db.execute('DROP TRIGGER skill_inputs_no_update')
    db.execute('UPDATE skill_input_snapshots SET snapshot=?', (json.dumps(bad),))
    with pytest.raises(ValueError):
        skills.get(db, 'model', run)


def test_augment_preserves_original_and_enforces_permission_language_and_total_limit(setup):
    db, root, run = setup
    frozen = skills.freeze(db, 'model', run, root)
    result = skills.augment('Required output protocol', frozen)
    assert result.startswith('Required output protocol\n\n')
    assert '不扩大工具权限' in result and '不改变当前任务规定的输出协议' in result
    assert 'First version' in result and frozen['skills'][0]['version'] in result
    with pytest.raises(ValueError, match='64000'):
        skills.augment('x' * 64000, frozen)
