#!/usr/bin/env python3
"""
FlowEngine — 读取 flows/*.yaml，驱动任务 step、gate_mode 与 legacy 状态机兼容。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import TASK_STAGE_OWNER, TASK_TYPE_OWNER, TaskService, TaskStatus, TaskType, utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FLOWS_DIR = PROJECT_ROOT / "flows"
LEGACY_FLOW_ID = "legacy"


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        pass
    # 无 PyYAML 时：仅支持本仓库内嵌的 JSON 等价定义
    json_path = path.with_suffix(".json")
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))
    raise ImportError(
        "加载 flow 需要 PyYAML（pip install pyyaml）或提供同名 .json 文件"
    )


def list_flow_ids() -> List[str]:
    ids: List[str] = [LEGACY_FLOW_ID]
    if not FLOWS_DIR.exists():
        return ids
    for path in sorted(FLOWS_DIR.glob("*.yaml")):
        ids.append(path.stem)
    for path in sorted(FLOWS_DIR.glob("*.json")):
        if path.stem not in ids:
            ids.append(path.stem)
    return ids


def load_flow(flow_id: str) -> Dict[str, Any]:
    if flow_id == LEGACY_FLOW_ID:
        return {
            "id": LEGACY_FLOW_ID,
            "name": "默认十三部门链",
            "description": "兼容 TASK_STAGE_OWNER 状态机",
            "steps": [],
        }
    for ext in (".json", ".yaml"):
        path = FLOWS_DIR / f"{flow_id}{ext}"
        if not path.exists():
            continue
        if ext == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = _load_yaml(path)
        data.setdefault("id", flow_id)
        return data
    raise ValueError(f"Flow 不存在: {flow_id}")


def normalize_steps(flow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将 parallel 等复合 step 展平为可执行序列（并行组作为单步）。"""
    steps: List[Dict[str, Any]] = []
    for raw in flow.get("steps", []):
        if "parallel" in raw:
            steps.append(
                {
                    "id": raw.get("id", "parallel_group"),
                    "type": "parallel",
                    "parallel": raw["parallel"],
                    "gate_mode": raw.get("gate_mode", "auto"),
                    "postcondition": raw.get("postcondition", []),
                    "merge_gate": raw.get("merge_gate", []),
                    "on_fail": raw.get("on_fail"),
                    "send_back_to": raw.get("send_back_to"),
                    "max_retries": raw.get("max_retries"),
                }
            )
        else:
            steps.append(raw)
    return steps


def summarize_flow(flow_id: str) -> Dict[str, Any]:
    """Flow 市场摘要：步骤列表与 gate 信息。"""
    flow = load_flow(flow_id)
    steps = normalize_steps(flow)
    return {
        "id": flow.get("id", flow_id),
        "name": flow.get("name"),
        "description": flow.get("description"),
        "step_count": len(steps),
        "steps": [
            {
                "id": s.get("id"),
                "role": s.get("role"),
                "type": s.get("type", "standard"),
                "gate_mode": s.get("gate_mode", "auto"),
                "skills": s.get("skills") or [],
            }
            for s in steps
        ],
    }


class FlowEngine:
    """任务级 Flow 编排，与 WorkflowEngine 协同。"""

    GATE_MODES_NEED_APPROVAL = {
        "gate",
        "founder_select_one",
        "founder_ack",
        "override",
    }

    def __init__(self, task_service: Optional[TaskService] = None):
        self.task_service = task_service or TaskService()

    def attach_flow(self, task_id: str, flow_id: str, actor: str = "system") -> Dict[str, Any]:
        flow = load_flow(flow_id)
        steps = normalize_steps(flow)
        if not steps and flow_id != LEGACY_FLOW_ID:
            raise ValueError(f"Flow {flow_id} 无有效 steps")

        task: Dict[str, Any] = {}

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self.task_service._find_task(tasks, task_id)
            task["flow_id"] = flow_id
            task["flow_step_index"] = 0
            task["flow_state"] = {
                "skipped_steps": [],
                "gate_pending": False,
                "retries": {},
            }
            if steps:
                first = steps[0]
                task["flow_step_id"] = first.get("id")
                task["execution_owner"] = first.get("role", task.get("execution_owner"))
                task["current_owner"] = task["execution_owner"]
            return tasks

        self.task_service.store.update([], mutate)
        self.task_service.event_log.append(
            "task", "flow_attached", actor, task_id, {"flow_id": flow_id}
        )
        return task

    def get_flow_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            return {
                "flow_id": LEGACY_FLOW_ID,
                "mode": "legacy",
                "current_owner": task.get("current_owner"),
                "status": task.get("status"),
            }
        flow = load_flow(flow_id)
        steps = normalize_steps(flow)
        idx = int(task.get("flow_step_index", 0))
        step = steps[idx] if idx < len(steps) else None
        return {
            "flow_id": flow_id,
            "flow_name": flow.get("name"),
            "mode": "flow",
            "step_index": idx,
            "step_total": len(steps),
            "current_step": step,
            "flow_step_id": task.get("flow_step_id"),
            "flow_state": task.get("flow_state", {}),
            "gate_pending": (task.get("flow_state") or {}).get("gate_pending"),
            "steps_timeline": self.build_steps_timeline(task),
        }

    def build_steps_timeline(self, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """供 Dashboard 渲染的 Flow 步骤进度。"""
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            return []
        flow = load_flow(flow_id)
        steps = normalize_steps(flow)
        idx = int(task.get("flow_step_index", 0))
        state = task.get("flow_state") or {}
        skipped = set(state.get("skipped_steps", [])) | set(self.apply_hotfix_skips(task))
        completed = task.get("flow_step_id") == "completed" or idx >= len(steps)
        timeline: List[Dict[str, Any]] = []
        for i, step in enumerate(steps):
            sid = step.get("id", f"step_{i}")
            if sid in skipped or step.get("gate_mode") == "skip":
                status = "skipped"
            elif completed:
                status = "done"
            elif i < idx:
                status = "done"
            elif i == idx:
                status = "current"
            else:
                status = "pending"
            timeline.append(
                {
                    "id": sid,
                    "index": i,
                    "role": step.get("role"),
                    "type": step.get("type", "standard"),
                    "gate_mode": step.get("gate_mode", "auto"),
                    "status": status,
                }
            )
        return timeline

    def apply_hotfix_skips(self, task: Dict[str, Any]) -> List[str]:
        """hotfix flow 默认跳过产品 Demo 类步骤。"""
        if task.get("flow_id") != "hotfix":
            return []
        skip_ids = {"product_demo", "board_discussion"}
        state = dict(task.get("flow_state") or {})
        skipped = list(state.get("skipped_steps", []))
        for sid in skip_ids:
            if sid not in skipped:
                skipped.append(sid)
        return skipped

    def advance(
        self,
        task_id: str,
        actor: str = "system",
        force: bool = False,
    ) -> Dict[str, Any]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            raise ValueError("legacy 任务请使用 WorkflowEngine.transition 推进状态")

        flow = load_flow(flow_id)
        steps = normalize_steps(flow)
        idx = int(task.get("flow_step_index", 0))
        state = dict(task.get("flow_state") or {})
        skipped = set(state.get("skipped_steps", [])) | set(self.apply_hotfix_skips(task))

        if idx >= len(steps):
            raise ValueError("Flow 已走完所有步骤")

        current = steps[idx]
        gate_mode = current.get("gate_mode", "auto")
        if gate_mode in self.GATE_MODES_NEED_APPROVAL and state.get("gate_pending") and not force:
            raise ValueError(f"当前 step 等待 gate 确认: {current.get('id')}")

        # 进入下一步
        next_idx = idx + 1
        while next_idx < len(steps):
            nxt = steps[next_idx]
            if nxt.get("id") in skipped or nxt.get("gate_mode") == "skip":
                next_idx += 1
                continue
            break

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self.task_service._find_task(tasks, task_id)
            fs = dict(task.get("flow_state") or {})
            fs["gate_pending"] = False
            fs["last_advanced_by"] = actor
            fs["last_advanced_at"] = utc_now_iso()
            task["flow_state"] = fs
            task["flow_step_index"] = next_idx
            if next_idx < len(steps):
                nxt = steps[next_idx]
                task["flow_step_id"] = nxt.get("id")
                role = nxt.get("role")
                if isinstance(nxt.get("parallel"), list) and nxt["parallel"]:
                    role = nxt["parallel"][0].get("role", role)
                if role:
                    task["execution_owner"] = role
                    task["current_owner"] = role
            else:
                task["flow_step_id"] = "completed"
            task["updated_at"] = utc_now_iso()
            self.task_service._append_history(
                task,
                f"flow_advance:{current.get('id')}",
                actor,
                {"from_index": idx, "to_index": next_idx},
            )
            return tasks

        self.task_service.store.update([], mutate)
        task = self.task_service.get_task(task_id) or {}
        next_step = steps[next_idx] if next_idx < len(steps) else None
        self._maybe_trigger_step(task, next_step)
        return task

    def approve_gate(self, task_id: str, actor: str, note: str = "") -> Dict[str, Any]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self.task_service._find_task(tasks, task_id)
            fs = dict(task.get("flow_state") or {})
            fs["gate_pending"] = False
            fs["gate_approved_by"] = actor
            fs["gate_note"] = note
            task["flow_state"] = fs
            if TaskStatus(task["status"]) == TaskStatus.BLOCKED:
                task["status"] = TaskStatus.EXECUTING.value
            task["updated_at"] = utc_now_iso()
            self.task_service._append_history(task, "flow_gate_approved", actor, {"note": note})
            return tasks

        self.task_service.store.update([], mutate)
        return self.advance(task_id, actor=actor, force=True)

    def skip_remaining(self, task_id: str, step_ids: List[str], actor: str) -> Dict[str, Any]:
        """董事会 direct_order / 紧急通道：跳过指定 step。"""

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            task = self.task_service._find_task(tasks, task_id)
            fs = dict(task.get("flow_state") or {})
            skipped = list(fs.get("skipped_steps", []))
            for sid in step_ids:
                if sid not in skipped:
                    skipped.append(sid)
            fs["skipped_steps"] = skipped
            task["flow_state"] = fs
            task["updated_at"] = utc_now_iso()
            self.task_service._append_history(
                task, "flow_skip", actor, {"step_ids": step_ids}
            )
            return tasks

        self.task_service.store.update([], mutate)
        return self.advance(task_id, actor=actor, force=True)

    def start_current_step(self, task_id: str, workflow: Any) -> Dict[str, Any]:
        """将 flow step 与 executing 状态对齐并交给 Runtime。"""
        from core import ExecutionService, TaskStatus

        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            return ExecutionService(workflow).start(task_id, "flow_engine")

        flow = load_flow(flow_id)
        steps = normalize_steps(flow)
        idx = int(task.get("flow_step_index", 0))
        if idx >= len(steps):
            raise ValueError("无当前 step")
        step = steps[idx]
        gate_mode = step.get("gate_mode", "auto")

        if gate_mode in self.GATE_MODES_NEED_APPROVAL:
            def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                t = self.task_service._find_task(tasks, task_id)
                fs = dict(t.get("flow_state") or {})
                fs["gate_pending"] = True
                t["flow_state"] = fs
                t["status"] = TaskStatus.BLOCKED.value
                t["updated_at"] = utc_now_iso()
                return tasks

            self.task_service.store.update([], mutate)
            return self.task_service.get_task(task_id) or {}

        role = step.get("role", task.get("execution_owner"))
        if step.get("type") == "parallel" and step.get("parallel"):
            role = step["parallel"][0].get("role", role)

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            t["execution_owner"] = role
            t["current_owner"] = role
            t["flow_step_id"] = step.get("id")
            return tasks

        self.task_service.store.update([], mutate)
        if TaskStatus(task["status"]) == TaskStatus.DISPATCHED:
            return workflow.transition(task_id, TaskStatus.EXECUTING, "flow_engine")
        if TaskStatus(task["status"]) in {TaskStatus.APPROVED, TaskStatus.PLANNED}:
            for st in (TaskStatus.DISPATCHED, TaskStatus.EXECUTING):
                try:
                    task = workflow.transition(task_id, st, "flow_engine")
                except ValueError:
                    break
            return task
        return workflow.transition(task_id, TaskStatus.EXECUTING, "flow_engine")

    def _maybe_trigger_step(
        self,
        task: Dict[str, Any],
        step: Optional[Dict[str, Any]],
    ) -> None:
        if not step:
            return
        gate = step.get("gate_mode", "auto")
        if gate == "skip":
            self.advance(task["task_id"], actor="flow:auto_skip", force=True)

    def step_skills(self, task: Dict[str, Any]) -> List[str]:
        ctx = self.get_flow_context(task)
        step = ctx.get("current_step")
        if not step:
            return []
        skills = step.get("skills", [])
        return list(skills) if isinstance(skills, list) else []

    def is_supervisor_step(self, step: Optional[Dict[str, Any]]) -> bool:
        return bool(step and step.get("type") == "supervisor")

    def is_parallel_step(self, step: Optional[Dict[str, Any]]) -> bool:
        return bool(step and step.get("type") == "parallel" and step.get("parallel"))

    def is_close_step(self, step: Optional[Dict[str, Any]]) -> bool:
        return bool(step and step.get("id") == "project_close")

    def parallel_branches(self, step: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(step.get("parallel") or [])

    def step_executor(self, task: Dict[str, Any]) -> str:
        from runtime.execution_backends import resolve_backend_name
        ctx = self.get_flow_context(task)
        return resolve_backend_name(ctx.get("current_step"))

    def resolve_send_back_target(
        self, steps: List[Dict[str, Any]], current_idx: int, step: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        explicit = step.get("send_back_to")
        if explicit:
            for i, s in enumerate(steps):
                if s.get("id") == explicit:
                    return i, s
        for i in range(current_idx - 1, -1, -1):
            candidate = steps[i]
            if candidate.get("type") == "supervisor":
                continue
            if candidate.get("gate_mode") == "skip":
                continue
            return i, candidate
        return 0, steps[0] if steps else {}

    def rewind_to_step(
        self, task_id: str, target_index: int, actor: str, reason: str = ""
    ) -> Dict[str, Any]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            raise ValueError("legacy 任务不支持 Flow 回退")
        steps = normalize_steps(load_flow(flow_id))
        if target_index < 0 or target_index >= len(steps):
            raise ValueError(f"无效 step index: {target_index}")
        target = steps[target_index]
        from_step = task.get("flow_step_id")

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            fs = dict(t.get("flow_state") or {})
            fs["gate_pending"] = False
            fs["last_send_back"] = {
                "from": from_step,
                "to": target.get("id"),
                "reason": reason,
                "at": utc_now_iso(),
            }
            t["flow_state"] = fs
            t["flow_step_index"] = target_index
            t["flow_step_id"] = target.get("id")
            role = target.get("role")
            if target.get("type") == "parallel" and target.get("parallel"):
                role = target["parallel"][0].get("role", role)
            if role:
                t["execution_owner"] = role
                t["current_owner"] = role
            t["updated_at"] = utc_now_iso()
            self.task_service._append_history(
                t, "flow_rewind", actor, {"to_step": target.get("id"), "reason": reason}
            )
            return tasks

        self.task_service.store.update([], mutate)
        return self.task_service.get_task(task_id) or {}

    def _force_executing(self, task_id: str, workflow: Any, actor: str) -> Dict[str, Any]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        current = TaskStatus(task["status"])
        if current == TaskStatus.EXECUTING:
            return task

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            t["status"] = TaskStatus.EXECUTING.value
            t["updated_at"] = utc_now_iso()
            self.task_service._append_history(
                t, "flow_force_executing", actor, {"from": current.value}
            )
            return tasks

        self.task_service.store.update([], mutate)
        task = self.task_service.get_task(task_id) or {}
        workflow._notify_status_enter(task, current, TaskStatus.EXECUTING)
        return task

    def handle_step_failure(
        self,
        task_id: str,
        workflow: Any,
        step: Dict[str, Any],
        check: Dict[str, Any],
        actor: str,
    ) -> Dict[str, Any]:
        """postcondition 失败：retry 同 step 或 send_back 至上游 step 并 respawn。"""
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        flow_id = task.get("flow_id", LEGACY_FLOW_ID)
        if flow_id == LEGACY_FLOW_ID:
            return task

        steps = normalize_steps(load_flow(flow_id))
        ctx = self.get_flow_context(task)
        idx = int(ctx.get("step_index", 0))
        sid = step.get("id", "unknown")
        fs = dict(task.get("flow_state") or {})
        retries = dict(fs.get("retries", {}))
        retries[sid] = retries.get(sid, 0) + 1
        max_retries = int(step.get("max_retries", 3))
        on_fail = step.get("on_fail") or "send_back"
        errors = "; ".join(check.get("errors", []))

        def mutate_retries(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            fs2 = dict(t.get("flow_state") or {})
            fs2["retries"] = retries
            fs2["last_failure"] = {
                "step": sid,
                "errors": check.get("errors", []),
                "at": utc_now_iso(),
            }
            t["flow_state"] = fs2
            t["updated_at"] = utc_now_iso()
            return tasks

        self.task_service.store.update([], mutate_retries)

        if on_fail == "retry" and retries[sid] <= max_retries:
            task = self._force_executing(task_id, workflow, actor)
            try:
                self.start_current_step(task_id, workflow)
            except ValueError:
                pass
            return self.task_service.get_task(task_id) or task

        if on_fail == "send_back":
            if retries[sid] <= max_retries and step.get("type") != "supervisor":
                task = self._force_executing(task_id, workflow, actor)
                try:
                    self.start_current_step(task_id, workflow)
                except ValueError:
                    pass
                return self.task_service.get_task(task_id) or task

            target_idx, _ = self.resolve_send_back_target(steps, idx, step)
            task = self.rewind_to_step(task_id, target_idx, actor, errors)
            task = self._force_executing(task_id, workflow, actor)
            try:
                self.start_current_step(task_id, workflow)
            except ValueError:
                pass
            return self.task_service.get_task(task_id) or task

        return self.task_service.get_task(task_id) or task

    def override_gate(self, task_id: str, workflow: Any, actor: str, note: str = "") -> Dict[str, Any]:
        """创始人强制跳过 Gate（gate_mode: override / 紧急通道）。"""
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            fs = dict(t.get("flow_state") or {})
            fs["gate_pending"] = False
            fs["gate_overridden_by"] = actor
            fs["gate_override_note"] = note
            t["flow_state"] = fs
            if TaskStatus(t["status"]) == TaskStatus.BLOCKED:
                t["status"] = TaskStatus.EXECUTING.value
            t["updated_at"] = utc_now_iso()
            self.task_service._append_history(t, "flow_gate_override", actor, {"note": note})
            return tasks

        self.task_service.store.update([], mutate)
        return self.advance(task_id, actor=actor, force=True)

    def run_supervisor_step(
        self,
        task_id: str,
        workflow: Any,
        actor: str = "supervisor",
        verdict: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        监督型 step：仅跑 postcondition，不 spawn 产出型 Agent。
        verdict: pass | fail | None（自动根据 postcondition）
        """
        from postcondition import check_postconditions

        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        ctx = self.get_flow_context(task)
        step = ctx.get("current_step")
        if not self.is_supervisor_step(step):
            raise ValueError(f"当前 step 非 supervisor: {task.get('flow_step_id')}")

        check = check_postconditions(
            task_id,
            step.get("postcondition", []),
            step.get("inputs") or step.get("outputs"),
        )
        passed = check["passed"] if verdict is None else verdict == "pass"
        if verdict == "fail":
            passed = False

        def mutate_hist(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            fs = dict(t.get("flow_state") or {})
            fs.update(
                {
                    "supervisor_verdict": "pass" if passed else "fail",
                    "supervisor_check": check,
                    "supervisor_actor": actor,
                    "supervisor_at": utc_now_iso(),
                }
            )
            t["flow_state"] = fs
            t["updated_at"] = utc_now_iso()
            self.task_service._append_history(
                t,
                f"supervisor:{'pass' if passed else 'fail'}",
                actor,
                {"step_id": step.get("id"), "errors": check.get("errors", [])},
            )
            return tasks

        self.task_service.store.update([], mutate_hist)

        audit_path = PROJECT_ROOT / "artifacts" / task_id / "delivery_audit.md"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Delivery Audit — {task_id}",
            f"- step: {step.get('id')}",
            f"- verdict: {'PASS' if passed else 'FAIL'}",
            f"- actor: {actor}",
            "",
        ]
        if check.get("errors"):
            lines.append("## 未通过项\n")
            for err in check["errors"]:
                lines.append(f"- {err}\n")
        audit_path.write_text("\n".join(lines), encoding="utf-8")
        self.task_service.append_artifacts(
            task_id,
            [{"path": str(audit_path.relative_to(PROJECT_ROOT)), "agent_id": actor, "type": "delivery_audit"}],
            actor=actor,
        )

        if passed:
            task = self.advance(task_id, actor=actor, force=True)
            if task.get("flow_step_id") == "completed":
                try:
                    workflow.transition(task_id, TaskStatus.COMPLETED, actor)
                except ValueError:
                    pass
            else:
                try:
                    self.start_current_step(task_id, workflow)
                except ValueError:
                    pass
            return task

        return self.handle_step_failure(task_id, workflow, step, check, actor)

    def run_close_step(
        self,
        task_id: str,
        workflow: Any,
        actor: str = "project_close",
    ) -> Dict[str, Any]:
        """结案 step：自动生成财务/法务产出，等待 founder_ack。"""
        from project_close import check_closeout_outputs, emit_project_close_artifacts

        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        ctx = self.get_flow_context(task)
        step = ctx.get("current_step")
        if not self.is_close_step(step):
            raise ValueError(f"当前 step 非 project_close: {task.get('flow_step_id')}")

        router = None
        try:
            from runtime.model_router import ModelRouter

            router = ModelRouter()
        except Exception:
            pass

        closeout = emit_project_close_artifacts(task_id, self.task_service, router=router)
        check = check_closeout_outputs(task_id)

        def mutate_hist(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            t = self.task_service._find_task(tasks, task_id)
            fs = dict(t.get("flow_state") or {})
            fs.update(
                {
                    "closeout": closeout,
                    "closeout_check": check,
                    "closeout_at": utc_now_iso(),
                    "gate_pending": step.get("gate_mode") in self.GATE_MODES_NEED_APPROVAL,
                }
            )
            t["flow_state"] = fs
            t["updated_at"] = utc_now_iso()
            if fs.get("gate_pending"):
                t["status"] = TaskStatus.BLOCKED.value
            self.task_service._append_history(
                t,
                "project_close:artifacts",
                actor,
                {"passed": check.get("passed"), "errors": check.get("errors", [])},
            )
            return tasks

        self.task_service.store.update([], mutate_hist)
        return self.task_service.get_task(task_id) or {}
