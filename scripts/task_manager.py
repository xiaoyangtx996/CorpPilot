#!/usr/bin/env python3
"""
CorpPilot Task Manager CLI
任务管理命令行工具
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from enum import Enum

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"           # 待处理
    CLASSIFIED = "classified"     # 已分拣
    PLANNED = "planned"          # 已规划
    REVIEWING = "reviewing"      # 审核中
    APPROVED = "approved"        # 已通过
    REJECTED = "rejected"        # 已驳回
    DISPATCHED = "dispatched"    # 已派发
    EXECUTING = "executing"      # 执行中
    REVIEW = "review"            # 待审查
    COMPLETED = "completed"      # 已完成
    BLOCKED = "blocked"          # 阻塞


class TaskType(str, Enum):
    """任务类型枚举（国内大厂架构）"""
    RD = "RD"         # 技术研发
    PD = "PD"         # 产品设计
    DA = "DA"         # 数据分析
    OP = "OP"         # 运营推广
    MK = "MK"         # 市场营销
    FN = "FN"         # 财务预算
    HR = "HR"         # 人力招聘
    LG = "LG"         # 法务合规


class TaskPriority(str, Enum):
    """任务优先级枚举"""
    P0 = "P0"  # 紧急
    P1 = "P1"  # 高
    P2 = "P2"  # 中
    P3 = "P3"  # 低


# 状态转换规则（状态机）
VALID_TRANSITIONS = {
    TaskStatus.PENDING: [TaskStatus.CLASSIFIED],
    TaskStatus.CLASSIFIED: [TaskStatus.PLANNED],
    TaskStatus.PLANNED: [TaskStatus.REVIEWING],
    TaskStatus.REVIEWING: [TaskStatus.APPROVED, TaskStatus.REJECTED],
    TaskStatus.REJECTED: [TaskStatus.PLANNED],  # 驳回后可重新规划
    TaskStatus.APPROVED: [TaskStatus.DISPATCHED],
    TaskStatus.DISPATCHED: [TaskStatus.EXECUTING],
    TaskStatus.EXECUTING: [TaskStatus.REVIEW, TaskStatus.BLOCKED],
    TaskStatus.BLOCKED: [TaskStatus.EXECUTING],
    TaskStatus.REVIEW: [TaskStatus.COMPLETED, TaskStatus.EXECUTING],
    TaskStatus.COMPLETED: [],  # 终态
}


def ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TASKS_FILE.exists():
        save_tasks([])


def load_tasks() -> list:
    """加载所有任务"""
    if not TASKS_FILE.exists():
        return []
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tasks(tasks: list):
    """保存所有任务"""
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def generate_task_id() -> str:
    """生成任务ID"""
    year = datetime.now().year
    tasks = load_tasks()
    count = len([t for t in tasks if t.get("task_id", "").startswith(f"TASK-{year}")]) + 1
    return f"TASK-{year}-{count:04d}"


def create_task(
    title: str,
    task_type: TaskType,
    priority: TaskPriority,
    requester: str,
    description: str = ""
) -> dict:
    """创建新任务"""
    ensure_data_dir()
    task = {
        "task_id": generate_task_id(),
        "title": title,
        "type": task_type.value,
        "priority": priority.value,
        "requester": requester,
        "description": description,
        "status": TaskStatus.PENDING.value,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "history": [
            {
                "action": "created",
                "timestamp": datetime.now().isoformat(),
                "actor": requester
            }
        ]
    }
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    return task


def update_task_status(task_id: str, new_status: TaskStatus, actor: str = "system") -> dict:
    """更新任务状态（带状态机校验）"""
    tasks = load_tasks()
    task = next((t for t in tasks if t["task_id"] == task_id), None)
    
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    
    current_status = TaskStatus(task["status"])
    
    # 状态机校验
    if new_status not in VALID_TRANSITIONS.get(current_status, []):
        raise ValueError(
            f"非法状态转换: {current_status.value} -> {new_status.value}\n"
            f"允许的转换: {[s.value for s in VALID_TRANSITIONS.get(current_status, [])]}"
        )
    
    task["status"] = new_status.value
    task["updated_at"] = datetime.now().isoformat()
    task["history"].append({
        "action": f"status_change:{current_status.value}->{new_status.value}",
        "timestamp": datetime.now().isoformat(),
        "actor": actor
    })
    
    save_tasks(tasks)
    return task


def get_task(task_id: str) -> Optional[dict]:
    """获取单个任务"""
    tasks = load_tasks()
    return next((t for t in tasks if t["task_id"] == task_id), None)


def list_tasks(status: Optional[TaskStatus] = None, limit: int = 20) -> list:
    """列出任务"""
    tasks = load_tasks()
    if status:
        tasks = [t for t in tasks if t["status"] == status.value]
    return tasks[:limit]


def print_task(task: dict):
    """打印任务信息"""
    print(f"\n📋 任务ID: {task['task_id']}")
    print(f"   标题: {task['title']}")
    print(f"   类型: {task['type']}")
    print(f"   优先级: {task['priority']}")
    print(f"   状态: {task['status']}")
    print(f"   请求者: {task['requester']}")
    print(f"   创建时间: {task['created_at']}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="CorpPilot 任务管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # create 命令
    create_parser = subparsers.add_parser("create", help="创建任务")
    create_parser.add_argument("--title", "-t", required=True, help="任务标题")
    create_parser.add_argument("--type", choices=[t.value for t in TaskType], required=True, help="任务类型")
    create_parser.add_argument("--priority", "-p", choices=[p.value for p in TaskPriority], default="P2", help="优先级")
    create_parser.add_argument("--requester", "-r", required=True, help="请求者")
    create_parser.add_argument("--description", "-d", default="", help="任务描述")
    
    # status 命令
    status_parser = subparsers.add_parser("status", help="更新任务状态")
    status_parser.add_argument("--task-id", required=True, help="任务ID")
    status_parser.add_argument("--new-status", choices=[s.value for s in TaskStatus], required=True, help="新状态")
    status_parser.add_argument("--actor", default="system", help="操作者")
    
    # get 命令
    get_parser = subparsers.add_parser("get", help="查看任务")
    get_parser.add_argument("--task-id", required=True, help="任务ID")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出任务")
    list_parser.add_argument("--status", choices=[s.value for s in TaskStatus], help="按状态筛选")
    list_parser.add_argument("--limit", "-l", type=int, default=20, help="数量限制")
    
    args = parser.parse_args()
    
    if args.command == "create":
        task = create_task(
            title=args.title,
            task_type=TaskType(args.type),
            priority=TaskPriority(args.priority),
            requester=args.requester,
            description=args.description
        )
        print("✅ 任务创建成功")
        print_task(task)
    
    elif args.command == "status":
        try:
            task = update_task_status(
                task_id=args.task_id,
                new_status=TaskStatus(args.new_status),
                actor=args.actor
            )
            print("✅ 状态更新成功")
            print_task(task)
        except ValueError as e:
            print(f"❌ 错误: {e}")
            sys.exit(1)
    
    elif args.command == "get":
        task = get_task(args.task_id)
        if task:
            print_task(task)
        else:
            print(f"❌ 任务不存在: {args.task_id}")
            sys.exit(1)
    
    elif args.command == "list":
        status = TaskStatus(args.status) if args.status else None
        tasks = list_tasks(status=status, limit=args.limit)
        if tasks:
            print(f"\n📋 任务列表 (共 {len(tasks)} 个)")
            for task in tasks:
                print(f"   [{task['status']}] {task['task_id']}: {task['title']}")
        else:
            print("暂无任务")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
