#!/usr/bin/env python3
"""
run_team.py — CorpPilot Agent 运行时 CLI 入口
交互式控制台，支持：发布目标 / 查看 Agent 状态 / 流量统计 / 注入消息
"""
from __future__ import annotations

import json
import sys
import time
import threading
from pathlib import Path

# 加载 scripts 到 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from runtime.llm_client import LLMClient
from runtime.model_router import ModelRouter
from runtime.traffic_monitor import TrafficMonitor
from runtime.message_bus import MessageBus
from runtime.agent_manager import AgentManager

# CorpPilot 部门负责人映射
DEPT_HEADS = {
    "ceo": "CEO / 战略方向接收",
    "president_office": "总裁办",
    "strategy": "战略总监",
    "pmo": "PMO 主任",
    "rd_center": "研发总监",
    "product_center": "产品总监",
    "data_center": "数据总监",
    "operation_center": "运营总监",
    "marketing_center": "市场总监",
    "finance": "财务总监",
    "hr": "人力总监",
    "legal": "法务总监",
    "risk_center": "风控总监",
}

_log_buffer: list = []
_log_lock = threading.Lock()


def _on_output(agent_id: str, text: str) -> None:
    """全局日志回调，打印并写入缓冲。"""
    ts = time.strftime("%H:%M:%S")
    line = f"\033[90m[{ts}]\033[0m \033[36m[{agent_id}]\033[0m {text}"
    print(line)
    with _log_lock:
        _log_buffer.append({"ts": time.time(), "agent_id": agent_id, "text": text})


def _print_banner() -> None:
    print("\n" + "=" * 52)
    print("  CorpPilot  Agentic Runtime  v1.0")
    print("  企业级多 Agent 协同运行时控制台")
    print("=" * 52 + "\n")


def _init_runtime():
    router = ModelRouter()
    monitor = TrafficMonitor(router=router)
    client = LLMClient(max_retries=3, retry_delay=2.0)
    bus = MessageBus()

    # 尝试接入 TaskService
    task_service = None
    try:
        from core import TaskService
        task_service = TaskService()
    except Exception:
        pass

    manager = AgentManager(bus, router, monitor, client, task_service)
    manager.on_log(_on_output)
    return router, monitor, client, bus, manager


def _menu(router, monitor, bus, manager) -> None:
    MODEL_CFG = router.resolve("ceo")

    while True:
        print("\n" + "-" * 40)
        print("  [1] 发布业务目标（向 CEO Agent 发送）")
        print("  [2] 查看运行中的 Agent")
        print("  [3] 查看消息总线状态")
        print("  [4] 查看实时流量统计")
        print("  [5] 向指定 Agent 注入消息")
        print("  [6] 当前模型配置")
        print("  [Q] 退出")
        print("-" * 40)

        choice = input("请选择 > ").strip().upper()

        if choice == "1":
            _cmd_send_goal(bus, manager)
        elif choice == "2":
            _cmd_list_agents(manager)
        elif choice == "3":
            _cmd_bus_status(bus)
        elif choice == "4":
            _cmd_traffic(monitor)
        elif choice == "5":
            _cmd_inject_msg(bus)
        elif choice == "6":
            _cmd_show_model(router)
        elif choice in ("Q", "QUIT", "EXIT"):
            print("\n已退出 CorpPilot Runtime。\n")
            break
        else:
            print("[提示] 无效选择，请重新输入。")


def _cmd_send_goal(bus: MessageBus, manager: AgentManager) -> None:
    print("\n== 发布新业务目标 ==")
    goal = input("请描述业务目标（按回车确认）:\n> ").strip()
    if not goal:
        print("[取消] 目标为空，已取消。")
        return

    target_agent = input("发送给哪个 Agent？（默认 ceo）[按回车跳过]: ").strip() or "ceo"

    print(f"\n[系统] 正在启动 {target_agent}…")
    manager.spawn(
        agent_id=target_agent,
        initial_task=goal,
        on_output=_on_output,
    )
    print(f"[系统] {target_agent} 已在后台运行，可按 [2] 查看状态。\n")


def _cmd_list_agents(manager: AgentManager) -> None:
    print("\n== 当前 Agent 状态 ==")
    agents = manager.list_agents()
    if not agents:
        print("  （暂无运行中的 Agent）")
        return
    for a in agents:
        running = manager.is_running(a["agent_id"])
        status_str = "\033[32m运行中\033[0m" if running else f"\033[90m{a['status']}\033[0m"
        task_preview = a.get("task", "")[:60]
        print(f"  [{a['agent_id']}] {status_str} | {task_preview}")


def _cmd_bus_status(bus: MessageBus) -> None:
    print("\n== 消息总线状态 ==")
    active = bus.list_active_agents()
    if not active:
        print("  （所有收件箱为空）")
        return
    for name in active:
        count = bus.inbox_count(name)
        if count > 0:
            print(f"  {name}: {count} 条待处理消息")


def _cmd_traffic(monitor: TrafficMonitor) -> None:
    print("\n== 流量统计（最近 1 小时）==")
    stats = monitor.get_stats("1h")
    print(f"  总调用次数: {stats['total_calls']}")
    print(f"  总 Token 消耗: {stats['total_tokens']:,}")
    print(f"  估算成本: ${stats['total_cost_usd']:.4f} USD")
    print(f"  平均延迟: {stats['avg_latency_ms']} ms")
    print(f"  当前 RPM: {stats['current_rpm']}")
    if stats["by_model"]:
        print("\n  -- 按模型分布 --")
        for mdl, info in stats["by_model"].items():
            print(f"    {mdl}: {info['calls']} 次, {info['tokens']:,} tokens, ${info['cost_usd']:.4f}")
    if stats["by_agent"]:
        print("\n  -- 按 Agent 分布 --")
        for aid, info in stats["by_agent"].items():
            print(f"    {aid}: {info['calls']} 次, {info['tokens']:,} tokens")


def _cmd_inject_msg(bus: MessageBus) -> None:
    print("\n== 向 Agent 注入消息 ==")
    agent_id = input("目标 Agent ID: ").strip()
    if not agent_id:
        print("[取消]")
        return
    content = input("消息内容:\n> ").strip()
    if not content:
        print("[取消]")
        return
    msg_type = input("消息类型 [message/task_assign/task_output] (默认 message): ").strip() or "message"
    bus.send("cli_operator", agent_id, content, msg_type)
    print(f"[发送成功] 消息已投递至 {agent_id} 的收件箱。")


def _cmd_show_model(router: ModelRouter) -> None:
    print("\n== 当前模型配置 ==")
    cfg = router.to_dict()
    gp = cfg.get("global", {}).get("primary", {})
    gf = cfg.get("global", {}).get("fallback", {})
    print(f"  全局主模型: {gp.get('provider', '-')}/{gp.get('model', '-')}")
    print(f"  全局备用: {gf.get('provider', '-')}/{gf.get('model', '-')}")
    overrides = cfg.get("agent_overrides", {})
    if overrides:
        print("  Agent 专属覆盖:")
        for aid, ov in overrides.items():
            pm = ov.get("primary", {})
            print(f"    {aid}: {pm.get('provider', '-')}/{pm.get('model', '-')}")


def main() -> None:
    _print_banner()
    try:
        router, monitor, client, bus, manager = _init_runtime()
    except Exception as exc:
        print(f"\n[错误] 初始化运行时失败: {exc}")
        print("请检查 data/llm_config.json 配置文件并设置对应的 API Key 环境变量。\n")
        sys.exit(1)

    primary = router.resolve("ceo")
    print(f"  当前全局主模型: \033[33m{primary.provider}/{primary.model}\033[0m")
    print("  输入 [1] 开始发送业务目标来启动 Agent 协同流程。\n")

    try:
        _menu(router, monitor, bus, manager)
    except KeyboardInterrupt:
        print("\n\n[中断] 等待后台 Agent 完成…")
        manager.wait_all(timeout=10.0)
        print("已退出。\n")


if __name__ == "__main__":
    main()
