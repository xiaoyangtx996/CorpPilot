"""
Tools — Agent 可用工具集
定义工具 Schema（供 LLM function calling 使用）及其执行逻辑。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .message_bus import MessageBus


# --------------------------------------------------------------------------- #
# 工具 Schema（OpenAI function calling 格式）
# --------------------------------------------------------------------------- #

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "向其他 Agent 发送消息或任务产出。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "目标 Agent ID"},
                    "content": {"type": "string", "description": "消息内容"},
                    "msg_type": {
                        "type": "string",
                        "enum": ["message", "task_assign", "task_output", "review_request", "resource_request"],
                        "description": "消息类型",
                    },
                },
                "required": ["to", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_inbox",
            "description": "读取自己的收件箱（不消耗，只查看）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作目录下的文件内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于项目根目录的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件内容到工作目录（会覆盖原有内容）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于项目根目录的文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "在系统中创建一个新的业务任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题"},
                    "type": {
                        "type": "string",
                        "enum": ["RD", "PD", "DA", "OP", "MK", "HR", "FN", "LG"],
                        "description": "任务类型",
                    },
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                    "description": {"type": "string", "description": "任务详细描述"},
                },
                "required": ["title", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_done",
            "description": "标记当前任务已完成，输出最终产出物摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "任务产出摘要"},
                    "artifacts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "产出文件路径列表",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# 工具执行器
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ToolExecutor:
    """
    执行 LLM 返回的工具调用，返回结果字符串。

    用法：
        executor = ToolExecutor(agent_id="rd_director", bus=message_bus)
        result = executor.execute("send_message", {"to": "product_director", "content": "PRD已就绪"})
    """

    def __init__(
        self,
        agent_id: str,
        bus: "MessageBus",
        task_service=None,  # 可选：接入 TaskService
    ):
        self.agent_id = agent_id
        self.bus = bus
        self.task_service = task_service
        self._done = False
        self._done_summary: Optional[str] = None

    @property
    def is_done(self) -> bool:
        return self._done

    @property 
    def done_summary(self) -> Optional[str]:
        return self._done_summary

    def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """分发并执行工具调用，返回结果字符串。"""
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                return f"[Error] 未知工具: {tool_name}"
            return handler(tool_input)
        except Exception as exc:
            return f"[Error] 工具 {tool_name} 执行失败: {exc}"

    # ---------------------------------------------------------------------- #
    # 工具实现
    # ---------------------------------------------------------------------- #

    def _tool_send_message(self, inp: Dict[str, Any]) -> str:
        to = inp.get("to", "")
        content = inp.get("content", "")
        msg_type = inp.get("msg_type", "message")
        msg = self.bus.send(self.agent_id, to, content, msg_type)
        return f"消息已发送给 {to}，ID: {msg['id']}"

    def _tool_read_inbox(self, _inp: Dict[str, Any]) -> str:
        msgs = self.bus.peek_inbox(self.agent_id)
        if not msgs:
            return "收件箱为空。"
        return json.dumps(msgs, ensure_ascii=False, indent=2)

    def _tool_read_file(self, inp: Dict[str, Any]) -> str:
        path = PROJECT_ROOT / inp.get("path", "")
        if not path.exists():
            return f"[Error] 文件不存在: {path}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            return f"[Error] 读取失败: {exc}"

    def _tool_write_file(self, inp: Dict[str, Any]) -> str:
        path = PROJECT_ROOT / inp.get("path", "")
        content = inp.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"文件已写入: {path.relative_to(PROJECT_ROOT)}"
        except Exception as exc:
            return f"[Error] 写入失败: {exc}"

    def _tool_create_task(self, inp: Dict[str, Any]) -> str:
        if self.task_service:
            try:
                task = self.task_service.create_task(
                    title=inp.get("title", ""),
                    task_type=inp.get("type", "RD"),
                    priority=inp.get("priority", "P2"),
                    requester=self.agent_id,
                    description=inp.get("description", ""),
                )
                return f"任务已创建: {task['task_id']}"
            except Exception as exc:
                return f"[Error] 任务创建失败: {exc}"
        return "[Warning] TaskService 未挂载，跳过任务创建。"

    def _tool_report_done(self, inp: Dict[str, Any]) -> str:
        self._done = True
        self._done_summary = inp.get("summary", "")
        artifacts = inp.get("artifacts", [])
        result = f"任务完成。产出摘要：{self._done_summary}"
        if artifacts:
            result += f"\n产出文件：{', '.join(artifacts)}"
        return result
