#!/usr/bin/env python3
"""项目结案 step：财务部 cost_report + 法务 compliance_report。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def _scan_secrets(task_id: str) -> List[str]:
    from postcondition import check_rule

    ok, msg = check_rule(task_id, "no_secrets_in_repo == true")
    return [] if ok else [msg]


def _list_artifact_files(task_id: str) -> List[str]:
    base = ARTIFACTS_DIR / task_id
    if not base.exists():
        return []
    return sorted(
        str(p.relative_to(base)).replace("\\", "/")
        for p in base.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )[:40]


def write_compliance_report(task_id: str, task_service=None) -> str:
    """生成法务合规报告（含 secrets 扫描 + 产出清单）。"""
    out = ARTIFACTS_DIR / task_id / "compliance_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    secrets = _scan_secrets(task_id)
    files = _list_artifact_files(task_id)
    lines = [
        f"# 合规报告 — {task_id}",
        "",
        "## 敏感文件扫描",
        "- 通过：未发现敏感文件" if not secrets else "- **未通过**",
    ]
    for s in secrets:
        lines.append(f"  - {s}")
    lines.extend(["", "## 任务产出清单", ""])
    for f in files:
        lines.append(f"- `{f}`")
    lines.extend(
        [
            "",
            "## 审查项",
            "- [x] 产出物已归档",
            "- [ ] 第三方依赖许可（人工复核）",
            "- [ ] 隐私条款与用户数据路径（人工复核）",
            "",
            "_由 project_close 自动生成_",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    rel = str(out.relative_to(PROJECT_ROOT))
    if task_service:
        task_service.append_artifacts(
            task_id,
            [{"path": rel, "agent_id": "legal", "type": "compliance_report"}],
            actor="legal",
        )
    return rel


def emit_project_close_artifacts(
    task_id: str,
    task_service=None,
    router=None,
) -> Dict[str, Any]:
    """结案必选产出：cost_report.json + compliance_report.md + finance_brief.md。"""
    from cost_report import build_cost_report, write_cost_report_artifact
    from finance_agent import write_finance_brief

    ts = task_service
    report = write_cost_report_artifact(task_id, router=router, task_service=ts)
    compliance_path = write_compliance_report(task_id, ts)
    brief_path = write_finance_brief(task_id, report, task_service=ts, reason="project_close")

    return {
        "task_id": task_id,
        "cost_report": report,
        "compliance_report": compliance_path,
        "finance_brief": brief_path,
    }


def check_closeout_outputs(task_id: str) -> Dict[str, Any]:
    """验证结案必选文件是否存在。"""
    from postcondition import check_postconditions

    return check_postconditions(
        task_id,
        [],
        ["cost_report.json", "compliance_report.md"],
    )
