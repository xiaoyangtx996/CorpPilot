#!/usr/bin/env python3
"""
CorpPilot Agent Config Sync
Agent 配置同步工具
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
AGENTS_DIR = PROJECT_ROOT / "agents"
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_FILE = DATA_DIR / "agent_config.json"


# Agent 角色定义（参考国内大型互联网公司架构）
AGENT_ROLES = {
    "ceo": {
        "name": "CEO",
        "name_cn": "首席执行官",
        "layer": "decision",
        "description": "战略决策、最终审批"
    },
    "president_office": {
        "name": "总裁办",
        "name_cn": "总裁办公室",
        "layer": "decision",
        "description": "信息枢纽、任务分发"
    },
    "strategy": {
        "name": "战略发展部",
        "name_cn": "战略发展部",
        "layer": "decision",
        "description": "规划中枢、方案设计"
    },
    "risk_center": {
        "name": "风控中心",
        "name_cn": "风险控制中心",
        "layer": "review",
        "description": "风险审核、合规把控"
    },
    "pmo": {
        "name": "PMO",
        "name_cn": "项目管理办公室",
        "layer": "review",
        "description": "项目统筹、资源调度"
    },
    "rd_center": {
        "name": "研发中心",
        "name_cn": "技术研发中心",
        "layer": "execution",
        "description": "技术开发、系统实现"
    },
    "product_center": {
        "name": "产品中心",
        "name_cn": "产品设计中心",
        "layer": "execution",
        "description": "需求分析、产品设计"
    },
    "data_center": {
        "name": "数据中心",
        "name_cn": "数据智能中心",
        "layer": "execution",
        "description": "数据分析、数据治理"
    },
    "operation_center": {
        "name": "运营中心",
        "name_cn": "用户运营中心",
        "layer": "execution",
        "description": "用户增长、活动运营"
    },
    "marketing_center": {
        "name": "市场中心",
        "name_cn": "市场营销中心",
        "layer": "execution",
        "description": "品牌推广、市场营销"
    },
    "finance": {
        "name": "财务部",
        "name_cn": "财务管理部",
        "layer": "support",
        "description": "资源配额管理、成本控制"
    },
    "legal": {
        "name": "法务部",
        "name_cn": "法务合规部",
        "layer": "support",
        "description": "规则合规检查、安全策略"
    },
    "hr": {
        "name": "HR",
        "name_cn": "人力资源部",
        "layer": "support",
        "description": "智能体管理、能力配置"
    }
}


def ensure_data_dir():
    """确保数据目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def parse_soul_md(agent_dir: Path) -> Dict:
    """解析 Agent 的 SOUL.md 文件"""
    soul_file = agent_dir / "SOUL.md"
    
    if not soul_file.exists():
        return {}
    
    with open(soul_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 提取关键信息
    lines = content.split("\n")
    result = {
        "has_soul": True,
        "line_count": len(lines),
        "sections": []
    }
    
    # 提取二级标题作为章节
    for line in lines:
        if line.startswith("## "):
            result["sections"].append(line[3:].strip())
    
    return result


def sync_all_agents() -> Dict:
    """同步所有 Agent 配置"""
    ensure_data_dir()
    
    config = {
        "version": "1.0",
        "synced_at": datetime.now().isoformat(),
        "agents": {}
    }
    
    for role_id, role_info in AGENT_ROLES.items():
        agent_dir = AGENTS_DIR / role_id
        soul_info = parse_soul_md(agent_dir)
        
        agent_config = {
            **role_info,
            "id": role_id,
            "directory": str(agent_dir.relative_to(PROJECT_ROOT)),
            **soul_info
        }
        
        config["agents"][role_id] = agent_config
    
    # 保存配置
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    return config


def get_agent_config(agent_id: str) -> Optional[Dict]:
    """获取单个 Agent 配置"""
    if not CONFIG_FILE.exists():
        sync_all_agents()
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    return config.get("agents", {}).get(agent_id)


def list_agents(layer: Optional[str] = None) -> List[Dict]:
    """列出所有 Agent"""
    if not CONFIG_FILE.exists():
        sync_all_agents()
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    agents = list(config.get("agents", {}).values())
    
    if layer:
        agents = [a for a in agents if a.get("layer") == layer]
    
    return agents


def print_agent_summary(config: Dict):
    """打印 Agent 配置摘要"""
    print("\n🏢 CorpPilot Agent 配置摘要")
    print("=" * 50)
    
    layers = {
        "decision": "决策层",
        "review": "审核层",
        "execution": "执行层",
        "support": "辅助层"
    }
    
    for layer_id, layer_name in layers.items():
        agents = [a for a in config["agents"].values() if a.get("layer") == layer_id]
        if agents:
            print(f"\n【{layer_name}】")
            for agent in agents:
                soul_status = "✅" if agent.get("has_soul") else "❌"
                print(f"  {soul_status} {agent['name']} ({agent['name_cn']})")
                print(f"     {agent['description']}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="CorpPilot Agent 配置同步工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # sync 命令
    subparsers.add_parser("sync", help="同步所有 Agent 配置")
    
    # get 命令
    get_parser = subparsers.add_parser("get", help="获取 Agent 配置")
    get_parser.add_argument("--agent-id", required=True, help="Agent ID")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出 Agent")
    list_parser.add_argument("--layer", choices=["decision", "review", "execution", "support"], help="按层级筛选")
    
    args = parser.parse_args()
    
    if args.command == "sync":
        config = sync_all_agents()
        print_agent_summary(config)
        print(f"\n✅ 配置已同步到: {CONFIG_FILE}")
    
    elif args.command == "get":
        agent = get_agent_config(args.agent_id)
        if agent:
            print(json.dumps(agent, ensure_ascii=False, indent=2))
        else:
            print(f"❌ Agent 不存在: {args.agent_id}")
    
    elif args.command == "list":
        agents = list_agents(layer=args.layer)
        for agent in agents:
            print(f"[{agent['layer']}] {agent['name']}: {agent['description']}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
