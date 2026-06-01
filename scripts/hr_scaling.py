#!/usr/bin/env python3
"""HR 动态 Agent 扩缩容（flow 分支 replicas）与部门名册 runtime 同步。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ACTIVE_TASK_STATUSES = frozenset({"dispatched", "executing", "review"})


def expand_parallel_branches(branches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 branch.replicas 展开为独立 agent 实例。"""
    expanded: List[Dict[str, Any]] = []
    for branch in branches:
        replicas = max(1, int(branch.get("replicas", 1)))
        base_id = branch.get("id") or branch.get("role", "agent")
        role = branch.get("role", "rd_center")
        for i in range(replicas):
            item = dict(branch)
            if replicas > 1:
                item["instance_id"] = f"{base_id}_{i + 1:03d}"
                item["spawn_agent_id"] = f"{role}_{i + 1:03d}"
            else:
                item["instance_id"] = base_id
                item["spawn_agent_id"] = role
            expanded.append(item)
    return expanded


def register_dynamic_agents(task_id: str, branches: List[Dict[str, Any]], task_service) -> None:
    """记录 HR 动态编制到 task.runtime（含全部并行分支）。"""
    dynamic = [
        {
            "agent_id": b.get("spawn_agent_id"),
            "role": b.get("role"),
            "instance_id": b.get("instance_id"),
            "branch_id": b.get("id"),
            "task_id": task_id,
        }
        for b in branches
    ]
    if dynamic:
        task_service.patch_runtime(task_id, {"hr_dynamic_agents": dynamic})


def release_dynamic_agents(task_id: str, task_service) -> None:
    """任务结案后回收 runtime 动态编制。"""
    task_service.patch_runtime(task_id, {"hr_dynamic_agents": []})


def role_to_department(role: str) -> str:
    """角色 ID 即部门 ID（AGENT_ROLES 一一对应）。"""
    return role or "rd_center"


def format_runtime_dynamic_agent(entry: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    instance = entry.get("instance_id") or entry.get("agent_id", "")
    return {
        "agent_id": entry.get("agent_id"),
        "role": entry.get("role", ""),
        "label": f"临时 · {instance}",
        "task_id": entry.get("task_id") or task.get("task_id"),
        "instance_id": instance,
        "branch_id": entry.get("branch_id"),
        "is_dynamic": True,
        "source": "runtime",
    }


def collect_runtime_dynamic_agents(tasks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """从活跃任务的 runtime 收集按部门分组的动态 Agent。"""
    by_dept: Dict[str, List[Dict[str, Any]]] = {}
    seen: Set[str] = set()
    for task in tasks:
        if task.get("status") not in ACTIVE_TASK_STATUSES:
            continue
        for entry in (task.get("runtime") or {}).get("hr_dynamic_agents") or []:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("agent_id") or "")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            dept = role_to_department(str(entry.get("role") or ""))
            by_dept.setdefault(dept, []).append(format_runtime_dynamic_agent(entry, task))
    return by_dept


def merge_runtime_dynamic_agents(roster: Dict[str, Any], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将 runtime 动态 Agent 合并进部门名册视图（不持久化写入 roster 文件）。"""
    runtime_by_dept = collect_runtime_dynamic_agents(tasks)
    departments = roster.setdefault("departments", {})
    for dept_id, dept_data in departments.items():
        static = list(dept_data.get("dynamic_agents") or [])
        static_ids = {str(a.get("agent_id")) for a in static if a.get("agent_id")}
        runtime_added = [
            a for a in runtime_by_dept.get(dept_id, [])
            if str(a.get("agent_id")) not in static_ids
        ]
        dept_data["dynamic_agents"] = static + runtime_added
        dept_data["runtime_dynamic_count"] = len(runtime_added)
    return roster


def bootstrap_department_roster(agent_roles: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """从 AGENT_ROLES 生成默认部门名册。"""
    departments: Dict[str, Any] = {}
    for role_id, info in agent_roles.items():
        name = info.get("name_cn") or info.get("name") or role_id
        departments[role_id] = {
            "id": role_id,
            "head": {
                "agent_id": role_id,
                "role": name,
                "label": name,
            },
            "default_roles": [],
            "dynamic_agents": [],
        }
    return {"version": "1.0", "departments": departments}


def load_or_bootstrap_roster(data_dir: Path, agent_roles: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """加载部门名册；缺失时从 AGENT_ROLES 引导生成。"""
    roster_path = data_dir / "department_roster.json"
    if roster_path.exists():
        try:
            with open(roster_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            pass
    roster = bootstrap_department_roster(agent_roles)
    data_dir.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2), encoding="utf-8")
    return roster
