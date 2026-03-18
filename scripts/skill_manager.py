#!/usr/bin/env python3
"""
CorpPilot Skill Manager
Skill 管理工具。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from core import DEFAULT_DATA_DIR, SKILLS_DIR, SkillCatalogService


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = DEFAULT_DATA_DIR
SKILLS_DIR = SKILLS_DIR
SKILLS_CONFIG = DATA_DIR / "skills.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def load_skills_config() -> Dict:
    return SkillCatalogService(DATA_DIR, SKILLS_DIR)._load()


def save_skills_config(config: Dict) -> None:
    SkillCatalogService(DATA_DIR, SKILLS_DIR)._save(config)


def add_local_skill(skill_id: str, name: str, description: str, agents: List[str], skill_content: str) -> Dict:
    ensure_dirs()
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).add_local_skill(skill_id, name, description, agents, skill_content)


def add_remote_skill(skill_id: str, name: str, description: str, agents: List[str], url: str) -> Dict:
    ensure_dirs()
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).add_remote_skill(skill_id, name, description, agents, url)


def update_skill(skill_id: str, **kwargs) -> Optional[Dict]:
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).update_skill(skill_id, **kwargs)


def remove_skill(skill_id: str) -> bool:
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).remove_skill(skill_id)


def get_skill(skill_id: str) -> Optional[Dict]:
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).get_skill(skill_id)


def list_skills(agent: Optional[str] = None) -> List[Dict]:
    return SkillCatalogService(DATA_DIR, SKILLS_DIR).list_skills(agent)


def print_skill(skill: Dict) -> None:
    print(f"\nSkill: {skill['name']} ({skill['id']})")
    print(f"  类型: {skill.get('type', 'unknown')}")
    print(f"  描述: {skill.get('description', 'N/A')}")
    print(f"  可用 Agent: {', '.join(skill.get('agents', []))}")
    print(f"  更新时间: {skill.get('updated_at', 'N/A')}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot Skill 管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    add_local_parser = subparsers.add_parser("add-local", help="添加本地 Skill")
    add_local_parser.add_argument("--id", required=True, help="Skill ID")
    add_local_parser.add_argument("--name", required=True, help="Skill 名称")
    add_local_parser.add_argument("--description", "-d", default="", help="描述")
    add_local_parser.add_argument("--agents", "-a", required=True, help="可用 Agent，逗号分隔")
    add_local_parser.add_argument("--file", "-f", help="Skill 文件路径")

    add_remote_parser = subparsers.add_parser("add-remote", help="添加远程 Skill")
    add_remote_parser.add_argument("--id", required=True, help="Skill ID")
    add_remote_parser.add_argument("--name", required=True, help="Skill 名称")
    add_remote_parser.add_argument("--description", "-d", default="", help="描述")
    add_remote_parser.add_argument("--agents", "-a", required=True, help="可用 Agent，逗号分隔")
    add_remote_parser.add_argument("--url", required=True, help="远程 URL")

    remove_parser = subparsers.add_parser("remove", help="移除 Skill")
    remove_parser.add_argument("--id", required=True, help="Skill ID")

    list_parser = subparsers.add_parser("list", help="列出 Skills")
    list_parser.add_argument("--agent", help="按 Agent 过滤")

    get_parser = subparsers.add_parser("get", help="获取 Skill 详情")
    get_parser.add_argument("--id", required=True, help="Skill ID")

    args = parser.parse_args()

    if args.command == "add-local":
        agents = [item.strip() for item in args.agents.split(",") if item.strip()]
        content = Path(args.file).read_text(encoding="utf-8") if args.file else f"# {args.name}\n\n{args.description}"
        skill = add_local_skill(args.id, args.name, args.description, agents, content)
        print("本地 Skill 添加成功")
        print_skill(skill)
        return

    if args.command == "add-remote":
        agents = [item.strip() for item in args.agents.split(",") if item.strip()]
        skill = add_remote_skill(args.id, args.name, args.description, agents, args.url)
        print("远程 Skill 添加成功")
        print_skill(skill)
        return

    if args.command == "remove":
        print("Skill 已移除" if remove_skill(args.id) else "Skill 不存在")
        return

    if args.command == "list":
        print(json.dumps(list_skills(args.agent), ensure_ascii=False, indent=2))
        return

    if args.command == "get":
        skill = get_skill(args.id)
        if not skill:
            print(f"Skill 不存在: {args.id}")
            return
        print(json.dumps(skill, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
