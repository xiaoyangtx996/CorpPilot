"""
Tools — Agent 可用工具集
定义工具 Schema（供 LLM function calling 使用）及其执行逻辑。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

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
            "name": "git_commit",
            "description": "在 git 仓库中暂存指定文件并提交（需已 git init）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "提交说明"},
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要提交的文件路径，空则提交全部变更",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "在项目根目录执行 shell 命令（超时 120 秒），用于测试、构建、git 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "cwd": {"type": "string", "description": "可选，相对于项目根的工作目录"},
                },
                "required": ["command"],
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
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def normalize_artifact_path(rel_path: str, task_id: Optional[str] = None) -> Path:
    """将 Agent 写入路径规范到 artifacts/{task_id}/ 下（若提供 task_id）。"""
    rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if task_id and not rel.startswith("artifacts/"):
        rel = f"artifacts/{task_id}/{rel}"
    full = (PROJECT_ROOT / rel).resolve()
    root = PROJECT_ROOT.resolve()
    if not str(full).startswith(str(root)):
        raise ValueError(f"路径越界: {rel_path}")
    return full


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
        on_report_done: Optional[Callable[[str, str, List[str]], None]] = None,
        task_id: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.bus = bus
        self.task_service = task_service
        self.on_report_done = on_report_done
        self.task_id = task_id
        self._done = False
        self._done_summary: Optional[str] = None
        self._done_artifacts: List[str] = []

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def done_summary(self) -> Optional[str]:
        return self._done_summary

    @property
    def done_artifacts(self) -> List[str]:
        return list(self._done_artifacts)

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
        try:
            path = normalize_artifact_path(inp.get("path", ""), self.task_id)
        except ValueError as exc:
            return f"[Error] {exc}"
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

    def _tool_git_commit(self, inp: Dict[str, Any]) -> str:
        message = inp.get("message", "").strip()
        if not message:
            return "[Error] message 不能为空"
        paths = inp.get("paths") or []
        try:
            if paths:
                add = subprocess.run(
                    ["git", "add", *paths],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if add.returncode != 0:
                    return f"[Error] git add 失败: {add.stderr}"
            else:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    timeout=30,
                )
            commit = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            out = (commit.stdout or "") + (commit.stderr or "")
            return f"exit_code={commit.returncode}\n{out}"
        except Exception as exc:
            return f"[Error] git commit 失败: {exc}"

    def _tool_run_shell(self, inp: Dict[str, Any]) -> str:
        command = inp.get("command", "").strip()
        if not command:
            return "[Error] command 不能为空"
        cwd_rel = inp.get("cwd", "")
        cwd = PROJECT_ROOT / cwd_rel if cwd_rel else PROJECT_ROOT
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if len(out) > 8000:
                out = out[:8000] + "\n…(输出已截断)"
            return f"exit_code={proc.returncode}\n{out}"
        except subprocess.TimeoutExpired:
            return "[Error] 命令执行超时（120s）"
        except Exception as exc:
            return f"[Error] 执行失败: {exc}"

    def _tool_report_done(self, inp: Dict[str, Any]) -> str:
        self._done = True
        self._done_summary = inp.get("summary", "")
        artifacts = inp.get("artifacts", [])
        self._done_artifacts = list(artifacts) if isinstance(artifacts, list) else []
        result = f"任务完成。产出摘要：{self._done_summary}"
        if self._done_artifacts:
            result += f"\n产出文件：{', '.join(self._done_artifacts)}"
        if self.on_report_done:
            try:
                self.on_report_done(self.agent_id, self._done_summary, self._done_artifacts)
            except Exception as exc:
                result += f"\n[Warning] 治理层回调失败: {exc}"
        return result
