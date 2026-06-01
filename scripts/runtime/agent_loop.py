"""
Agent Loop — 单个 Agent 的 LLM 执行循环
参照 learn-claude-code s09 的 _teammate_loop，整合：
  - 收件箱读取 + 注入上下文
  - 动态模型路由
  - LLM 调用
  - 工具执行
  - 流量监控
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm_client import LLMClient, ModelConfig
from .model_router import ModelRouter
from .traffic_monitor import TrafficMonitor
from .message_bus import MessageBus
from .tools import TOOL_SCHEMAS, ToolExecutor

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"


def _load_soul(agent_id: str) -> str:
    """加载 Agent 的 SOUL.md 作为系统提示。"""
    soul_file = _AGENTS_DIR / agent_id / "SOUL.md"
    if soul_file.exists():
        return soul_file.read_text(encoding="utf-8")
    return f"你是 CorpPilot 系统中的 {agent_id} 智能体，请根据收到的指令认真完成你的职责。"


def _load_skills(agent_id: str, skill_ids: Optional[List[str]] = None) -> str:
    """加载绑定到该 Agent 的 Skill 正文，注入 system prompt。"""
    try:
        from core import PROJECT_ROOT as _root, SkillCatalogService

        catalog = SkillCatalogService()
    except Exception:
        return ""

    skills: List[Dict[str, Any]] = []
    if skill_ids:
        seen = set()
        for sid in skill_ids:
            if sid in seen:
                continue
            seen.add(sid)
            skill = catalog.get_skill(sid)
            if skill:
                skills.append(skill)
    if not skills:
        skills = catalog.list_skills(agent_id)

    if not skills:
        return ""

    blocks: List[str] = ["\n\n---\n## 已加载 Skills\n"]
    for skill in skills:
        blocks.append(f"\n### Skill: {skill.get('name', skill.get('id'))}\n")
        if skill.get("type") == "local" and skill.get("path"):
            path = _root / str(skill["path"])
            if path.exists():
                blocks.append(path.read_text(encoding="utf-8"))
            else:
                blocks.append(f"（文件缺失: {skill['path']}）")
        else:
            blocks.append(skill.get("description", ""))
    return "\n".join(blocks)


def agent_loop(
    agent_id: str,
    initial_task: str,
    bus: MessageBus,
    router: ModelRouter,
    monitor: TrafficMonitor,
    client: LLMClient,
    on_output: Optional[Callable[[str, str], None]] = None,
    max_turns: int = 50,
    task_service=None,
    task_id: Optional[str] = None,
    skill_ids: Optional[List[str]] = None,
    on_report_done: Optional[Callable[[str, str, List[str]], None]] = None,
) -> str:
    """
    单个 Agent 的完整执行循环。

    参数：
        agent_id: Agent 标识符
        initial_task: 初始任务描述
        bus: 消息总线
        router: 模型路由器
        monitor: 流量监控器
        client: LLM 客户端
        on_output: 每轮输出回调 on_output(agent_id, text)
        max_turns: 最大循环轮次
        task_service: 可选的 TaskService 实例

    返回：
        最终输出的文本摘要
    """
    system_prompt = _load_soul(agent_id) + _load_skills(agent_id, skill_ids)
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": initial_task}
    ]
    executor = ToolExecutor(agent_id, bus, task_service, on_report_done=on_report_done, task_id=task_id)
    final_output = ""

    def _emit(text: str) -> None:
        if on_output:
            on_output(agent_id, text)

    _emit(f"[{agent_id}] 开始执行任务：{initial_task[:80]}…")

    for turn in range(max_turns):
        # 1. 读收件箱，有新消息则注入上下文
        inbox_msgs = bus.read_inbox(agent_id)
        if inbox_msgs:
            inbox_text = json.dumps(inbox_msgs, ensure_ascii=False, indent=2)
            messages.append({
                "role": "user",
                "content": f"<inbox>\n{inbox_text}\n</inbox>\n请处理以上新消息后继续。"
            })
            messages.append({
                "role": "assistant",
                "content": "收到消息，已理解，继续执行。"
            })
            _emit(f"[{agent_id}] 读取到 {len(inbox_msgs)} 条新消息")

        # 2. 解析本轮应使用的模型
        dept_id = role_id = ""
        flow_step_id = None
        try:
            from core import AgentCatalogService

            catalog = AgentCatalogService()
            info = catalog.get_agent(agent_id) or {}
            dept_id = info.get("department", "") or agent_id
            role_id = info.get("role", "") or agent_id
        except ImportError:
            dept_id = agent_id

        if task_id and task_service:
            try:
                task = task_service.get_task(task_id)
                if task:
                    flow_step_id = task.get("flow_step_id")
                    dept_id = dept_id or task.get("execution_owner") or agent_id
            except Exception:
                pass

        route = router.resolve(agent_id=agent_id, department_id=dept_id, role_id=role_id, capability="chat")

        # 3. 调用 LLM（RPM 限流 + 内部重试）
        if not monitor.check_rate_limit(agent_id, router.get_rate_limit_rpm()):
            _emit(f"[{agent_id}] RPM 限流触发，等待 5s…")
            time.sleep(5)
        t0 = time.time()
        try:
            response = client.call(
                messages=messages,
                system=system_prompt,
                tools=TOOL_SCHEMAS,
                model_cfg=route,
            )
        except Exception as exc:
            _emit(f"[{agent_id}] LLM 达到最大重试且备用跌落失败: {exc}")
            raise
        latency_ms = (time.time() - t0) * 1000

        # 4. 上报流量
        monitor.record(
            agent_id=agent_id,
            model=getattr(response, "model_name", route.primary.model),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=latency_ms,
            extra={
                "task_id": task_id,
                "department_id": dept_id,
                "flow_step_id": flow_step_id,
            },
        )

        # 5. 追加助手回复
        assistant_content: Any = response.content or ""
        if response.has_tool_calls:
            assistant_content = [{"type": "text", "text": response.content}] if response.content else []
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
        messages.append({"role": "assistant", "content": assistant_content})

        if response.content:
            _emit(f"[{agent_id}] {response.content[:200]}…" if len(response.content) > 200 else f"[{agent_id}] {response.content}")
            final_output = response.content

        # 6. 没有工具调用时结束循环
        if not response.has_tool_calls:
            _emit(f"[{agent_id}] 本轮循环结束（stop_reason={response.stop_reason}）")
            break

        # 7. 执行工具调用，追加结果
        tool_results = []
        for tc in response.tool_calls:
            _emit(f"[{agent_id}] 调用工具: {tc['name']}({json.dumps(tc['input'], ensure_ascii=False)[:100]})")
            result_text = executor.execute(tc["name"], tc["input"])
            _emit(f"[{agent_id}] 工具结果: {result_text[:200]}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result_text,
            })
        messages.append({"role": "user", "content": tool_results})

        # 检查 report_done 工具是否触发
        if executor.is_done:
            _emit(f"[{agent_id}] 任务完成：{executor.done_summary}")
            return executor.done_summary or final_output

    if executor.is_done:
        return executor.done_summary or final_output

    _emit(f"[{agent_id}] 执行循环结束（共 {min(turn+1, max_turns)} 轮）")
    return final_output
