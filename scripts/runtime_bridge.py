#!/usr/bin/env python3
"""
治理层与 Runtime 执行层的桥接：状态变更自动调度 Agent，report_done 回写产物并推进流程。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import ExecutionService, TaskStatus, WorkflowEngine, utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def _runtime_enabled() -> bool:
    return os.environ.get("CORPPILOT_AUTO_RUNTIME", "1").strip().lower() not in {"0", "false", "no"}


class RuntimeOrchestrator:
    """任务状态机 ↔ AgentManager 的生命周期编排。"""

    def __init__(self, workflow: WorkflowEngine, enabled: Optional[bool] = None):
        self.workflow = workflow
        self.execution = ExecutionService(workflow)
        self.enabled = _runtime_enabled() if enabled is None else enabled
        self._lock = threading.Lock()
        self._manager = None
        self._spawned: Dict[str, str] = {}  # task_id -> agent_id (in-flight)

    def _get_manager(self):
        if self._manager is not None:
            return self._manager
        from runtime.agent_manager import AgentManager
        from runtime.llm_client import LLMClient
        from runtime.message_bus import MessageBus
        from runtime.model_router import ModelRouter
        from runtime.traffic_monitor import TrafficMonitor

        router = ModelRouter()
        monitor = TrafficMonitor(router=router)
        client = LLMClient()
        bus = MessageBus()
        self._manager = AgentManager(
            bus, router, monitor, client, self.workflow.task_service
        )
        return self._manager

    def on_status_enter(
        self,
        task: Dict[str, Any],
        old_status: TaskStatus,
        new_status: TaskStatus,
    ) -> None:
        if not self.enabled:
            return
        task_id = task["task_id"]

        if new_status == TaskStatus.DISPATCHED and task.get("auto_execute", True):
            try:
                self.workflow.transition(task_id, TaskStatus.EXECUTING, "runtime:auto")
            except ValueError:
                pass
            return

        if new_status == TaskStatus.EXECUTING:
            try:
                from flow_engine import FlowEngine

                fe = FlowEngine(self.workflow.task_service)
                if task.get("flow_id") and task.get("flow_id") != "legacy":
                    ctx = fe.get_flow_context(task)
                    step = ctx.get("current_step")
                    if fe.is_supervisor_step(step):
                        threading.Thread(
                            target=self._run_supervisor,
                            args=(task_id,),
                            daemon=True,
                            name=f"supervisor-{task_id}",
                        ).start()
                        return
                    if fe.is_close_step(step):
                        threading.Thread(
                            target=self._run_close,
                            args=(task_id,),
                            daemon=True,
                            name=f"close-{task_id}",
                        ).start()
                        return
                    if fe.is_parallel_step(step):
                        self._spawn_parallel(task, step, fe)
                        return
            except Exception:
                pass
            self._spawn_for_task(task)

    def _run_supervisor(self, task_id: str) -> None:
        try:
            from flow_engine import FlowEngine

            fe = FlowEngine(self.workflow.task_service)
            fe.run_supervisor_step(task_id, self.workflow, actor="supervisor:auto")
        except Exception as exc:
            self.workflow.task_service.patch_runtime(
                task_id, {"supervisor_error": str(exc)}
            )

    def _run_close(self, task_id: str) -> None:
        try:
            from flow_engine import FlowEngine

            fe = FlowEngine(self.workflow.task_service)
            fe.run_close_step(task_id, self.workflow, actor="project_close:auto")
        except Exception as exc:
            self.workflow.task_service.patch_runtime(
                task_id, {"close_error": str(exc)}
            )

    def _spawn_for_task(self, task: Dict[str, Any]) -> None:
        task_id = task["task_id"]
        agent_id = str(task.get("execution_owner") or task.get("current_owner") or "ceo")
        skill_ids: Optional[List[str]] = None
        step = None
        try:
            from flow_engine import FlowEngine

            fe = FlowEngine(self.workflow.task_service)
            if task.get("flow_id") and task.get("flow_id") != "legacy":
                skill_ids = fe.step_skills(task) or None
                step = fe.get_flow_context(task).get("current_step")
        except Exception:
            skill_ids = None

        backend_name = "agent_loop"
        try:
            from runtime.execution_backends import get_backend, resolve_backend_name

            backend_name = resolve_backend_name(step)
        except Exception:
            pass

        spawn_key = f"{task_id}:{agent_id}"
        with self._lock:
            if self._spawned.get(task_id) == spawn_key or self._spawned.get(task_id) == agent_id:
                return
            self._spawned[task_id] = spawn_key

        initial_task = self._build_agent_prompt(task)
        self.workflow.task_service.patch_runtime(
            task_id,
            {"agent_id": agent_id, "spawned_at": utc_now_iso(), "executor": backend_name},
        )

        def _on_done(aid: str, summary: Optional[str], artifacts: List[str]) -> None:
            self.on_agent_report_done(task_id, aid, summary or "", artifacts)

        def _bg() -> None:
            try:
                if backend_name == "claude_code":
                    from runtime.execution_backends import ClaudeCodeBackend

                    ClaudeCodeBackend().run(
                        agent_id, initial_task, task_id=task_id, on_report_done=_on_done
                    )
                else:
                    manager = self._get_manager()
                    manager.spawn(
                        agent_id=agent_id,
                        initial_task=initial_task,
                        task_id=task_id,
                        skill_ids=skill_ids,
                        on_report_done=_on_done,
                    )
            finally:
                with self._lock:
                    self._spawned.pop(task_id, None)

        threading.Thread(target=_bg, daemon=True, name=f"runtime-{task_id}").start()

    def _spawn_parallel(self, task: Dict[str, Any], step: Dict[str, Any], fe) -> None:
        task_id = task["task_id"]
        from hr_scaling import expand_parallel_branches, register_dynamic_agents

        branches = expand_parallel_branches(fe.parallel_branches(step))
        register_dynamic_agents(task_id, branches, self.workflow.task_service)
        if not branches:
            self._spawn_for_task(task)
            return

        with self._lock:
            if self._spawned.get(task_id):
                return
            self._spawned[task_id] = f"{task_id}:parallel"

        pending = {"n": len(branches)}
        lock = threading.Lock()
        merged_arts: List[str] = []

        def _branch_done(aid: str, summary: Optional[str], artifacts: List[str]) -> None:
            with lock:
                merged_arts.extend(artifacts or [])
                pending["n"] -= 1
                done = pending["n"] <= 0
            if not done:
                return
            merge_rules = step.get("merge_gate") or step.get("postcondition") or []
            if merge_rules:
                from postcondition import check_postconditions
                check = check_postconditions(task_id, merge_rules if isinstance(merge_rules, list) else [merge_rules])
                self.workflow.task_service.patch_runtime(task_id, {"merge_gate": check})
                if not check["passed"]:
                    fe.handle_step_failure(task_id, self.workflow, step, check, "parallel")
                    with self._lock:
                        self._spawned.pop(task_id, None)
                    return
            self.on_agent_report_done(task_id, "parallel", f"并行 {len(branches)} 路完成", merged_arts)
            with self._lock:
                self._spawned.pop(task_id, None)

        prompt_base = self._build_agent_prompt(task)
        manager = self._get_manager()

        for branch in branches:
            aid = str(branch.get("spawn_agent_id") or branch.get("role", "rd_center"))
            skills = branch.get("skills")
            branch_prompt = f"{prompt_base}\n\n【并行 {branch.get('instance_id', aid)}】\n{branch.get('description', '')}"

            def _run(aid=aid, branch_prompt=branch_prompt, skills=skills, branch=branch):
                be_name = branch.get("executor") or fe.step_executor(task)
                if be_name == "claude_code":
                    from runtime.execution_backends import ClaudeCodeBackend
                    ClaudeCodeBackend().run(aid, branch_prompt, task_id=task_id, on_report_done=_branch_done)
                else:
                    manager.spawn(
                        agent_id=aid,
                        initial_task=branch_prompt,
                        task_id=task_id,
                        skill_ids=list(skills) if skills else None,
                        on_report_done=_branch_done,
                    )

            threading.Thread(target=_run, daemon=True, name=f"parallel-{task_id}-{aid}").start()

    def _build_agent_prompt(self, task: Dict[str, Any]) -> str:
        lines = [
            f"【CorpPilot 任务】{task.get('task_id')} — {task.get('title', '')}",
            f"类型: {task.get('type')} | 优先级: {task.get('priority')}",
            f"状态: {task.get('status')} | 负责: {task.get('execution_owner')}",
            "",
            task.get("description") or "（无详细描述）",
            "",
            "请完成本阶段工作。完成后务必调用 report_done，并在 artifacts 中列出产出文件路径。",
        ]
        existing = task.get("artifacts") or []
        if existing:
            lines.append("\n已有产出：")
            for item in existing[-5:]:
                if isinstance(item, dict):
                    lines.append(f"  - {item.get('path', item)}")
                else:
                    lines.append(f"  - {item}")
        return "\n".join(lines)

    def on_agent_report_done(
        self,
        task_id: str,
        agent_id: str,
        summary: str,
        artifacts: List[str],
    ) -> None:
        ts = self.workflow.task_service
        entries = []
        for path in artifacts:
            entries.append(
                {
                    "path": path,
                    "agent_id": agent_id,
                    "summary": summary,
                    "recorded_at": utc_now_iso(),
                }
            )
        if summary and not entries:
            entries.append(
                {
                    "path": f"artifacts/{task_id}/summary.txt",
                    "agent_id": agent_id,
                    "summary": summary,
                    "recorded_at": utc_now_iso(),
                }
            )
            summary_path = ARTIFACTS_DIR / task_id / "summary.txt"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(summary, encoding="utf-8")

        if entries:
            ts.append_artifacts(task_id, entries, actor=agent_id)

        ts.patch_runtime(
            task_id,
            {"last_report": summary[:500], "last_agent": agent_id, "reported_at": utc_now_iso()},
        )

        task = ts.get_task(task_id)
        if not task:
            return

        flow_id = task.get("flow_id", "legacy")
        if flow_id and flow_id != "legacy":
            self._complete_flow_step(task_id, agent_id, task)
            return

        if TaskStatus(task["status"]) == TaskStatus.EXECUTING:
            try:
                self.execution.complete(task_id, agent_id)
            except ValueError:
                pass

    def _complete_flow_step(
        self,
        task_id: str,
        agent_id: str,
        task: Dict[str, Any],
    ) -> None:
        from flow_engine import FlowEngine
        from postcondition import check_postconditions

        fe = FlowEngine(self.workflow.task_service)
        ctx = fe.get_flow_context(task)
        step = ctx.get("current_step") or {}
        if fe.is_supervisor_step(step):
            fe.run_supervisor_step(task_id, self.workflow, agent_id)
            return

        check = check_postconditions(
            task_id,
            step.get("postcondition", []),
            step.get("outputs"),
        )
        ts = self.workflow.task_service
        ts.patch_runtime(
            task_id,
            {
                "last_postcondition": check,
                "postcondition_passed": check["passed"],
            },
        )
        if not check["passed"]:
            fe.handle_step_failure(task_id, self.workflow, step, check, agent_id)
            return

        try:
            task = fe.advance(task_id, actor=agent_id)
        except ValueError:
            return

        if task.get("flow_step_id") == "completed":
            try:
                self.workflow.transition(task_id, TaskStatus.COMPLETED, agent_id)
            except ValueError:
                pass
            return

        try:
            fe.start_current_step(task_id, self.workflow)
        except ValueError:
            pass
