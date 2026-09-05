"""Persistent messaging, membership revocation, idempotency and v1 migration."""
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.store import Store


def test_dm_idempotency_and_message_concurrency(tmp_path):
    store = Store(tmp_path)
    actor = store.agents()[0]["id"]
    payload = {"type": "dm", "title": "秘书", "member_ids": [actor]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rooms = list(pool.map(lambda _: store.save_conversation(payload), range(8)))
    assert len({r["id"] for r in rooms}) == 1
    room = rooms[0]["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        repeated = list(pool.map(lambda _: store.send_message(room, {"content": "目标", "request_id": "same"}), range(8)))
    assert len({m["id"] for m in repeated}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = list(pool.map(lambda i: store.send_message(room, {"content": f"进展{i}", "request_id": str(i)}, actor), range(8)))
    messages = Store(tmp_path).messages(room)
    assert len(messages) == 9
    assert [m["sequence"] for m in messages] == sorted({m["sequence"] for m in messages})
    assert messages[0]["sender_kind"] == "owner" and messages[0]["sender_id"] is None
    assert all(m["sender_kind"] == "agent" and m["sender_id"] == actor for m in sent)
    assert store.messages(room, after=messages[0]["sequence"], limit=2) == messages[1:3]
    assert store.conversation(room)["last_message"] == messages[-1]
    with pytest.raises(ValueError, match="不同消息"):
        store.send_message(room, {"content": "变了", "request_id": "same"})
    with pytest.raises(ValueError, match="不同消息"):
        store.send_message(room, {"content": "目标", "request_id": "same"}, actor)


def test_permissions_archive_and_disable(tmp_path):
    store = Store(tmp_path)
    first, second, other = [a["id"] for a in store.agents()[:3]]
    room = store.save_conversation({"type": "board", "title": "董事会", "member_ids": [first, second]})["id"]
    assert len(store.conversations(first)) == 1 and not store.conversations(other)
    for read in (store.conversation, store.messages):
        with pytest.raises(PermissionError):
            read(room, other)
    store.send_message(room, {"content": "内部资料", "request_id": "one"}, first)
    store.set_member(room, first, False)
    with pytest.raises(PermissionError):
        store.messages(room, first)
    with pytest.raises(PermissionError):
        store.send_message(room, {"content": "再发", "request_id": "two"}, first)
    store.set_member(room, first, True)
    store.save_agent({"enabled": False}, first)
    with pytest.raises(ValueError):
        store.send_message(room, {"content": "再发", "request_id": "two"}, first)
    with pytest.raises(ValueError):
        store.save_conversation({"type": "project", "title": "新工作", "member_ids": [first]})
    store.save_conversation({"archived": True}, room)
    assert store.messages(room)[0]["content"] == "内部资料"
    with pytest.raises(ValueError, match="归档"):
        store.send_message(room, {"content": "新消息", "request_id": "three"})
    with pytest.raises(ValueError):
        store.set_member(room, other, True)
    restored = Store(tmp_path)
    assert restored.conversation(room)["archived"]
    restored.save_conversation({"archived": False, "title": "恢复会议"}, room)
    assert restored.send_message(room, {"content": "继续", "request_id": "three"})["content"] == "继续"


def test_strict_fields_and_members(tmp_path):
    store = Store(tmp_path)
    ids = [a["id"] for a in store.agents()[:2]]
    valid = {"type": "project", "title": "项目", "member_ids": ids}
    for change in ({"type": "unknown"}, {"member_ids": []}, {"member_ids": ["missing"]},
                   {"member_ids": "bad"}, {"title": " "}, {"archived": True},
                   {"type": "dm"}, {"actor_id": ids[0]}):
        with pytest.raises(ValueError):
            store.save_conversation({**valid, **change})
    room = store.save_conversation(valid)["id"]
    for change in ({"type": "dm"}, {"member_ids": ids}, {"archived": "false"}):
        with pytest.raises(ValueError):
            store.save_conversation(change, room)
    for payload in ({"content": "a"}, {"content": " ", "request_id": "a"},
                    {"content": "a", "request_id": "a", "sender_kind": "agent"}):
        with pytest.raises(ValueError):
            store.send_message(room, payload)
    for after, limit in ((True, 1), (-1, 2), (0, 0), (0, 201)):
        with pytest.raises(ValueError):
            store.messages(room, after=after, limit=limit)
    store.set_member(room, ids[0], False)
    with pytest.raises(ValueError):
        store.set_member(room, ids[1], False)


def test_v1_upgrade_backs_up_and_preserves_identities(tmp_path):
    store = Store(tmp_path)
    actor = store.agents()[0]["id"]
    expected = store.save_agent({"name": "迁移前身份", "enabled": False}, actor)
    with sqlite3.connect(store.db_path) as db:
        for table in ("messages", "members", "conversations"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE schema_version SET version=1")
    upgraded = Store(tmp_path)
    assert upgraded.agent(actor) == expected
    with sqlite3.connect(upgraded.db_path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (2,)
    backups = list(tmp_path.glob("workbench-before-migration-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (1,)
        assert db.execute("SELECT name,enabled FROM agents WHERE id=?", (actor,)).fetchone() == ("迁移前身份", 0)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='messages'").fetchone() is None


def test_concurrent_upgrade_preserves_original_v1_snapshot(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_agent({"name": "迁移前身份", "enabled": False}, store.agents()[0]["id"])
    with closing(sqlite3.connect(store.db_path)) as db, db:
        for table in ("messages", "members", "conversations"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE schema_version SET version=1")
        identities = db.execute("SELECT * FROM agents ORDER BY id").fetchall()
    connect = sqlite3.connect
    reached, resume = threading.Event(), threading.Event()

    def delayed_backup(path, *args, **kwargs):
        if (Path(path).name.startswith("workbench-before-migration-")
                and threading.current_thread().name.startswith("slow-upgrade")):
            reached.set()
            assert resume.wait(10), "升级并发测试等待超时"
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", delayed_backup)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="slow-upgrade") as pool:
        slow = pool.submit(Store, tmp_path)
        try:
            assert reached.wait(10), "慢启动未到达备份边界"
            Store(tmp_path)
        finally:
            resume.set()
        slow.result(timeout=10)
    versions = []
    for path in tmp_path.glob("workbench-before-migration-*.sqlite3"):
        with closing(connect(path)) as db:
            versions.append(db.execute("SELECT version FROM schema_version").fetchone()[0])
            assert db.execute("SELECT * FROM agents ORDER BY id").fetchall() == identities
    assert sorted(versions) == [1, 2]
    with closing(connect(store.db_path)) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (2,)
        assert db.execute("SELECT * FROM agents ORDER BY id").fetchall() == identities


@pytest.mark.parametrize("read", ["conversation", "conversations", "messages"])
def test_read_snapshot_excludes_messages_after_membership_revocation(tmp_path, monkeypatch, read):
    store, writer = Store(tmp_path), Store(tmp_path)
    first, second = [a["id"] for a in store.agents()[:2]]
    room = store.save_conversation({"type": "board", "title": "内部会议", "member_ids": [first, second]})["id"]
    original = store.send_message(room, {"content": "撤销前资料", "request_id": "before"})
    connect = store.connect
    revoked = False

    class InterleavedRead:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            nonlocal revoked
            if "SELECT * FROM messages" in sql and not revoked:
                revoked = True
                writer.set_member(room, first, False)
                writer.send_message(room, {"content": "撤销后秘密", "request_id": "after"})
            return self.db.execute(sql, *args)

    @contextmanager
    def intercepted():
        with connect() as db:
            yield InterleavedRead(db)

    monkeypatch.setattr(store, "connect", intercepted)
    if read == "messages":
        assert store.messages(room, first) == [original]
    else:
        result = store.conversation(room, first) if read == "conversation" else store.conversations(first)[0]
        assert result["last_message"] == original
    assert revoked
    with pytest.raises(PermissionError):
        store.conversation(room, first)
