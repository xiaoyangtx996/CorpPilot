#!/usr/bin/env python3
"""
CorpPilot Sample Data Generator
生成示例数据用于演示和测试
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import random

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
AGENT_CONFIG_FILE = DATA_DIR / "agent_config.json"
SKILLS_CONFIG_FILE = DATA_DIR / "skills.json"


# 示例任务模板
SAMPLE_TASKS = [
    {
        "title": "用户增长系统开发",
        "type": "RD",
        "priority": "P1",
        "requester": "CEO",
        "description": "开发一套完整的用户增长系统，包括数据采集、分析看板、自动化运营等功能"
    },
    {
        "title": "产品首页改版设计",
        "type": "PD",
        "priority": "P1",
        "requester": "产品中心",
        "description": "对产品首页进行全面改版，提升用户体验和转化率"
    },
    {
        "title": "用户行为数据分析报告",
        "type": "DA",
        "priority": "P2",
        "requester": "运营中心",
        "description": "分析近3个月用户行为数据，输出增长洞察报告"
    },
    {
        "title": "双十一活动策划",
        "type": "OP",
        "priority": "P0",
        "requester": "市场中心",
        "description": "策划双十一大型促销活动，包括活动机制、推广渠道、预算分配"
    },
    {
        "title": "品牌宣传片制作",
        "type": "MK",
        "priority": "P2",
        "requester": "CEO",
        "description": "制作企业品牌宣传片，用于官网和社交媒体传播"
    },
    {
        "title": "年度预算编制",
        "type": "FN",
        "priority": "P1",
        "requester": "CEO",
        "description": "编制下一年度各部门预算，包括人力、技术、市场等费用"
    },
    {
        "title": "高级后端工程师招聘",
        "type": "HR",
        "priority": "P1",
        "requester": "研发中心",
        "description": "招聘3名高级后端工程师，支持核心系统开发"
    },
    {
        "title": "用户协议合规审查",
        "type": "LG",
        "priority": "P2",
        "requester": "产品中心",
        "description": "审查新版用户协议，确保符合最新法规要求"
    }
]

# 任务状态流转
STATUS_FLOW = [
    "pending",
    "classified", 
    "planned",
    "reviewing",
    "approved",
    "dispatched",
    "executing",
    "review",
    "completed"
]


def ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def generate_task_id(year: int, count: int) -> str:
    """生成任务ID"""
    return f"TASK-{year}-{count:04d}"


def create_sample_tasks(count: int = 8) -> list:
    """创建示例任务"""
    tasks = []
    year = datetime.now().year
    
    for i, template in enumerate(SAMPLE_TASKS[:count]):
        task_id = generate_task_id(year, i + 1)
        
        # 随机设置任务状态（模拟不同阶段的任务）
        status_index = random.randint(0, min(i + 2, len(STATUS_FLOW) - 1))
        status = STATUS_FLOW[status_index]
        
        # 创建时间（模拟历史任务）
        created_days_ago = random.randint(1, 30)
        created_at = datetime.now() - timedelta(days=created_days_ago)
        
        task = {
            "task_id": task_id,
            "title": template["title"],
            "type": template["type"],
            "priority": template["priority"],
            "requester": template["requester"],
            "description": template["description"],
            "status": status,
            "created_at": created_at.isoformat(),
            "updated_at": (created_at + timedelta(days=random.randint(0, created_days_ago))).isoformat(),
            "history": [
                {
                    "action": "created",
                    "timestamp": created_at.isoformat(),
                    "actor": template["requester"]
                }
            ]
        }
        
        # 添加状态变更历史
        for j in range(1, status_index + 1):
            prev_status = STATUS_FLOW[j - 1]
            curr_status = STATUS_FLOW[j]
            task["history"].append({
                "action": f"status_change:{prev_status}->{curr_status}",
                "timestamp": (created_at + timedelta(hours=j * 2)).isoformat(),
                "actor": get_actor_for_status(curr_status)
            })
        
        tasks.append(task)
    
    return tasks


def get_actor_for_status(status: str) -> str:
    """根据状态获取执行者"""
    actors = {
        "classified": "president_office",
        "planned": "strategy",
        "reviewing": "risk_center",
        "approved": "risk_center",
        "rejected": "risk_center",
        "dispatched": "pmo",
        "executing": "rd_center",
        "review": "pmo",
        "completed": "pmo"
    }
    return actors.get(status, "system")


def create_sample_skills() -> list:
    """创建示例 Skills"""
    skills = [
        {
            "id": "code_review",
            "name": "代码审查",
            "description": "对代码进行全面审查，包括代码质量、安全性、性能等方面",
            "type": "local",
            "agents": ["rd_center", "risk_center"],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        },
        {
            "id": "data_analysis",
            "name": "数据分析",
            "description": "对数据进行深度分析，输出洞察报告",
            "type": "local",
            "agents": ["data_center", "operation_center"],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        },
        {
            "id": "market_research",
            "name": "市场调研",
            "description": "进行市场调研，分析竞品和用户需求",
            "type": "local",
            "agents": ["marketing_center", "product_center"],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        },
        {
            "id": "risk_assessment",
            "name": "风险评估",
            "description": "对项目或方案进行全面风险评估",
            "type": "local",
            "agents": ["risk_center", "legal"],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
    ]
    return skills


def save_tasks(tasks: list):
    """保存任务数据"""
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def save_skills(skills: list):
    """保存 Skills 数据"""
    with open(SKILLS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"skills": {s["id"]: s for s in skills}}, f, ensure_ascii=False, indent=2)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="CorpPilot 示例数据生成器")
    parser.add_argument("--tasks", type=int, default=8, help="生成任务数量")
    parser.add_argument("--force", "-f", action="store_true", help="强制覆盖现有数据")
    
    args = parser.parse_args()
    
    ensure_data_dir()
    
    # 检查是否已有数据
    if TASKS_FILE.exists() and not args.force:
        print("⚠️  数据文件已存在，使用 --force 参数覆盖")
        return
    
    # 生成任务
    print(f"📝 生成 {args.tasks} 个示例任务...")
    tasks = create_sample_tasks(args.tasks)
    save_tasks(tasks)
    print(f"✅ 任务数据已保存到: {TASKS_FILE}")
    
    # 生成 Skills
    print("🔧 生成示例 Skills...")
    skills = create_sample_skills()
    save_skills(skills)
    print(f"✅ Skills 数据已保存到: {SKILLS_CONFIG_FILE}")
    
    # 同步 Agent 配置
    print("👥 同步 Agent 配置...")
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from sync_agent_config import sync_all_agents
    config = sync_all_agents()
    print(f"✅ Agent 配置已同步到: {AGENT_CONFIG_FILE}")
    
    # 输出统计
    print("\n" + "=" * 50)
    print("📊 数据统计")
    print("=" * 50)
    print(f"任务总数: {len(tasks)}")
    print(f"Skills 总数: {len(skills)}")
    print(f"Agent 总数: {len(config['agents'])}")
    
    # 按状态统计
    status_counts = {}
    for task in tasks:
        status = task["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    
    print("\n任务状态分布:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    
    print("\n✨ 示例数据生成完成！")
    print("🚀 启动服务: python dashboard/server.py")


if __name__ == "__main__":
    main()
