#!/usr/bin/env python3
"""
CorpPilot Agent Config Sync
Agent 配置同步工具。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from core import AGENT_ROLES, AgentCatalogService, DEFAULT_DATA_DIR


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / "agents"
DATA_DIR = DEFAULT_DATA_DIR
CONFIG_FILE = DATA_DIR / "agent_config.json"
AGENT_ROLES = AGENT_ROLES


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def parse_soul_md(agent_dir: Path) -> Dict:
    return AgentCatalogService(DATA_DIR, AGENTS_DIR).parse_soul_md(agent_dir)


def sync_all_agents() -> Dict:
    ensure_data_dir()
    return AgentCatalogService(DATA_DIR, AGENTS_DIR).sync_all_agents()


def get_agent_config(agent_id: str) -> Optional[Dict]:
    return AgentCatalogService(DATA_DIR, AGENTS_DIR).get_agent(agent_id)


def list_agents(layer: Optional[str] = None) -> List[Dict]:
    return AgentCatalogService(DATA_DIR, AGENTS_DIR).list_agents(layer)


def print_agent_summary(config: Dict) -> None:
    print("\nCorpPilot Agent 配置摘要")
    print("=" * 50)
    layers = {
        "decision": "决策层",
        "review": "审核层",
        "execution": "执行层",
        "support": "支持层",
    }
    for layer_id, layer_name in layers.items():
        agents = [agent for agent in config["agents"].values() if agent.get("layer") == layer_id]
        if not agents:
            continue
        print(f"\n[{layer_name}]")
        for agent in agents:
            soul_status = "已加载 SOUL" if agent.get("has_soul") else "缺少 SOUL"
            print(f"  - {agent['name']} ({agent['name_cn']}) / {soul_status}")
            print(f"    {agent['description']}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot Agent 配置同步工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    subparsers.add_parser("sync", help="同步全部 Agent 配置")

    get_parser = subparsers.add_parser("get", help="获取 Agent 配置")
    get_parser.add_argument("--agent-id", required=True, help="Agent ID")

    list_parser = subparsers.add_parser("list", help="列出 Agent")
    list_parser.add_argument("--layer", choices=["decision", "review", "execution", "support"], help="层级过滤")

    args = parser.parse_args()

    if args.command == "sync":
        config = sync_all_agents()
        print_agent_summary(config)
        print(f"\n配置已同步到: {CONFIG_FILE}")
        return

    if args.command == "get":
        agent = get_agent_config(args.agent_id)
        if not agent:
            print(f"Agent 不存在: {args.agent_id}")
            return
        print(json.dumps(agent, ensure_ascii=False, indent=2))
        return

    if args.command == "list":
        print(json.dumps(list_agents(args.layer), ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
