#!/usr/bin/env python3
"""为演示/测试任务写入带归因的 traffic 日志。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = PROJECT_ROOT / "data" / "traffic_logs.jsonl"

# 模拟 greenfield 各 step 的典型调用
GREENFIELD_TRAFFIC_TEMPLATE: List[Dict[str, Any]] = [
    {"agent_id": "president_office", "department_id": "president_office", "flow_step_id": "board_discussion", "model": "gpt-4o", "prompt_tokens": 820, "completion_tokens": 410, "latency_ms": 920},
    {"agent_id": "product_center", "department_id": "product_center", "flow_step_id": "product_demo", "model": "gpt-4o", "prompt_tokens": 2400, "completion_tokens": 1800, "latency_ms": 2100},
    {"agent_id": "pmo", "department_id": "pmo", "flow_step_id": "prd_generation", "model": "claude-3-5-sonnet", "prompt_tokens": 1600, "completion_tokens": 900, "latency_ms": 1400},
    {"agent_id": "product_center", "department_id": "product_center", "flow_step_id": "dev_parallel", "model": "gpt-4o", "prompt_tokens": 3200, "completion_tokens": 2400, "latency_ms": 2800},
    {"agent_id": "rd_center", "department_id": "rd_center", "flow_step_id": "dev_parallel", "model": "claude-3-5-sonnet", "prompt_tokens": 4100, "completion_tokens": 2900, "latency_ms": 3500},
    {"agent_id": "rd_center", "department_id": "rd_center", "flow_step_id": "qa_gate", "model": "gpt-4o", "prompt_tokens": 600, "completion_tokens": 200, "latency_ms": 680},
]


def _pricing(model: str) -> Dict[str, float]:
    table = {
        "gpt-4o": {"input_per_1k": 0.0025, "output_per_1k": 0.01},
        "claude-3-5-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    }
    return table.get(model, {"input_per_1k": 0.002, "output_per_1k": 0.008})


def _cost(entry: Dict[str, Any]) -> float:
    p = _pricing(str(entry.get("model", "")))
    pt = int(entry.get("prompt_tokens", 0))
    ct = int(entry.get("completion_tokens", 0))
    return round(pt / 1000 * p["input_per_1k"] + ct / 1000 * p["output_per_1k"], 6)


def seed_task_traffic(
    task_id: str,
    *,
    log_path: Optional[Path] = None,
    template: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """追加模拟 traffic 行，供 cost_report 聚合。"""
    path = log_path or DEFAULT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = template or GREENFIELD_TRAFFIC_TEMPLATE
    base_ts = time.time() - 3600
    written = 0
    total_cost = 0.0

    with open(path, "a", encoding="utf-8") as handle:
        for i, row in enumerate(rows):
            entry = dict(row)
            entry["ts"] = base_ts + i * 120
            entry["task_id"] = task_id
            entry["total_tokens"] = int(entry["prompt_tokens"]) + int(entry["completion_tokens"])
            entry["cost_usd"] = _cost(entry)
            total_cost += entry["cost_usd"]
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

    return {"task_id": task_id, "rows_written": written, "estimated_cost_usd": round(total_cost, 6), "log_path": str(path)}
