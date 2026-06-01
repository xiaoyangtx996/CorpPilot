"""
Traffic Monitor — 流量监控与成本审计
每次 LLM 调用后记录：Token 消耗 / 延迟 / 成本估算 / RPM 计数
"""
from __future__ import annotations

import json
import time
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

_DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "traffic_logs.jsonl"


class TrafficMonitor:
    """
    用法示例：
        monitor = TrafficMonitor(router=my_router)
        monitor.record("rd_center", "gpt-4o", 1200, 400, 1500)
        stats = monitor.get_stats(window="1h")
    """

    def __init__(
        self,
        log_path: Optional[Path | str] = None,
        router=None,  # ModelRouter 实例，用于获取定价
    ):
        self.log_path = Path(log_path) if log_path else _DEFAULT_LOG_PATH
        self.router = router
        self._lock = threading.Lock()
        # 滑动窗口：记录最近 1 分钟内的调用时间戳（deque 最多保留 1000 条）
        self._minute_calls: Deque[Tuple[float, str]] = deque(maxlen=1000)

    # ---------------------------------------------------------------------- #
    # 公开接口
    # ---------------------------------------------------------------------- #

    def record(
        self,
        agent_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float = 0.0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """追加一条调用记录到 JSONL 日志。"""
        pricing = {}
        cost_usd = 0.0
        if self.router:
            pricing = self.router.get_pricing(model)
            cost_usd = (
                prompt_tokens / 1000 * pricing.get("input_per_1k", 0)
                + completion_tokens / 1000 * pricing.get("output_per_1k", 0)
            )

        entry = {
            "ts": time.time(),
            "agent_id": agent_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "latency_ms": round(latency_ms, 1),
            "cost_usd": round(cost_usd, 6),
        }
        if extra:
            entry.update(extra)

        with self._lock:
            self._minute_calls.append((time.time(), agent_id))
            self._append_log(entry)

    def check_rate_limit(self, agent_id: Optional[str] = None, limit_rpm: int = 60) -> bool:
        """检查是否超过 RPM 限制。超限返回 True。"""
        now = time.time()
        window_start = now - 60.0
        with self._lock:
            recent = [
                e for e in self._minute_calls
                if e[0] >= window_start and (agent_id is None or e[1] == agent_id)
            ]
        return len(recent) >= limit_rpm

    def get_stats(self, window: str = "1h", group_by: Optional[str] = None) -> Dict[str, Any]:
        """
        聚合统计数据。
        window: "1h" | "24h" | "all"
        group_by: None | "department" | "task" | "step"
        """
        seconds = {"1h": 3600, "24h": 86400, "all": float("inf")}
        cutoff = time.time() - seconds.get(window, 3600)

        rows = self._read_logs(cutoff)

        total_calls = len(rows)
        total_tokens = sum(r.get("total_tokens", 0) for r in rows)
        total_cost = sum(r.get("cost_usd", 0) for r in rows)
        total_latency = sum(r.get("latency_ms", 0) for r in rows)
        avg_latency = round(total_latency / total_calls, 1) if total_calls else 0

        by_agent: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0}
        )
        by_model: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0}
        )

        for r in rows:
            aid = r.get("agent_id", "unknown")
            mdl = r.get("model", "unknown")
            toks = r.get("total_tokens", 0)
            cost = r.get("cost_usd", 0)

            by_agent[aid]["calls"] += 1
            by_agent[aid]["tokens"] += toks
            by_agent[aid]["cost_usd"] += cost

            by_model[mdl]["calls"] += 1
            by_model[mdl]["tokens"] += toks
            by_model[mdl]["cost_usd"] += cost

        # 当前分钟 RPM
        now = time.time()
        with self._lock:
            rpm = sum(1 for e in self._minute_calls if e[0] >= now - 60)

        result: Dict[str, Any] = {
            "window": window,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "avg_latency_ms": avg_latency,
            "current_rpm": rpm,
            "by_agent": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_agent.items()},
            "by_model": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_model.items()},
        }

        if group_by in {"department", "task", "step"}:
            key_map = {
                "department": "department_id",
                "task": "task_id",
                "step": "flow_step_id",
            }
            field = key_map[group_by]
            grouped: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            )
            for r in rows:
                gid = r.get(field) or "unknown"
                grouped[gid]["calls"] += 1
                grouped[gid]["tokens"] += r.get("total_tokens", 0)
                grouped[gid]["cost_usd"] += r.get("cost_usd", 0)
            result[f"by_{group_by}"] = {
                k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in grouped.items()
            }

        return result

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 N 条记录（时间倒序）。"""
        rows = self._read_logs()
        return list(reversed(rows[-limit:]))

    # ---------------------------------------------------------------------- #
    # 私有
    # ---------------------------------------------------------------------- #

    def _append_log(self, entry: Dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read_logs(self, cutoff: float = 0.0) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        rows = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if row.get("ts", 0) >= cutoff:
                            rows.append(row)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return rows
