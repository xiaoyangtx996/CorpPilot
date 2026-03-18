#!/usr/bin/env python3
"""
CorpPilot Task Manager CLI
任务管理命令行工具。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from core import (
    DEFAULT_DATA_DIR,
    VALID_TASK_TRANSITIONS,
    TaskPriority,
    TaskService,
    TaskStatus,
    TaskType,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = DEFAULT_DATA_DIR
TASKS_FILE = DATA_DIR / "tasks.json"
VALID_TRANSITIONS = VALID_TASK_TRANSITIONS


def ensure_data_dir() -> None:
    """确保数据目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_tasks() -> list:
    """加载全部任务。"""
    return TaskService(DATA_DIR).list_tasks(limit=10000)


def save_tasks(tasks: list) -> None:
    """兼容旧接口，直接覆盖任务文件。"""
    from core import JsonStore

    JsonStore(TASKS_FILE).write(tasks)


def generate_task_id() -> str:
    """为兼容旧接口保留。"""
    service = TaskService(DATA_DIR)
    existing = service.list_tasks(limit=10000)
    return service._generate_task_id(existing)  # type: ignore[attr-defined]


def create_task(
    title: str,
    task_type: TaskType,
    priority: TaskPriority,
    requester: str,
    description: str = "",
) -> dict:
    """创建任务。"""
    ensure_data_dir()
    return TaskService(DATA_DIR).create_task(title, task_type, priority, requester, description)


def update_task_status(task_id: str, new_status: TaskStatus, actor: str = "system") -> dict:
    """更新任务状态。"""
    return TaskService(DATA_DIR).update_task_status(task_id, new_status, actor)


def get_task(task_id: str) -> Optional[dict]:
    """获取单个任务。"""
    return TaskService(DATA_DIR).get_task(task_id)


def list_tasks(status: Optional[TaskStatus] = None, limit: int = 20) -> list:
    """列出任务。"""
    return TaskService(DATA_DIR).list_tasks(status=status, limit=limit)


def print_task(task: dict) -> None:
    """打印任务摘要。"""
    print(f"\n任务ID: {task['task_id']}")
    print(f"  标题: {task['title']}")
    print(f"  类型: {task['type']}")
    print(f"  优先级: {task['priority']}")
    print(f"  状态: {task['status']}")
    print(f"  当前责任方: {task.get('current_owner', '-')}")
    print(f"  执行责任方: {task.get('execution_owner', '-')}")
    print(f"  请求者: {task['requester']}")
    print(f"  创建时间: {task['created_at']}")


def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot 任务管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    create_parser = subparsers.add_parser("create", help="创建任务")
    create_parser.add_argument("--title", "-t", required=True, help="任务标题")
    create_parser.add_argument("--type", choices=[item.value for item in TaskType], required=True, help="任务类型")
    create_parser.add_argument("--priority", "-p", choices=[item.value for item in TaskPriority], default=TaskPriority.P2.value, help="优先级")
    create_parser.add_argument("--requester", "-r", required=True, help="请求者")
    create_parser.add_argument("--description", "-d", default="", help="任务描述")

    status_parser = subparsers.add_parser("status", help="更新任务状态")
    status_parser.add_argument("--task-id", required=True, help="任务ID")
    status_parser.add_argument("--new-status", choices=[item.value for item in TaskStatus], required=True, help="新状态")
    status_parser.add_argument("--actor", default="system", help="操作者")

    get_parser = subparsers.add_parser("get", help="查看任务")
    get_parser.add_argument("--task-id", required=True, help="任务ID")

    list_parser = subparsers.add_parser("list", help="列出任务")
    list_parser.add_argument("--status", choices=[item.value for item in TaskStatus], help="状态过滤")
    list_parser.add_argument("--limit", "-l", type=int, default=20, help="数量限制")

    args = parser.parse_args()

    if args.command == "create":
        task = create_task(
            title=args.title,
            task_type=TaskType(args.type),
            priority=TaskPriority(args.priority),
            requester=args.requester,
            description=args.description,
        )
        print("任务创建成功")
        print_task(task)
        return

    if args.command == "status":
        try:
            task = update_task_status(args.task_id, TaskStatus(args.new_status), args.actor)
            print("状态更新成功")
            print_task(task)
        except ValueError as exc:
            print(f"错误: {exc}")
            sys.exit(1)
        return

    if args.command == "get":
        task = get_task(args.task_id)
        if not task:
            print(f"任务不存在: {args.task_id}")
            sys.exit(1)
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return

    if args.command == "list":
        status = TaskStatus(args.status) if args.status else None
        tasks = list_tasks(status=status, limit=args.limit)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
