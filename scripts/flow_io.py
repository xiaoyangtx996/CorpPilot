#!/usr/bin/env python3
"""Flow 模板导入 / 导出。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from flow_engine import FLOWS_DIR, load_flow, list_flow_ids, normalize_steps

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FLOW_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,48}$")


def export_flow(flow_id: str) -> Dict[str, Any]:
    """导出 Flow 为可序列化 JSON（含完整 steps）。"""
    if flow_id == "legacy":
        raise ValueError("legacy flow 不可导出")
    flow = load_flow(flow_id)
    return {
        "format": "corppilot-flow",
        "version": "1.0",
        "flow": flow,
    }


def export_flow_json(flow_id: str) -> str:
    return json.dumps(export_flow(flow_id), ensure_ascii=False, indent=2)


def _validate_flow_id(flow_id: str) -> None:
    if not flow_id or flow_id == "legacy":
        raise ValueError("无效的 flow id")
    if not FLOW_ID_PATTERN.match(flow_id):
        raise ValueError(f"flow id 格式无效: {flow_id}")


def import_flow(payload: Dict[str, Any], *, overwrite: bool = False) -> Dict[str, Any]:
    """
    导入 Flow 模板到 flows/{id}.json。
    payload 可为 export_flow 返回值或裸 flow 对象。
    """
    FLOWS_DIR.mkdir(parents=True, exist_ok=True)

    if payload.get("format") == "corppilot-flow":
        flow = dict(payload.get("flow") or {})
    else:
        flow = dict(payload)

    flow_id = str(flow.get("id") or "").strip()
    _validate_flow_id(flow_id)

    if not flow.get("steps"):
        raise ValueError("Flow 缺少 steps")

    target = FLOWS_DIR / f"{flow_id}.json"
    existed = target.exists()
    if existed and not overwrite:
        raise ValueError(f"Flow 已存在: {flow_id}，请设置 overwrite=true")

    flow.setdefault("id", flow_id)
    target.write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "flow_id": flow_id,
        "path": str(target.relative_to(PROJECT_ROOT)),
        "step_count": len(flow.get("steps", [])),
        "overwritten": existed,
    }


def list_exportable_flow_ids() -> list:
    return [fid for fid in list_flow_ids() if fid != "legacy"]


def _collect_skipped_step_ids(task: Dict[str, Any], flow_engine: Any = None) -> set:
    skipped: set = set()
    state = task.get("flow_state") or {}
    skipped |= set(state.get("skipped_steps", []))
    if flow_engine:
        skipped |= set(flow_engine.apply_hotfix_skips(task))
        for item in flow_engine.build_steps_timeline(task):
            if item.get("status") == "skipped":
                skipped.add(item["id"])
    flow_id = task.get("flow_id")
    if flow_id:
        flow = load_flow(flow_id)
        for step in normalize_steps(flow):
            if step.get("gate_mode") == "skip":
                skipped.add(step.get("id", ""))
    return {sid for sid in skipped if sid}


def flow_from_task(
    task: Dict[str, Any],
    *,
    new_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    flow_engine: Any = None,
) -> Dict[str, Any]:
    """从任务执行轨迹生成 Flow 模板（保留跳步为 gate_mode: skip）。"""
    _validate_flow_id(new_id)
    source_flow_id = task.get("flow_id")
    if not source_flow_id or source_flow_id == "legacy":
        raise ValueError("legacy 任务无法另存为 Flow 模板")

    base = load_flow(source_flow_id)
    skipped = _collect_skipped_step_ids(task, flow_engine)
    steps: List[Dict[str, Any]] = []
    for step in normalize_steps(base):
        cloned = dict(step)
        sid = cloned.get("id")
        if sid in skipped:
            cloned["gate_mode"] = "skip"
        steps.append(cloned)

    task_id = str(task.get("task_id", ""))
    return {
        "id": new_id,
        "name": name or f"{base.get('name', source_flow_id)} · {task_id}",
        "description": description or f"由任务 {task_id} 另存为模板（源 Flow: {source_flow_id}）",
        "derived_from": {
            "task_id": task_id,
            "source_flow_id": source_flow_id,
            "skipped_steps": sorted(skipped),
        },
        "steps": steps,
    }


def save_task_as_flow(
    task: Dict[str, Any],
    *,
    new_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    flow_engine: Any = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    flow = flow_from_task(
        task,
        new_id=new_id,
        name=name,
        description=description,
        flow_engine=flow_engine,
    )
    result = import_flow(flow, overwrite=overwrite)
    result["derived_from"] = flow.get("derived_from")
    return result
