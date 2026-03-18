#!/usr/bin/env python3
"""
CorpPilot Sample Data Generator
生成演示任务、Skills 与 Agent 配置。
"""

from __future__ import annotations

import json
from pathlib import Path

from core import (
    AgentCatalogService,
    SkillCatalogService,
    TaskPriority,
    TaskService,
    TaskStatus,
    TaskType,
    WorkflowEngine,
    build_sample_skills,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_TASKS = [
    {"title": "\u7528\u6237\u589e\u957f\u7cfb\u7edf\u5f00\u53d1", "type": TaskType.RD, "priority": TaskPriority.P1, "requester": "CEO", "description": "\u5f00\u53d1\u5b8c\u6574\u7684\u7528\u6237\u589e\u957f\u7cfb\u7edf\u3002"},
    {"title": "\u4ea7\u54c1\u9996\u9875\u6539\u7248\u8bbe\u8ba1", "type": TaskType.PD, "priority": TaskPriority.P1, "requester": "product_center", "description": "\u5b8c\u6210\u9996\u9875\u4fe1\u606f\u67b6\u6784\u4e0e\u89c6\u89c9\u6539\u7248\u3002"},
    {"title": "\u7528\u6237\u884c\u4e3a\u6570\u636e\u5206\u6790\u62a5\u544a", "type": TaskType.DA, "priority": TaskPriority.P2, "requester": "operation_center", "description": "\u8f93\u51fa\u6700\u8fd1\u4e00\u4e2a\u6708\u7684\u884c\u4e3a\u5206\u6790\u62a5\u544a\u3002"},
    {"title": "\u53cc\u5341\u4e00\u6d3b\u52a8\u7b56\u5212", "type": TaskType.OP, "priority": TaskPriority.P0, "requester": "marketing_center", "description": "\u5236\u5b9a\u5927\u4fc3\u6d3b\u52a8\u7b56\u7565\u4e0e\u6267\u884c\u8ba1\u5212\u3002"},
    {"title": "\u54c1\u724c\u5ba3\u4f20\u7247\u5236\u4f5c", "type": TaskType.MK, "priority": TaskPriority.P2, "requester": "CEO", "description": "\u5236\u4f5c\u4f01\u4e1a\u54c1\u724c\u5ba3\u4f20\u7247\u3002"},
    {"title": "\u5e74\u5ea6\u9884\u7b97\u7f16\u5236", "type": TaskType.FN, "priority": TaskPriority.P1, "requester": "CEO", "description": "\u7f16\u5236\u4e0b\u4e00\u8d22\u5e74\u7684\u9884\u7b97\u8349\u6848\u3002"},
    {"title": "\u9ad8\u7ea7\u540e\u7aef\u5de5\u7a0b\u5e08\u62db\u8058", "type": TaskType.HR, "priority": TaskPriority.P1, "requester": "rd_center", "description": "\u62db\u8058 3 \u540d\u9ad8\u7ea7\u540e\u7aef\u5de5\u7a0b\u5e08\u3002"},
    {"title": "\u7528\u6237\u534f\u8bae\u5408\u89c4\u5ba1\u67e5", "type": TaskType.LG, "priority": TaskPriority.P2, "requester": "product_center", "description": "\u5ba1\u67e5\u65b0\u7248\u7528\u6237\u534f\u8bae\u3002"},
]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot 示例数据生成器")
    parser.add_argument("--tasks", type=int, default=8, help="生成任务数量")
    parser.add_argument("--force", "-f", action="store_true", help="强制覆盖现有数据")
    args = parser.parse_args()

    task_service = TaskService()
    workflow = WorkflowEngine(task_service)
    skill_service = SkillCatalogService()
    agent_service = AgentCatalogService()

    data_dir = task_service.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    tasks_file = data_dir / "tasks.json"
    if tasks_file.exists() and not args.force:
        print("数据文件已存在，使用 --force 覆盖")
        return

    task_service.store.reset([])
    (task_service.data_dir / "proposals.json").write_text("[]", encoding="utf-8")

    tasks = []
    for sample in SAMPLE_TASKS[: args.tasks]:
        task = task_service.create_task(
            title=sample["title"],
            task_type=sample["type"],
            priority=sample["priority"],
            requester=sample["requester"],
            description=sample["description"],
        )
        tasks.append(task)

    advance_map = {
        1: [TaskStatus.CLASSIFIED],
        2: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED],
        3: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING],
        4: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED],
        5: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED, TaskStatus.DISPATCHED],
        6: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED, TaskStatus.DISPATCHED, TaskStatus.EXECUTING],
        7: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED, TaskStatus.DISPATCHED, TaskStatus.EXECUTING, TaskStatus.REVIEW],
        8: [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED, TaskStatus.DISPATCHED, TaskStatus.EXECUTING, TaskStatus.REVIEW, TaskStatus.COMPLETED],
    }

    for index, task in enumerate(tasks, start=1):
        for status in advance_map.get(index, []):
            workflow.transition(task["task_id"], status, actor=f"sample:{status.value}")

    skill_config = {"skills": {skill["id"]: skill for skill in build_sample_skills()}}
    skill_service._save(skill_config)
    agent_config = agent_service.sync_all_agents()

    print(json.dumps({"tasks": task_service.list_tasks(limit=100), "skills": skill_service.list_skills(), "agents": len(agent_config["agents"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
