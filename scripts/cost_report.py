#!/usr/bin/env python3
"""任务级 Token 成本报告（财务部）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = PROJECT_ROOT / "data" / "traffic_logs.jsonl"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def _read_logs(task_id: str, log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = log_path or DEFAULT_LOG
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("task_id") == task_id:
                rows.append(row)
    return rows


def build_cost_report(
    task_id: str,
    router=None,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    rows = _read_logs(task_id, log_path)
    by_agent: Dict[str, Dict[str, float]] = {}
    by_model: Dict[str, Dict[str, float]] = {}
    by_department: Dict[str, Dict[str, float]] = {}
    total_tokens = 0
    total_cost = 0.0

    for r in rows:
        agent = r.get("agent_id", "unknown")
        model = r.get("model", "unknown")
        dept = r.get("department_id") or "unknown"
        toks = int(r.get("total_tokens", 0))
        cost = float(r.get("cost_usd", 0))
        total_tokens += toks
        total_cost += cost
        for bucket, key in (
            (by_agent, agent),
            (by_model, model),
            (by_department, dept),
        ):
            if key not in bucket:
                bucket[key] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            bucket[key]["calls"] += 1
            bucket[key]["tokens"] += toks
            bucket[key]["cost_usd"] += cost

    budget_usd = 5.0
    if router:
        cfg = getattr(router, "_config", None) or getattr(router, "config", None) or {}
        if isinstance(cfg, dict):
            budgets = cfg.get("traffic", {}).get("budgets", {})
            budget_usd = float(budgets.get("per_task_default_usd", budget_usd))

    over_budget = total_cost > budget_usd
    return {
        "task_id": task_id,
        "total_calls": len(rows),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "budget_usd": budget_usd,
        "over_budget": over_budget,
        "by_agent": by_agent,
        "by_model": by_model,
        "by_department": by_department,
    }


def write_cost_report_artifact(
    task_id: str,
    router=None,
    task_service=None,
) -> Dict[str, Any]:
    report = build_cost_report(task_id, router=router)
    out_dir = ARTIFACTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cost_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if task_service:
        task_service.append_artifacts(
            task_id,
            [
                {
                    "path": str(out_path.relative_to(PROJECT_ROOT)),
                    "agent_id": "finance",
                    "summary": "任务成本报告",
                    "type": "cost_report",
                }
            ],
            actor="finance",
        )
    return report


def emit_budget_alert_if_needed(
    task_id: str,
    report: Dict[str, Any],
    event_log=None,
    task_service=None,
) -> Optional[Dict[str, Any]]:
    """超预算时写入财务部事件，供 Dashboard 展示。"""
    if not report.get("over_budget"):
        return None
    alert = {
        "task_id": task_id,
        "total_cost_usd": report.get("total_cost_usd"),
        "budget_usd": report.get("budget_usd"),
        "message": f"任务 {task_id} 预估成本 ${report.get('total_cost_usd')} 超过预算 ${report.get('budget_usd')}",
    }
    if event_log:
        event_log.append(
            "finance",
            "budget_exceeded",
            "finance",
            task_id,
            alert,
        )
        try:
            from finance_agent import write_finance_brief
            write_finance_brief(task_id, report, task_service=task_service, reason="budget_exceeded")
        except Exception:
            pass
    return alert
