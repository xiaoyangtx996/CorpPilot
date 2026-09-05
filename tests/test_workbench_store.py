"""Identity is persistent and distinct from role templates and execution runs."""
import sys
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.store import REPO_ROOT, Store, role_catalog


def test_all_actual_templates_seed_once_and_preserve_identity(tmp_path):
    source = REPO_ROOT / "agents"
    catalog = role_catalog(source)
    expected = {p.relative_to(source).as_posix().removesuffix(".md")
                for p in source.rglob("*.md") if p.name == "SOUL.md" or p.parent.name == "roles"}
    assert {role["id"] for role in catalog} == expected
    store = Store(tmp_path)
    initial = store.agents()
    assert len(initial) == len(expected)
    chosen = initial[0]
    store.save_agent({"name": "长期身份小林", "enabled": False}, chosen["id"])
    restored = Store(tmp_path)
    assert {agent["id"] for agent in restored.agents()} == {agent["id"] for agent in initial}
    assert restored.agent(chosen["id"])["name"] == "长期身份小林"
    assert not restored.agent(chosen["id"])["enabled"]


def test_custom_identity_validation_and_concurrent_writes(tmp_path):
    store = Store(tmp_path)
    template = store.templates()[0]["id"]
    def create(i):
        return store.save_agent({"name": f"成员 {i}", "template_id": template,
                                 "skills": ["review"], "tools": ["read", "write"]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(create, range(8)))
    assert len({agent["id"] for agent in created}) == 8
    restored = Store(tmp_path)
    for agent in created:
        assert restored.agent(agent["id"]) == agent
        assert not agent["is_default"]
    for payload in ({"name": ""}, {"enabled": "false"}, {"tools": ["admin"]},
                    {"template_id": "missing"}, {"id": "overwrite"}, {"skills": "bad"}):
        with pytest.raises(ValueError):
            store.save_agent(payload, created[0]["id"])
    with pytest.raises(KeyError):
        store.save_agent({"name": "no"}, "missing")


def test_concurrent_patches_preserve_other_fields(tmp_path, monkeypatch):
    store = Store(tmp_path)
    identity = store.agents()[0]["id"]
    read = store.agent
    barrier = threading.Barrier(2)
    state = threading.local()
    def simultaneous_read(agent_id):
        result = read(agent_id)
        if not getattr(state, "read", False):
            state.read = True
            barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(store, "agent", simultaneous_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.save_agent, change, identity)
                   for change in ({"name": "改名后"}, {"enabled": False})]
        for future in futures:
            future.result()
    assert read(identity)["name"] == "改名后"
    assert read(identity)["enabled"] is False


def test_future_database_version_is_not_silently_changed(tmp_path):
    store = Store(tmp_path)
    with sqlite3.connect(store.db_path) as db:
        db.execute("UPDATE schema_version SET version=999")
    with pytest.raises(ValueError, match="数据库版本"):
        Store(tmp_path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchall() == [(999,)]
