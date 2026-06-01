"""
Agent Manager — Agent 生命周期管理
负责：Agent 启动（线程）/ 状态追踪 / 关闭 / 名册同步
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .agent_loop import agent_loop
from .llm_client import LLMClient
from .message_bus import MessageBus
from .model_router import ModelRouter
from .traffic_monitor import TrafficMonitor

_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / ".team" / "agents.json"

# Agent 状态常量
STATUS_IDLE = "idle"
STATUS_WORKING = "working"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class AgentManager:
    """
    用法示例：
        manager = AgentManager(bus, router, monitor, client)
        manager.spawn(
            agent_id="rd_director",
            initial_task="请基于以下 PRD 开始技术架构设计...",
            on_output=print,
        )
        manager.wait_all()
    """

    def __init__(
        self,
        bus: MessageBus,
        router: ModelRouter,
        monitor: TrafficMonitor,
        client: LLMClient,
        task_service=None,
    ):
        self.bus = bus
        self.router = router
        self.monitor = monitor
        self.client = client
        self.task_service = task_service

        self._agents: Dict[str, Dict[str, Any]] = {}  # agent_id -> 状态字典
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._log_callbacks: List[Callable[[str, str], None]] = []

        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------- #
    # 日志回调注册
    # ---------------------------------------------------------------------- #

    def on_log(self, callback: Callable[[str, str], None]) -> None:
        """注册日志回调：callback(agent_id, text)。"""
        self._log_callbacks.append(callback)

    def _emit(self, agent_id: str, text: str) -> None:
        for cb in self._log_callbacks:
            try:
                cb(agent_id, text)
            except Exception:
                pass

    # ---------------------------------------------------------------------- #
    # Agent 启动与管理
    # ---------------------------------------------------------------------- #

    def spawn(
        self,
        agent_id: str,
        initial_task: str,
        on_output: Optional[Callable[[str, str], None]] = None,
        max_turns: int = 50,
        daemon: bool = True,
        task_id: Optional[str] = None,
        skill_ids: Optional[List[str]] = None,
        on_report_done: Optional[Callable[[str, Optional[str], List[str]], None]] = None,
    ) -> None:
        """在独立线程中启动一个 Agent。"""
        with self._lock:
            if agent_id in self._threads and self._threads[agent_id].is_alive():
                print(f"[AgentManager] {agent_id} 已在运行中，跳过重复启动。")
                return

            self._agents[agent_id] = {
                "agent_id": agent_id,
                "status": STATUS_WORKING,
                "started_at": time.time(),
                "task": initial_task[:200],
                "task_id": task_id,
                "result": None,
                "error": None,
            }
            self._save_state()

        def _run() -> None:
            reported_summary: Optional[str] = None
            reported_artifacts: List[str] = []

            def _bridge_done(aid: str, summary: str, arts: List[str]) -> None:
                nonlocal reported_summary
                reported_summary = summary
                reported_artifacts.extend(arts)
                if on_report_done:
                    on_report_done(aid, summary, arts)

            try:
                result = agent_loop(
                    agent_id=agent_id,
                    initial_task=initial_task,
                    bus=self.bus,
                    router=self.router,
                    monitor=self.monitor,
                    client=self.client,
                    on_output=lambda aid, txt: [self._emit(aid, txt), on_output(aid, txt) if on_output else None],
                    max_turns=max_turns,
                    task_service=self.task_service,
                    task_id=task_id,
                    skill_ids=skill_ids,
                    on_report_done=_bridge_done if on_report_done else None,
                )
                with self._lock:
                    self._agents[agent_id]["status"] = STATUS_DONE
                    self._agents[agent_id]["result"] = result
                    self._save_state()
                if on_report_done and not reported_summary and result:
                    on_report_done(agent_id, result, reported_artifacts)
            except Exception as exc:
                with self._lock:
                    self._agents[agent_id]["status"] = STATUS_FAILED
                    self._agents[agent_id]["error"] = str(exc)
                    self._save_state()
                self._emit(agent_id, f"[AgentManager] {agent_id} 执行失败: {exc}")

        t = threading.Thread(target=_run, daemon=daemon, name=f"agent-{agent_id}")
        self._threads[agent_id] = t
        t.start()
        self._emit(agent_id, f"[AgentManager] {agent_id} 已启动")

    def shutdown(self, agent_id: str, timeout: float = 5.0) -> None:
        """等待指定 Agent 的线程结束（不强制终止）。"""
        t = self._threads.get(agent_id)
        if t and t.is_alive():
            t.join(timeout=timeout)
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["status"] = STATUS_DONE

    def wait_all(self, timeout: Optional[float] = None) -> None:
        """等待所有 Agent 完成。"""
        for t in list(self._threads.values()):
            if t.is_alive():
                t.join(timeout=timeout)

    # ---------------------------------------------------------------------- #
    # 状态查询
    # ---------------------------------------------------------------------- #

    def list_agents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._agents.get(agent_id)

    def is_running(self, agent_id: str) -> bool:
        t = self._threads.get(agent_id)
        return bool(t and t.is_alive())

    def running_count(self) -> int:
        return sum(1 for t in self._threads.values() if t.is_alive())

    # ---------------------------------------------------------------------- #
    # 私有：持久化
    # ---------------------------------------------------------------------- #

    def _save_state(self) -> None:
        """将 Agent 状态快照写入 JSON 文件（不加锁，调用方需持锁）。"""
        try:
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {"updated_at": time.time(), "agents": self._agents},
                    f, ensure_ascii=False, indent=2
                )
        except Exception:
            pass
