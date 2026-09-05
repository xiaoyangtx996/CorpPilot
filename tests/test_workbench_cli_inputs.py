"""Approved input snapshots are bounded copies, never shared workspace mounts."""
import hashlib
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench import artifacts
from workbench.cli import InputPreparationError, prepare_workspace, run_codex


def snapshot(data=b"approved bytes", path="reports/result.md", **changes):
    return {"id": str(uuid.uuid4()), "execution_id": str(uuid.uuid4()), "path": path,
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "data": data,
            **changes}


def test_inputs_are_independent_flat_copies_not_ambient_instructions(tmp_path):
    original = snapshot(path=".codex/config.toml")
    other = snapshot(b"instructions", path="AGENTS.md")
    first = prepare_workspace(tmp_path, str(uuid.uuid4()), [original, other])
    second = prepare_workspace(tmp_path, str(uuid.uuid4()), [original, other])
    for paths in (first, second):
        assert sorted(p.name for p in paths["work"].iterdir()) == ["inputs"]
        assert sorted(p.name for p in (paths["work"] / "inputs").iterdir()) == sorted([original["id"], other["id"]])
        assert (paths["work"] / "inputs" / original["id"]).read_bytes() == original["data"]
        assert not list(paths["codex"].iterdir())
    (first["work"] / "inputs" / original["id"]).write_bytes(b"changed downstream")
    assert (second["work"] / "inputs" / original["id"]).read_bytes() == original["data"]
    assert original["data"] == b"approved bytes"
    with pytest.raises(FileExistsError):
        prepare_workspace(tmp_path, first["root"].name, [original])
    assert (first["work"] / "inputs" / original["id"]).read_bytes() == b"changed downstream"


@pytest.mark.parametrize("change", [
    {"id": "../escape"}, {"execution_id": "not-an-id"}, {"path": "../escape"},
    {"path": "/absolute"}, {"path": "C:/escape"}, {"path": "CON"},
    {"path": "nested\\escape"}, {"path": "x\u202ey"}, {"size": True},
    {"size": -1}, {"size": 0}, {"sha256": "0" * 64}, {"data": "not bytes"},
    {"extra": "unexpected"},
])
def test_invalid_later_snapshot_creates_nothing(tmp_path, change):
    target = tmp_path / "not-created"
    with pytest.raises(ValueError):
        prepare_workspace(target, str(uuid.uuid4()), [snapshot(), {**snapshot(), **change}])
    assert not target.exists()


def test_limits_and_duplicate_ids_validate_before_filesystem_changes(tmp_path):
    item = snapshot()
    cases = [(), {}, [item, dict(item)], [snapshot() for _ in range(101)],
             [snapshot(b"x" * (artifacts.MAX_FILE_BYTES + 1))],
             [snapshot(b"x" * artifacts.MAX_FILE_BYTES) for _ in range(5)]]
    for inputs in cases:
        target = tmp_path / str(uuid.uuid4())
        with pytest.raises(ValueError):
            prepare_workspace(target, str(uuid.uuid4()), inputs)
        assert not target.exists()


def test_materialization_failure_never_invokes_process(tmp_path, monkeypatch):
    import workbench.process_tree as process_tree
    called = []
    monkeypatch.setattr(process_tree, "run_process", lambda *args: called.append(args))
    args = (Path(sys.executable), tmp_path, str(uuid.uuid4()), "task", "model", "test-key", 5)
    with pytest.raises(InputPreparationError):
        run_codex(*args, input_artifacts=[snapshot(sha256="invalid")])
    assert called == [] and not list(tmp_path.iterdir())
    prepare_workspace(tmp_path, args[2])
    with pytest.raises(InputPreparationError):
        run_codex(*args, input_artifacts=[snapshot()])
    assert called == []


def test_runner_receives_complete_inputs_before_start(tmp_path, monkeypatch):
    import workbench.process_tree as process_tree
    item = snapshot()
    calls = []
    def process(argv, cwd, env, stdin, timeout, cancel):
        calls.append(cwd)
        assert (cwd / "inputs" / item["id"]).read_bytes() == item["data"]
        assert item["path"] not in str(argv)
        return {"exit_code": 1, "reason": "exited", "stdout": b"", "stderr": b""}
    monkeypatch.setattr(process_tree, "run_process", process)
    result = run_codex(Path(sys.executable), tmp_path, str(uuid.uuid4()), "task", "model", "test-key", 5,
                       input_artifacts=[item])
    assert len(calls) == 1 and result["exit_code"] == 1


def test_input_write_error_is_not_started_and_does_not_overwrite(tmp_path, monkeypatch):
    import workbench.process_tree as process_tree
    item = snapshot()
    original_open = Path.open
    called = []
    def open_file(path, mode="r", *args, **kwargs):
        if path.name == item["id"]:
            assert mode == "xb"
            raise PermissionError("injected filesystem failure")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(process_tree, "run_process", lambda *args: called.append(args))
    with pytest.raises(InputPreparationError):
        run_codex(Path(sys.executable), tmp_path, str(uuid.uuid4()), "task", "model", "test-key", 5,
                   input_artifacts=[item])
    assert called == []
