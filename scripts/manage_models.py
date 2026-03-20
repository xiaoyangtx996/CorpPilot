#!/usr/bin/env python3
"""
manage_models.py — 模型配置管理 CLI (只读版)
使用方法：
  python scripts/manage_models.py list
  python scripts/manage_models.py show <agent_id>
提示：更复杂的修改请在 Dashboard 界面完成。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime.model_router import ModelRouter

def cmd_list(router: ModelRouter, _args) -> None:
    cfg = router.to_dict()
    print("\n== CorpPilot 模型池概览 ==\n")
    for m in cfg.get("models", []):
        print(f"[{m.get('id')}] {m.get('provider')}/{m.get('model')} ({m.get('type')})")
    
    print("\n== 全局路由 ==")
    for cap, route in cfg.get("global_routes", {}).items():
        print(f"  {cap.upper()}: 主={route.get('primary')} / 备={route.get('fallback')} (重试 {route.get('max_retries')} 次)")

    print("\n== 层级覆盖 ==")
    for k, label in [("department_routes", "部门"), ("role_routes", "岗位"), ("agent_routes", "Agent")]:
        routes = cfg.get(k, {})
        if routes:
            print(f"  [{label}级]")
            for tid, val in routes.items():
                print(f"    {tid}: chat={val.get('chat','-')} image={val.get('image','-')}")
    print()

def cmd_show(router: ModelRouter, args) -> None:
    # 模拟获取基础信息以展示解析链路
    dept_id = role_id = ""
    try:
        from core import AgentCatalogService
        catalog = AgentCatalogService()
        info = catalog.get_agent(args.agent_id) or {}
        dept_id = info.get("department", "")
        role_id = info.get("role", "")
    except Exception:
        pass

    route = router.resolve(agent_id=args.agent_id, department_id=dept_id, role_id=role_id, capability="chat")
    print(f"\n== {args.agent_id} (部门:{dept_id}, 岗位:{role_id}) Chat 最终解析路由 ==")
    print(f"主模型: {route.primary.model} ({route.primary.provider})")
    if route.fallback:
        print(f"备用模型: {route.fallback.model} ({route.fallback.provider})")
    print(f"尝试策略: 总共可尝试 {len(route.get_attempts())} 次")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CorpPilot 模型配置展示工具（由于新版架构复杂度高，仅提供只读查询。修改请通过 Web 控制台进行。）"
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出所有模型配置与路由表")
    
    p = sub.add_parser("show", help="查看某个 Agent 最终使用的模型配置")
    p.add_argument("agent_id")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    router = ModelRouter()
    dispatch = {"list": cmd_list, "show": cmd_show}
    if args.cmd in dispatch:
        dispatch[args.cmd](router, args)

if __name__ == "__main__":
    main()
