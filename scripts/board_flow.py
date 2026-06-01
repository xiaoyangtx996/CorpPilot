#!/usr/bin/env python3
"""董事会下令与 Flow 编排的解析与执行。"""
from __future__ import annotations

import re
from typing import List, Optional

from flow_engine import load_flow, normalize_steps


# 紧急下令常用语义 → 默认跳过的 step id
_EMERGENCY_SKIP_HINTS = {
    "demo": ["product_demo"],
    "产品": ["product_demo"],
    "prd": ["product_demo", "prd_generation"],
    "讨论": ["board_discussion"],
    "风控": ["risk_gate"],
    "qa": ["qa_gate"],
    "验收": ["qa_gate", "project_close"],
}


def parse_skip_steps_from_order(order: str, flow_id: Optional[str] = None) -> List[str]:
    """
    从董事长下令文本解析要跳过的 flow step。
    支持：显式 step_id、关键词、skip:step_a,step_b 格式。
    """
    if not order:
        return []

    found: List[str] = []
    text = order.strip()

    m = re.search(r"skip\s*[:：]\s*([\w,\s/-]+)", text, re.I)
    if m:
        for part in re.split(r"[,，\s]+", m.group(1)):
            part = part.strip()
            if part:
                found.append(part)

    if flow_id and flow_id not in ("legacy", ""):
        try:
            flow = load_flow(flow_id)
            for step in normalize_steps(flow):
                sid = step.get("id", "")
                if sid and sid in text:
                    found.append(sid)
        except Exception:
            pass

    lower = text.lower()
    for hint, step_ids in _EMERGENCY_SKIP_HINTS.items():
        if hint in lower or hint in text:
            found.extend(step_ids)

    if any(k in text for k in ("紧急", "hotfix", "直接开发", "跳过审批", "跳过流程")):
        found.extend(["board_discussion", "product_demo", "prd_generation"])

    # 去重保序
    seen = set()
    out: List[str] = []
    for sid in found:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def apply_direct_order_to_task(
    task_id: str,
    order: str,
    flow_engine,
    actor: str = "chairman",
    step_ids: Optional[List[str]] = None,
) -> dict:
    """将 direct_order 作用于指定任务的 Flow。"""
    task = flow_engine.task_service.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    flow_id = task.get("flow_id", "legacy")
    if flow_id == "legacy":
        raise ValueError("仅 flow 任务支持董事长跳步")

    skips = list(step_ids or []) or parse_skip_steps_from_order(order, flow_id)
    if not skips:
        raise ValueError("未能从下令内容解析出要跳过的 step，请传 step_ids")

    updated = flow_engine.skip_remaining(task_id, skips, actor)
    return {"task_id": task_id, "skipped_steps": skips, "task": updated}
