#!/usr/bin/env python3
"""
CorpPilot Skill Manager
Skill 管理工具 - 远程/本地 Skills 添加、更新、移除
"""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SKILLS_DIR = PROJECT_ROOT / "skills"
SKILLS_CONFIG = DATA_DIR / "skills.json"


def ensure_dirs():
    """确保目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    
    if not SKILLS_CONFIG.exists():
        save_skills_config({"skills": {}})


def load_skills_config() -> Dict:
    """加载 Skills 配置"""
    if not SKILLS_CONFIG.exists():
        return {"skills": {}}
    with open(SKILLS_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def save_skills_config(config: Dict):
    """保存 Skills 配置"""
    with open(SKILLS_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def add_local_skill(
    skill_id: str,
    name: str,
    description: str,
    agents: List[str],
    skill_content: str
) -> Dict:
    """
    添加本地 Skill
    
    Args:
        skill_id: Skill ID
        name: Skill 名称
        description: Skill 描述
        agents: 可使用此 Skill 的 Agent 列表
        skill_content: Skill 内容
    """
    ensure_dirs()
    
    # 创建 Skill 文件
    skill_file = SKILLS_DIR / f"{skill_id}.md"
    with open(skill_file, "w", encoding="utf-8") as f:
        f.write(skill_content)
    
    # 更新配置
    config = load_skills_config()
    config["skills"][skill_id] = {
        "id": skill_id,
        "name": name,
        "description": description,
        "type": "local",
        "agents": agents,
        "path": str(skill_file.relative_to(PROJECT_ROOT)),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    save_skills_config(config)
    
    return config["skills"][skill_id]


def add_remote_skill(
    skill_id: str,
    name: str,
    description: str,
    agents: List[str],
    url: str
) -> Dict:
    """
    添加远程 Skill
    
    Args:
        skill_id: Skill ID
        name: Skill 名称
        description: Skill 描述
        agents: 可使用此 Skill 的 Agent 列表
        url: Skill 远程 URL
    """
    ensure_dirs()
    
    config = load_skills_config()
    config["skills"][skill_id] = {
        "id": skill_id,
        "name": name,
        "description": description,
        "type": "remote",
        "agents": agents,
        "url": url,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    save_skills_config(config)
    
    return config["skills"][skill_id]


def update_skill(skill_id: str, **kwargs) -> Optional[Dict]:
    """更新 Skill"""
    config = load_skills_config()
    
    if skill_id not in config["skills"]:
        return None
    
    skill = config["skills"][skill_id]
    skill.update(kwargs)
    skill["updated_at"] = datetime.now().isoformat()
    
    save_skills_config(config)
    return skill


def remove_skill(skill_id: str) -> bool:
    """移除 Skill"""
    config = load_skills_config()
    
    if skill_id not in config["skills"]:
        return False
    
    skill = config["skills"][skill_id]
    
    # 如果是本地 Skill，删除文件
    if skill.get("type") == "local" and "path" in skill:
        skill_file = PROJECT_ROOT / skill["path"]
        if skill_file.exists():
            skill_file.unlink()
    
    del config["skills"][skill_id]
    save_skills_config(config)
    
    return True


def get_skill(skill_id: str) -> Optional[Dict]:
    """获取 Skill"""
    config = load_skills_config()
    return config["skills"].get(skill_id)


def list_skills(agent: Optional[str] = None) -> List[Dict]:
    """列出 Skills"""
    config = load_skills_config()
    skills = list(config["skills"].values())
    
    if agent:
        skills = [s for s in skills if agent in s.get("agents", [])]
    
    return skills


def print_skill(skill: Dict):
    """打印 Skill 信息"""
    print(f"\n🔧 Skill: {skill['name']} ({skill['id']})")
    print(f"   类型: {skill.get('type', 'unknown')}")
    print(f"   描述: {skill.get('description', 'N/A')}")
    print(f"   可用 Agent: {', '.join(skill.get('agents', []))}")
    print(f"   创建时间: {skill.get('created_at', 'N/A')}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="CorpPilot Skill 管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # add-local 命令
    add_local_parser = subparsers.add_parser("add-local", help="添加本地 Skill")
    add_local_parser.add_argument("--id", required=True, help="Skill ID")
    add_local_parser.add_argument("--name", required=True, help="Skill 名称")
    add_local_parser.add_argument("--description", "-d", default="", help="描述")
    add_local_parser.add_argument("--agents", "-a", required=True, help="可用 Agent（逗号分隔）")
    add_local_parser.add_argument("--file", "-f", help="Skill 文件路径")
    
    # add-remote 命令
    add_remote_parser = subparsers.add_parser("add-remote", help="添加远程 Skill")
    add_remote_parser.add_argument("--id", required=True, help="Skill ID")
    add_remote_parser.add_argument("--name", required=True, help="Skill 名称")
    add_remote_parser.add_argument("--description", "-d", default="", help="描述")
    add_remote_parser.add_argument("--agents", "-a", required=True, help="可用 Agent（逗号分隔）")
    add_remote_parser.add_argument("--url", required=True, help="远程 URL")
    
    # remove 命令
    remove_parser = subparsers.add_parser("remove", help="移除 Skill")
    remove_parser.add_argument("--id", required=True, help="Skill ID")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出 Skills")
    list_parser.add_argument("--agent", help="按 Agent 筛选")
    
    # get 命令
    get_parser = subparsers.add_parser("get", help="获取 Skill 详情")
    get_parser.add_argument("--id", required=True, help="Skill ID")
    
    args = parser.parse_args()
    
    if args.command == "add-local":
        agents = [a.strip() for a in args.agions.split(",")]
        
        # 读取 Skill 文件或使用默认内容
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = f"# {args.name}\n\n{args.description}"
        
        skill = add_local_skill(
            skill_id=args.id,
            name=args.name,
            description=args.description,
            agents=agents,
            skill_content=content
        )
        print("✅ 本地 Skill 添加成功")
        print_skill(skill)
    
    elif args.command == "add-remote":
        agents = [a.strip() for a in args.agions.split(",")]
        skill = add_remote_skill(
            skill_id=args.id,
            name=args.name,
            description=args.description,
            agents=agents,
            url=args.url
        )
        print("✅ 远程 Skill 添加成功")
        print_skill(skill)
    
    elif args.command == "remove":
        if remove_skill(args.id):
            print(f"✅ Skill 已移除: {args.id}")
        else:
            print(f"❌ Skill 不存在: {args.id}")
    
    elif args.command == "list":
        skills = list_skills(agent=args.agent)
        if skills:
            print(f"\n🔧 Skills 列表 (共 {len(skills)} 个)")
            for skill in skills:
                print(f"   [{skill.get('type', 'unknown')}] {skill['id']}: {skill['name']}")
        else:
            print("暂无 Skills")
    
    elif args.command == "get":
        skill = get_skill(args.id)
        if skill:
            print_skill(skill)
        else:
            print(f"❌ Skill 不存在: {args.id}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
