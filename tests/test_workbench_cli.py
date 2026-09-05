"""CLI protocol acceptance and explicit per-execution configuration boundaries."""
import json
from pathlib import Path
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.cli import execution_environment, parse_result, prepare_workspace, run_codex


def output(events, **changes):
    return {"exit_code": 0, "reason": "exited", "stderr": b"", "stdout":
            b"\n".join(json.dumps(event).encode() for event in events), **changes}


def completion():
    return [{"type": "item.completed", "item": {"type": "agent_message", "text": "Delivered"}},
            {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}]


def test_cli_protocol_does_not_confuse_exit_with_completed_turn():
    result = parse_result(output(completion()), "dummy-secret")
    assert result["success"] and result["summary"] == "Delivered"
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3, "cached_input_tokens": None}
    for events in ([], completion()[:1], completion()[1:], [*completion(), {"type": "turn.started"}],
                   [{"type": "turn.failed"}, *completion()], [*completion(), completion()[-1]], [True]):
        assert not parse_result(output(events), "dummy-secret")["success"]
    for raw in (b"not json", b"\xff"):
        assert not parse_result(output([], stdout=raw), "dummy-secret")["success"]
    for reason in ("cancelled", "timeout", "output_limit", "start_failed", "unknown"):
        assert not parse_result(output(completion(), reason=reason), "dummy-secret")["success"]
    assert not parse_result(output(completion(), exit_code=4), "dummy-secret")["success"]
    events = completion()
    events[0]["item"]["text"] = "echo dummy-secret"
    assert "dummy-secret" not in str(parse_result(output(events), "dummy-secret"))
    assert "dummy-secret" not in str(parse_result(output(events), "  dummy-secret  "))


def test_workspace_and_environment_are_distinct_and_do_not_inherit_secrets(tmp_path, monkeypatch):
    first = prepare_workspace(tmp_path, str(uuid.uuid4()))
    second = prepare_workspace(tmp_path, str(uuid.uuid4()))
    (first["work"] / "marker.txt").write_text("first")
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-pass")
    monkeypatch.setenv("PYTHONPATH", "must-not-pass")
    monkeypatch.setenv("NODE_OPTIONS", "must-not-pass")
    env = execution_environment(first, "only-this-key")
    assert env["CODEX_API_KEY"] == "only-this-key"
    assert not any(name in env for name in ("UNRELATED_API_KEY", "PYTHONPATH", "NODE_OPTIONS"))
    assert env["HOME"] != execution_environment(second, "other")["HOME"]
    assert not (second["work"] / "marker.txt").exists()
    assert env["CODEX_HOME"] == str(first["codex"])
    with pytest.raises(FileExistsError):
        prepare_workspace(tmp_path, first["root"].name)
    with pytest.raises(ValueError):
        prepare_workspace(tmp_path, "../escape")


def test_cli_adapter_fixed_argv_and_private_stdin(tmp_path, monkeypatch):
    import workbench.process_tree as process_tree
    captured = {}

    def run(argv, cwd, env, stdin, timeout, cancel):
        captured.update(argv=argv, cwd=cwd, env=env, stdin=stdin)
        return output(completion())

    monkeypatch.setattr(process_tree, "run_process", run)
    result = run_codex(Path(sys.executable), tmp_path, str(uuid.uuid4()), "Private task", "test-model", " key-test ", 5)
    assert result["success"]
    assert "key-test" not in str(captured["argv"]) and "Private task" not in str(captured["argv"])
    assert captured["stdin"] == b"Private task" and captured["env"]["CODEX_API_KEY"] == "key-test"
    assert "workspace-write" in captured["argv"] and captured["argv"][-1] == "-"
    assert "project_root_markers=[]" in captured["argv"] and "allow_login_shell=false" in captured["argv"]
    assert not any("bypass" in arg for arg in captured["argv"])
    cancelled = threading.Event()
    cancelled.set()
    before = list(tmp_path.iterdir())
    result = run_codex(Path(sys.executable), tmp_path, str(uuid.uuid4()), "task", "model", "key", 5, cancelled)
    assert result["reason"] == "cancelled" and list(tmp_path.iterdir()) == before


def test_workspace_does_not_copy_ambient_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Ambient instructions")
    paths = prepare_workspace(tmp_path, str(uuid.uuid4()))
    assert not (paths["work"] / "AGENTS.md").exists()
