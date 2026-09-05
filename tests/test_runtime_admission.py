"""Provider attempts reserve RPM before execution, including retries."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import agent_loop as loop_module
from runtime import traffic_monitor
from runtime.llm_client import LLMClient, LLMResponse, ModelConfig
from runtime.traffic_monitor import TrafficMonitor


def test_atomic_admission_expiry_and_no_completion_double_count(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(traffic_monitor.time, "monotonic", lambda: clock[0])
    monitor = TrafficMonitor(tmp_path / "traffic.jsonl")
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(lambda _: monitor.reserve_call(3), range(30))) == 3
    monitor.record("a", "test", 2, 1)
    assert not monitor.reserve_call(3)
    clock[0] += 59.999
    assert not monitor.reserve_call(3)
    clock[0] = 160.0
    assert monitor.reserve_call(3)
    assert monitor.reserve_call(3)
    assert monitor.reserve_call(3)
    assert not monitor.reserve_call(3)
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            monitor.reserve_call(invalid)
    # Completed usage remains the existing reporting metric, separate from starts.
    assert monitor.get_stats()["total_calls"] == 1


def test_retries_reserve_before_each_provider_attempt(monkeypatch):
    events = []
    client = LLMClient(max_retries=2, retry_delay=0)
    def provider(*args):
        events.append("provider")
        if events.count("provider") == 1:
            raise RuntimeError("injected failure")
        return LLMResponse("ok")
    monkeypatch.setattr(client, "_call_openai", provider)
    result = client.call([], model_cfg=ModelConfig({}), before_attempt=lambda: events.append("reserve"))
    assert result.content == "ok"
    assert events == ["reserve", "provider", "reserve", "provider"]
    events.clear()
    def denied():
        raise ValueError("invalid limit")
    with pytest.raises(ValueError):
        client.call([], model_cfg=ModelConfig({}), before_attempt=denied)
    assert events == []


def test_loop_waits_only_when_admission_is_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_module, "_load_soul", lambda _: "role")
    monkeypatch.setattr(loop_module, "_load_skills", lambda *_: "")
    monkeypatch.setattr(loop_module, "ToolExecutor", lambda *a, **k: SimpleNamespace(is_done=False))
    cfg = ModelConfig({})
    route = SimpleNamespace(primary=cfg, get_attempts=lambda: [cfg])
    router = SimpleNamespace(resolve=lambda **_: route, get_rate_limit_rpm=lambda: 1)
    monitor = TrafficMonitor(tmp_path / "traffic.jsonl")
    decisions = iter([False, False, True])
    monkeypatch.setattr(monitor, "reserve_call", lambda _: next(decisions))
    sleeps = []
    monkeypatch.setattr(loop_module.time, "sleep", sleeps.append)
    client = LLMClient()
    monkeypatch.setattr(client, "_call_openai", lambda *_: LLMResponse("done"))
    result = loop_module.agent_loop("test", "task", SimpleNamespace(read_inbox=lambda _: []), router, monitor, client)
    assert result == "done" and sleeps == [5, 5]
    sleeps.clear()
    monkeypatch.setattr(monitor, "reserve_call", lambda _: True)
    loop_module.agent_loop("test", "task", SimpleNamespace(read_inbox=lambda _: []), router, monitor, client)
    assert sleeps == []
