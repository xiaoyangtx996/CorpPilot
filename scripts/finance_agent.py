#!/usr/bin/env python3
"""财务部 Agent 摘要。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def write_finance_brief(task_id: str, report: Dict[str, Any], task_service=None, reason: str = "budget_exceeded") -> str:
    out_dir = ARTIFACTS_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "finance_brief.md"
    by_dept = report.get("by_department") or {}
    lines = [
        f"# 财务部摘要 — {task_id}",
        f"触发: {reason}",
        f"总 Token: {report.get('total_tokens', 0):,}",
        f"成本: ${report.get('total_cost_usd', 0):.6f} / 预算 ${report.get('budget_usd', 0):.2f}",
        f"超预算: {'是' if report.get('over_budget') else '否'}",
        "",
        "## 按部门",
    ]
    for dept, info in sorted(by_dept.items(), key=lambda x: -x[1].get("cost_usd", 0)):
        lines.append(f"- {dept}: ${info.get('cost_usd', 0):.4f}")
    path.write_text("\n".join(lines), encoding="utf-8")
    rel = str(path.relative_to(PROJECT_ROOT))
    if task_service:
        task_service.append_artifacts(
            task_id,
            [{"path": rel, "agent_id": "finance", "type": "finance_brief"}],
            actor="finance",
        )
    return rel
