"""
Message Bus — JSONL 收件箱消息总线
每个 Agent 独立一个 .jsonl 文件作为收件箱，参照 learn-claude-code s09。
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_INBOX_DIR = Path(__file__).resolve().parent.parent.parent / "data" / ".team" / "inbox"


# 消息类型常量
MSG_TYPES = {
    "message": "普通消息",
    "broadcast": "广播",
    "task_assign": "任务指派",
    "task_output": "任务产出",
    "resource_request": "资源申请",
    "review_request": "评审请求",
    "review_result": "评审结果",
    "system": "系统通知",
}


class MessageBus:
    """
    用法示例：
        bus = MessageBus()
        bus.send("pmo_director", "rd_director", "请开始开发：用户系统", msg_type="task_assign")
        msgs = bus.read_inbox("rd_director")
    """

    def __init__(self, inbox_dir: Optional[Path | str] = None):
        self.dir = Path(inbox_dir) if inbox_dir else _DEFAULT_INBOX_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------- #
    # 核心接口
    # ---------------------------------------------------------------------- #

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """向目标 Agent 的收件箱追加一条消息。"""
        msg: Dict[str, Any] = {
            "id": f"{sender}-{int(time.time()*1000)}",
            "type": msg_type,
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)

        inbox_file = self.dir / f"{to}.jsonl"
        with self._lock:
            with open(inbox_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return msg

    def read_inbox(self, name: str, drain: bool = True) -> List[Dict[str, Any]]:
        """
        读取 Agent 的收件箱。
        drain=True 时读取后清空（标准做法，防止重复处理）。
        """
        inbox_file = self.dir / f"{name}.jsonl"
        if not inbox_file.exists():
            return []

        with self._lock:
            text = inbox_file.read_text(encoding="utf-8").strip()
            if drain:
                inbox_file.write_text("", encoding="utf-8")

        msgs = []
        for line in text.splitlines():
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return msgs

    def broadcast(
        self,
        sender: str,
        content: str,
        recipients: List[str],
        msg_type: str = "broadcast",
    ) -> List[Dict[str, Any]]:
        """广播消息给多个 Agent。"""
        sent = []
        for name in recipients:
            if name != sender:
                sent.append(self.send(sender, name, content, msg_type))
        return sent

    def peek_inbox(self, name: str) -> List[Dict[str, Any]]:
        """查看收件箱但不清空。"""
        return self.read_inbox(name, drain=False)

    def inbox_count(self, name: str) -> int:
        """查看收件箱消息数量。"""
        return len(self.peek_inbox(name))

    def list_active_agents(self) -> List[str]:
        """列出所有有收件箱文件的 Agent。"""
        return [f.stem for f in self.dir.glob("*.jsonl")]

    def clear_inbox(self, name: str) -> None:
        """强制清空某个 Agent 的收件箱。"""
        inbox_file = self.dir / f"{name}.jsonl"
        with self._lock:
            if inbox_file.exists():
                inbox_file.write_text("", encoding="utf-8")

    def get_log_path(self, name: str) -> Path:
        return self.dir / f"{name}.jsonl"
