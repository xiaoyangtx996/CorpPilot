"""Real filesystem capture and immutable artifact database evidence."""
import hashlib
import os
import sqlite3
import uuid

import pytest

from test_workbench_executions import setup, request
from workbench import artifacts


def workspace(tmp_path):
    identity = str(uuid.uuid4())
    root = tmp_path / "execution-workspaces" / identity / "work" / "artifacts"
    root.mkdir(parents=True)
    return identity, root


def test_capture_snapshot_persist_readback_and_immutable(tmp_path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    root = tmp_path / "execution-workspaces" / run["id"] / "work" / "artifacts"
    root.mkdir(parents=True)
    (root / "文档").mkdir()
    (root / "文档" / "result.txt").write_bytes(b"delivered content")
    items = artifacts.capture(tmp_path, run["id"], "private-credential")
    with store.connect() as db:
        artifacts.initialize(db)
        artifacts.persist(db, run["id"], items)
    (root / "文档" / "result.txt").write_bytes(b"later mutation")
    listed = artifacts.list_for(store, run["id"])
    assert len(listed) == 1 and "data" not in listed[0]
    assert listed[0]["sha256"] == hashlib.sha256(b"delivered content").hexdigest()
    assert artifacts.get(store, listed[0]["id"])["data"] == b"delivered content"
    for sql in ("UPDATE execution_artifacts SET size=0", "DELETE FROM execution_artifacts"):
        with pytest.raises(sqlite3.IntegrityError), store.connect() as db:
            db.execute(sql)
    with pytest.raises(KeyError):
        artifacts.list_for(store, str(uuid.uuid4()))
    with pytest.raises(KeyError):
        artifacts.get(store, str(uuid.uuid4()))


@pytest.mark.parametrize("case", ["oversize", "hardlink", "secret", "secret-name", "utf16", "depth", "count"])
def test_capture_rejects_unsafe_exports(tmp_path, case):
    identity, root = workspace(tmp_path)
    target = root / "result.txt"
    secret = "private-credential"
    if case == "oversize":
        target.write_bytes(b"x" * (artifacts.MAX_FILE_BYTES + 1))
    elif case == "hardlink":
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"private")
        os.link(outside, target)
    elif case == "secret":
        target.write_bytes(secret.encode())
    elif case == "utf16":
        target.write_bytes(secret.encode("utf-16"))
    elif case == "secret-name":
        (root / secret).write_bytes(b"x")
    elif case == "depth":
        deep = root.joinpath(*(["folder"] * (artifacts.MAX_DEPTH + 1)))
        deep.mkdir(parents=True)
    elif case == "count":
        for number in range(artifacts.MAX_FILES + 1):
            (root / str(number)).write_bytes(b"")
    with pytest.raises(ValueError) as error:
        artifacts.capture(tmp_path, identity, secret)
    assert secret not in str(error.value) and str(tmp_path) not in str(error.value)


def test_absent_export_and_links(tmp_path):
    identity, root = workspace(tmp_path)
    root.rmdir()
    assert artifacts.capture(tmp_path, identity, "secret") == []
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating symbolic links")
    with pytest.raises(ValueError):
        artifacts.capture(tmp_path, identity, "secret")


@pytest.mark.parametrize("path", ["../x", "/x", "x\\y", "x:y", "x\n", "CON.txt", "x/", "x/../y", "x\ud800", "x\u202e"])
def test_persist_validation_before_any_insert(tmp_path, path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    with store.connect() as db:
        artifacts.initialize(db)
        with pytest.raises(ValueError):
            artifacts.persist(db, run["id"], [{"path": "good", "data": b"ok"}, {"path": path, "data": b"bad"}])
        assert db.execute("SELECT COUNT(*) FROM execution_artifacts").fetchone()[0] == 0


def test_corrupt_content_is_not_downloaded(tmp_path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    with store.connect() as db:
        artifacts.initialize(db)
        artifacts.persist(db, run["id"], [{"path": "ok", "data": b"ok"}])
        db.execute("DROP TRIGGER artifacts_no_update")
        db.execute("UPDATE execution_artifacts SET content=?", (b"no",))
    identity = artifacts.list_for(store, run["id"])[0]["id"]
    with pytest.raises(ValueError):
        artifacts.get(store, identity)


def test_capture_total_and_entry_limits(tmp_path, monkeypatch):
    identity, root = workspace(tmp_path)
    monkeypatch.setattr(artifacts, "MAX_TOTAL_BYTES", 5)
    (root / "a").write_bytes(b"123")
    (root / "b").write_bytes(b"456")
    with pytest.raises(ValueError):
        artifacts.capture(tmp_path, identity, "secret")
    (root / "a").unlink()
    (root / "b").unlink()
    for number in range(3):
        (root / str(number)).mkdir()
    monkeypatch.setattr(artifacts, "MAX_ENTRIES", 2)
    with pytest.raises(ValueError):
        artifacts.capture(tmp_path, identity, "secret")


def test_live_handles_deny_directory_swap_and_file_mutation(tmp_path):
    identity, root = workspace(tmp_path)
    target = root / "answer"
    target.write_bytes(b"stable")
    with artifacts._locked(root, True):
        with pytest.raises(OSError):
            root.rename(root.with_name("moved"))
    with artifacts._locked(target, False) as stream:
        with pytest.raises(OSError):
            target.write_bytes(b"changed")
        with pytest.raises(OSError):
            target.unlink()
        assert stream.read() == b"stable"


def test_replace_cannot_change_snapshot_and_insertion_rolls_back(tmp_path):
    store, _, executions, task = setup(tmp_path)
    run = executions.create(task["id"], request())
    with store.connect() as db:
        artifacts.initialize(db)
        artifacts.persist(db, run["id"], [{"path": "a", "data": b"old"}])
        row = db.execute("SELECT * FROM execution_artifacts").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO execution_artifacts VALUES (?,?,?,?,?,?)", tuple(row))
        with pytest.raises(sqlite3.IntegrityError):
            artifacts.persist(db, run["id"], [{"path": "b", "data": b"new"}, {"path": "a", "data": b"overwrite"}])
        assert db.execute("SELECT COUNT(*) FROM execution_artifacts").fetchone()[0] == 1
