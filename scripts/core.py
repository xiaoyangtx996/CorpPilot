#!/usr/bin/env python3
"""
CorpPilot 核心领域模型与服务。统一任务流、董事会提案流、Agent 配置与 Skill 配置。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from file_lock import with_file_lock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
AGENTS_DIR = PROJECT_ROOT / "agents"
SKILLS_DIR = PROJECT_ROOT / "skills"


def utc_now_iso() -> str:
    """返回本地时区的 ISO 时间字符串。"""
    return datetime.now().isoformat(timespec="microseconds")


def resolve_data_dir(explicit_dir: Optional[Path | str] = None) -> Path:
    """解析数据目录，优先使用显式入参，其次读取环境变量。"""
    if explicit_dir:
        return Path(explicit_dir)
    override = None
    try:
        import os

        override = os.environ.get("CORPPILOT_DATA_DIR")
    except Exception:
        override = None
    return Path(override) if override else DEFAULT_DATA_DIR


class TaskStatus(str, Enum):
    """任务状态。"""

    PENDING = "pending"
    CLASSIFIED = "classified"
    PLANNED = "planned"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    REVIEW = "review"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class TaskType(str, Enum):
    """任务类型。"""

    RD = "RD"
    PD = "PD"
    DA = "DA"
    OP = "OP"
    MK = "MK"
    FN = "FN"
    HR = "HR"
    LG = "LG"


class TaskPriority(str, Enum):
    """任务优先级。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


VALID_TASK_TRANSITIONS: Dict[TaskStatus, List[TaskStatus]] = {
    TaskStatus.PENDING: [TaskStatus.CLASSIFIED],
    TaskStatus.CLASSIFIED: [TaskStatus.PLANNED],
    TaskStatus.PLANNED: [TaskStatus.REVIEWING],
    TaskStatus.REVIEWING: [TaskStatus.APPROVED, TaskStatus.REJECTED],
    TaskStatus.REJECTED: [TaskStatus.PLANNED],
    TaskStatus.APPROVED: [TaskStatus.DISPATCHED],
    TaskStatus.DISPATCHED: [TaskStatus.EXECUTING],
    TaskStatus.EXECUTING: [TaskStatus.REVIEW, TaskStatus.BLOCKED],
    TaskStatus.BLOCKED: [TaskStatus.EXECUTING],
    TaskStatus.REVIEW: [TaskStatus.COMPLETED, TaskStatus.EXECUTING],
    TaskStatus.COMPLETED: [],
}


TASK_TYPE_OWNER = {
    TaskType.RD.value: "rd_center",
    TaskType.PD.value: "product_center",
    TaskType.DA.value: "data_center",
    TaskType.OP.value: "operation_center",
    TaskType.MK.value: "marketing_center",
    TaskType.FN.value: "finance",
    TaskType.HR.value: "hr",
    TaskType.LG.value: "legal",
}


TASK_STAGE_OWNER = {
    TaskStatus.PENDING.value: "president_office",
    TaskStatus.CLASSIFIED.value: "strategy",
    TaskStatus.PLANNED.value: "risk_center",
    TaskStatus.REVIEWING.value: "risk_center",
    TaskStatus.APPROVED.value: "pmo",
    TaskStatus.REJECTED.value: "strategy",
    TaskStatus.DISPATCHED.value: "pmo",
    TaskStatus.EXECUTING.value: "execution",
    TaskStatus.REVIEW.value: "pmo",
    TaskStatus.COMPLETED.value: "ceo",
    TaskStatus.BLOCKED.value: "pmo",
}


class DecisionType(str, Enum):
    """董事会决策类型。"""

    STRATEGIC = "strategic"
    EMERGENCY = "emergency"
    RULE_CHANGE = "rule_change"


class VoteResult(str, Enum):
    """投票结果。"""

    AGREE = "agree"
    DISAGREE = "disagree"
    ABSTAIN = "abstain"


@dataclass
class BoardMember:
    """董事会成员。"""

    id: str
    name: str
    role: str
    vote_weight: float = 1.0


BOARD_MEMBERS: Dict[str, BoardMember] = {
    "chairman": BoardMember("chairman", "\u8463\u4e8b\u957f", "chairman", 1.5),
    "ceo": BoardMember("ceo", "CEO", "ceo", 1.0),
    "president_office": BoardMember("president_office", "\u603b\u88c1\u529e", "president_office", 1.0),
    "strategy": BoardMember("strategy", "\u6218\u7565\u53d1\u5c55\u90e8", "strategy", 1.0),
}


@dataclass
class Proposal:
    """董事会提案。"""

    id: str
    title: str
    content: str
    proposer: str
    decision_type: str
    created_at: str
    status: str = "pending"
    votes: List[Dict[str, Any]] = field(default_factory=list)
    discussion: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None


AGENT_ROLES: Dict[str, Dict[str, str]] = {
    "ceo": {"name": "CEO", "name_cn": "\u9996\u5e2d\u6267\u884c\u5b98", "layer": "decision", "description": "\u6218\u7565\u51b3\u7b56\u3001\u6700\u7ec8\u5ba1\u6279"},
    "president_office": {"name": "\u603b\u88c1\u529e", "name_cn": "\u603b\u88c1\u529e\u516c\u5ba4", "layer": "decision", "description": "\u4fe1\u606f\u67a2\u7ebd\u3001\u4efb\u52a1\u5206\u62e3"},
    "strategy": {"name": "\u6218\u7565\u53d1\u5c55\u90e8", "name_cn": "\u6218\u7565\u53d1\u5c55\u90e8", "layer": "decision", "description": "\u89c4\u5212\u4e2d\u67a2\u3001\u65b9\u6848\u8bbe\u8ba1"},
    "risk_center": {"name": "\u98ce\u63a7\u4e2d\u5fc3", "name_cn": "\u98ce\u9669\u63a7\u5236\u4e2d\u5fc3", "layer": "review", "description": "\u98ce\u9669\u5ba1\u6838\u3001\u5408\u89c4\u628a\u63a7"},
    "pmo": {"name": "PMO", "name_cn": "\u9879\u76ee\u7ba1\u7406\u529e\u516c\u5ba4", "layer": "review", "description": "\u9879\u76ee\u7edf\u7b79\u3001\u8d44\u6e90\u8c03\u5ea6"},
    "rd_center": {"name": "\u7814\u53d1\u4e2d\u5fc3", "name_cn": "\u6280\u672f\u7814\u53d1\u4e2d\u5fc3", "layer": "execution", "description": "\u6280\u672f\u5f00\u53d1\u3001\u7cfb\u7edf\u5b9e\u73b0"},
    "product_center": {"name": "\u4ea7\u54c1\u4e2d\u5fc3", "name_cn": "\u4ea7\u54c1\u8bbe\u8ba1\u4e2d\u5fc3", "layer": "execution", "description": "\u9700\u6c42\u5206\u6790\u3001\u4ea7\u54c1\u8bbe\u8ba1"},
    "data_center": {"name": "\u6570\u636e\u4e2d\u5fc3", "name_cn": "\u6570\u636e\u667a\u80fd\u4e2d\u5fc3", "layer": "execution", "description": "\u6570\u636e\u5206\u6790\u3001\u6570\u636e\u6cbb\u7406"},
    "operation_center": {"name": "\u8fd0\u8425\u4e2d\u5fc3", "name_cn": "\u7528\u6237\u8fd0\u8425\u4e2d\u5fc3", "layer": "execution", "description": "\u7528\u6237\u589e\u957f\u3001\u6d3b\u52a8\u8fd0\u8425"},
    "marketing_center": {"name": "\u5e02\u573a\u4e2d\u5fc3", "name_cn": "\u5e02\u573a\u8425\u9500\u4e2d\u5fc3", "layer": "execution", "description": "\u54c1\u724c\u63a8\u5e7f\u3001\u5e02\u573a\u8425\u9500"},
    "finance": {"name": "\u8d22\u52a1\u90e8", "name_cn": "\u8d22\u52a1\u7ba1\u7406\u90e8", "layer": "support", "description": "\u8d44\u6e90\u914d\u989d\u7ba1\u7406\u3001\u6210\u672c\u63a7\u5236"},
    "legal": {"name": "\u6cd5\u52a1\u90e8", "name_cn": "\u6cd5\u52a1\u5408\u89c4\u90e8", "layer": "support", "description": "\u89c4\u5219\u5408\u89c4\u68c0\u67e5\u3001\u5408\u540c\u5ba1\u67e5"},
    "hr": {"name": "HR", "name_cn": "\u4eba\u529b\u8d44\u6e90\u90e8", "layer": "support", "description": "\u667a\u80fd\u4f53\u7ba1\u7406\u3001\u80fd\u529b\u914d\u7f6e"},
}


class JsonStore:
    """带文件锁的 JSON 仓储。"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.lock_path = self.file_path.parent / "locks" / f"{self.file_path.name}.lock"

    def read(self, default: Any) -> Any:
        if not self.file_path.exists():
            return default
        with open(self.file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, value: Any) -> Any:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with with_file_lock(str(self.lock_path)):
            with open(self.file_path, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
        return value

    def update(self, default: Any, updater: Callable[[Any], Any]) -> Any:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with with_file_lock(str(self.lock_path)):
            if self.file_path.exists():
                with open(self.file_path, "r", encoding="utf-8") as handle:
                    current = json.load(handle)
            else:
                current = default
            updated = updater(current)
            with open(self.file_path, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
        return updated

    def reset(self, value: Any) -> Any:
        """重置文件内容。"""
        return self.write(value)


class EventLogService:
    """统一事件日志服务。"""

    def __init__(self, data_dir: Optional[Path | str] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.store = JsonStore(self.data_dir / "events.json")

    def append(self, category: str, action: str, actor: str, subject_id: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event: Dict[str, Any] = {}

        def mutate(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal event
            event = {
                "event_id": f"EVT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(events)+1:04d}",
                "category": category,
                "action": action,
                "actor": actor,
                "subject_id": subject_id,
                "timestamp": utc_now_iso(),
                "detail": detail or {},
            }
            events.append(event)
            return events

        self.store.update([], mutate)
        return event

    def list_events(self, category: Optional[str] = None, subject_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        events = self.store.read([])
        if category:
            events = [item for item in events if item["category"] == category]
        if subject_id:
            events = [item for item in events if item["subject_id"] == subject_id]
        events.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return events[:limit]


class TaskService:
    """任务领域服务。"""

    def __init__(self, data_dir: Optional[Path | str] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.store = JsonStore(self.data_dir / "tasks.json")
        self.event_log = EventLogService(self.data_dir)

    def _load(self) -> List[Dict[str, Any]]:
        return self.store.read([])

    def _save(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.store.write(tasks)

    def _find_task(self, tasks: List[Dict[str, Any]], task_id: str) -> Dict[str, Any]:
        task = next((item for item in tasks if item["task_id"] == task_id), None)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        return task

    def _append_history(self, task: Dict[str, Any], action: str, actor: str, detail: Optional[Dict[str, Any]] = None) -> None:
        event = {"action": action, "timestamp": utc_now_iso(), "actor": actor}
        if detail:
            event["detail"] = detail
        task["history"].append(event)

    def _generate_task_id(self, tasks: Iterable[Dict[str, Any]]) -> str:
        year = datetime.now().year
        count = sum(1 for task in tasks if str(task.get("task_id", "")).startswith(f"TASK-{year}-")) + 1
        return f"TASK-{year}-{count:04d}"

    def create_task(
        self,
        title: str,
        task_type: TaskType,
        priority: TaskPriority,
        requester: str,
        description: str = "",
        flow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        task: Dict[str, Any] = {}

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            now = utc_now_iso()
            task = {
                "task_id": self._generate_task_id(tasks),
                "title": title,
                "type": task_type.value,
                "priority": priority.value,
                "requester": requester,
                "description": description,
                "status": TaskStatus.PENDING.value,
                "current_owner": TASK_STAGE_OWNER[TaskStatus.PENDING.value],
                "execution_owner": TASK_TYPE_OWNER.get(task_type.value),
                "created_at": now,
                "updated_at": now,
                "history": [{"action": "created", "timestamp": now, "actor": requester}],
                "artifacts": [],
                "runtime": {},
                "auto_execute": True,
            }
            tasks.append(task)
            return tasks

        self.store.update([], mutate)
        self.event_log.append(
            category="task",
            action="created",
            actor=requester,
            subject_id=task["task_id"],
            detail={"type": task["type"], "priority": task["priority"], "current_owner": task["current_owner"]},
        )
        if flow_id and flow_id not in ("legacy", ""):
            try:
                from flow_engine import FlowEngine

                task = FlowEngine(self).attach_flow(task["task_id"], flow_id, requester)
            except Exception as exc:
                task["flow_attach_error"] = str(exc)
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return next((task for task in self._load() if task["task_id"] == task_id), None)

    def get_task_timeline(self, task_id: str) -> List[Dict[str, Any]]:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        return list(task.get("history", []))

    def get_task_artifacts(self, task_id: str) -> List[Dict[str, Any]]:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        return list(task.get("artifacts", []))

    def append_artifacts(
        self,
        task_id: str,
        entries: List[Dict[str, Any]],
        actor: str = "system",
    ) -> Dict[str, Any]:
        if not entries:
            return self.get_task(task_id) or {}
        task: Dict[str, Any] = {}

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self._find_task(tasks, task_id)
            artifacts = list(task.get("artifacts", []))
            artifacts.extend(entries)
            task["artifacts"] = artifacts
            task["updated_at"] = utc_now_iso()
            self._append_history(
                task,
                "artifacts_added",
                actor,
                {"count": len(entries), "paths": [e.get("path") for e in entries]},
            )
            return tasks

        self.store.update([], mutate)
        self.event_log.append(
            "task",
            "artifacts_added",
            actor,
            task_id,
            {"count": len(entries)},
        )
        return task

    def patch_runtime(self, task_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        task: Dict[str, Any] = {}

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self._find_task(tasks, task_id)
            runtime = dict(task.get("runtime", {}))
            runtime.update(patch)
            task["runtime"] = runtime
            task["updated_at"] = utc_now_iso()
            return tasks

        self.store.update([], mutate)
        return task

    def list_tasks(self, status: Optional[TaskStatus] = None, limit: int = 20) -> List[Dict[str, Any]]:
        tasks = self._load()
        if status:
            tasks = [task for task in tasks if task["status"] == status.value]
        tasks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return tasks[:limit]

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        task: Dict[str, Any] = {}
        protected = {"task_id", "created_at", "history", "status", "current_owner", "execution_owner", "updated_at"}
        invalid_fields = sorted(key for key in updates.keys() if key in protected)
        if invalid_fields:
            raise ValueError(f"以下字段只能通过专用流程更新: {invalid_fields}")

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task
            task = self._find_task(tasks, task_id)
            for key, value in updates.items():
                task[key] = value
            task["updated_at"] = utc_now_iso()
            return tasks

        self.store.update([], mutate)
        self.event_log.append("task", "updated", "system", task_id, {"fields": sorted(updates.keys())})
        return task

    def delete_task(self, task_id: str) -> bool:
        deleted = False

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal deleted
            after = [task for task in tasks if task["task_id"] != task_id]
            deleted = len(after) != len(tasks)
            return after

        self.store.update([], mutate)
        if not deleted:
            return False
        self.event_log.append("task", "deleted", "system", task_id)
        return True

    def update_task_status(self, task_id: str, new_status: TaskStatus, actor: str = "system") -> Dict[str, Any]:
        task: Dict[str, Any] = {}
        current_status: Optional[TaskStatus] = None

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task, current_status
            task = self._find_task(tasks, task_id)
            current_status = TaskStatus(task["status"])
            allowed = VALID_TASK_TRANSITIONS.get(current_status, [])
            if new_status not in allowed:
                raise ValueError(
                    f"非法状态转换: {current_status.value} -> {new_status.value}; "
                    f"允许转换: {[status.value for status in allowed]}"
                )

            now = utc_now_iso()
            task["status"] = new_status.value
            task["current_owner"] = self._resolve_owner(task, new_status)
            task["updated_at"] = now
            self._append_history(
                task,
                f"status_change:{current_status.value}->{new_status.value}",
                actor,
                {"from": current_status.value, "to": new_status.value, "owner": task["current_owner"]},
            )
            return tasks

        self.store.update([], mutate)
        self.event_log.append(
            "task",
            "status_changed",
            actor,
            task_id,
            {"from": current_status.value, "to": new_status.value, "current_owner": task["current_owner"]},
        )
        return task

    def intervene_task(self, task_id: str, action: str, actor: str = "system", reason: str = "") -> Dict[str, Any]:
        action_map = {
            "pause": TaskStatus.BLOCKED,
            "resume": TaskStatus.EXECUTING,
            "send_back": TaskStatus.PLANNED,
        }
        if action not in action_map:
            raise ValueError(f"不支持的干预动作: {action}")
        allowed_actions = {
            "pause": {TaskStatus.EXECUTING},
            "resume": {TaskStatus.BLOCKED},
            "send_back": {
                TaskStatus.REVIEWING,
                TaskStatus.APPROVED,
                TaskStatus.DISPATCHED,
                TaskStatus.EXECUTING,
                TaskStatus.REVIEW,
                TaskStatus.BLOCKED,
                TaskStatus.REJECTED,
            },
        }
        target_status = action_map[action]
        task: Dict[str, Any] = {}
        current_status: Optional[TaskStatus] = None

        def mutate(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal task, current_status
            task = self._find_task(tasks, task_id)
            current_status = TaskStatus(task["status"])
            if current_status not in allowed_actions[action]:
                raise ValueError(f"当前状态不允许执行干预: {current_status.value} -> {action}")

            task["status"] = target_status.value
            task["current_owner"] = self._resolve_owner(task, target_status)
            task["updated_at"] = utc_now_iso()
            self._append_history(
                task,
                f"intervention:{action}",
                actor,
                {
                    "from": current_status.value,
                    "to": target_status.value,
                    "reason": reason,
                    "owner": task["current_owner"],
                },
            )
            return tasks

        self.store.update([], mutate)
        self.event_log.append(
            "task",
            f"intervention:{action}",
            actor,
            task_id,
            {"from": current_status.value, "to": target_status.value, "reason": reason, "current_owner": task["current_owner"]},
        )
        return task

    def _resolve_owner(self, task: Dict[str, Any], status: TaskStatus) -> str:
        if status == TaskStatus.EXECUTING:
            return str(task.get("execution_owner") or TASK_TYPE_OWNER.get(task["type"], "execution"))
        return TASK_STAGE_OWNER[status.value]

    def get_stats(self) -> Dict[str, Any]:
        tasks = self._load()
        by_status: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        by_priority: Dict[str, int] = {}
        for task in tasks:
            by_status[task["status"]] = by_status.get(task["status"], 0) + 1
            by_type[task["type"]] = by_type.get(task["type"], 0) + 1
            by_priority[task["priority"]] = by_priority.get(task["priority"], 0) + 1
        return {
            "total_tasks": len(tasks),
            "by_status": by_status,
            "by_type": by_type,
            "by_priority": by_priority,
        }

    def all_tasks(self) -> List[Dict[str, Any]]:
        return self.list_tasks(limit=100000)


def _write_compliance_stub(task_id: str, task_service: TaskService) -> None:
    """项目结案法务报告（委托 project_close）。"""
    try:
        from project_close import write_compliance_report

        write_compliance_report(task_id, task_service)
    except Exception:
        pass


class WorkflowEngine:
    """统一编排入口，供 API 和 CLI 复用。"""

    def __init__(
        self,
        task_service: Optional[TaskService] = None,
        runtime_orchestrator: Optional[Any] = None,
        auto_runtime: bool = True,
        flow_engine: Optional[Any] = None,
    ):
        self.task_service = task_service or TaskService()
        self._flow_engine = flow_engine
        self._runtime_orchestrator = runtime_orchestrator
        if runtime_orchestrator is None and auto_runtime:
            try:
                from runtime_bridge import RuntimeOrchestrator

                self._runtime_orchestrator = RuntimeOrchestrator(self)
            except ImportError:
                self._runtime_orchestrator = None
        if self._flow_engine is None:
            try:
                from flow_engine import FlowEngine

                self._flow_engine = FlowEngine(self.task_service)
            except ImportError:
                self._flow_engine = None

    @property
    def flow_engine(self) -> Optional[Any]:
        return self._flow_engine

    def _notify_status_enter(
        self,
        task: Dict[str, Any],
        old_status: TaskStatus,
        new_status: TaskStatus,
    ) -> None:
        if self._runtime_orchestrator:
            self._runtime_orchestrator.on_status_enter(task, old_status, new_status)
        if new_status == TaskStatus.COMPLETED:
            try:
                from cost_report import emit_budget_alert_if_needed, write_cost_report_artifact
                from runtime.model_router import ModelRouter

                router = ModelRouter()
                report = write_cost_report_artifact(
                    task["task_id"],
                    router=router,
                    task_service=self.task_service,
                )
                emit_budget_alert_if_needed(
                    task["task_id"], report, self.task_service.event_log, self.task_service
                )
                try:
                    from skill_evolution import create_proposal_from_task
                    create_proposal_from_task(task)
                except Exception:
                    pass
                try:
                    _write_compliance_stub(task["task_id"], self.task_service)
                except Exception:
                    pass
                try:
                    from hr_scaling import release_dynamic_agents
                    release_dynamic_agents(task["task_id"], self.task_service)
                except Exception:
                    pass
            except Exception:
                pass

    def available_transitions(self, task_id: str) -> List[str]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        current = TaskStatus(task["status"])
        return [item.value for item in VALID_TASK_TRANSITIONS[current]]

    def transition(self, task_id: str, new_status: TaskStatus, actor: str) -> Dict[str, Any]:
        existing = self.task_service.get_task(task_id)
        if not existing:
            raise ValueError(f"任务不存在: {task_id}")
        old_status = TaskStatus(existing["status"])
        task = self.task_service.update_task_status(task_id, new_status, actor)
        self._notify_status_enter(task, old_status, new_status)
        return task

    def routing_snapshot(self, task_id: str) -> Dict[str, Any]:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        return {
            "task_id": task_id,
            "status": task["status"],
            "current_owner": task.get("current_owner"),
            "execution_owner": task.get("execution_owner"),
            "artifacts_count": len(task.get("artifacts", [])),
            "runtime": task.get("runtime", {}),
            "next_statuses": self.available_transitions(task_id),
        }

    def intervene(self, task_id: str, action: str, actor: str, reason: str = "") -> Dict[str, Any]:
        existing = self.task_service.get_task(task_id)
        if not existing:
            raise ValueError(f"任务不存在: {task_id}")
        if action == "override_gate":
            from flow_engine import FlowEngine

            return FlowEngine(self.task_service).override_gate(task_id, self, actor, reason)
        old_status = TaskStatus(existing["status"])
        task = self.task_service.intervene_task(task_id, action, actor, reason)
        self._notify_status_enter(task, old_status, TaskStatus(task["status"]))
        return task

    def timeline(self, task_id: str) -> List[Dict[str, Any]]:
        return self.task_service.get_task_timeline(task_id)


class ExecutionService:
    """执行层协议封装，统一执行动作入口。"""

    def __init__(self, workflow: Optional[WorkflowEngine] = None):
        self.workflow = workflow or WorkflowEngine()

    def start(self, task_id: str, actor: str) -> Dict[str, Any]:
        task = self.workflow.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        current = TaskStatus(task["status"])
        if current == TaskStatus.DISPATCHED:
            return self.workflow.transition(task_id, TaskStatus.EXECUTING, actor)
        if current == TaskStatus.BLOCKED:
            return self.workflow.intervene(task_id, "resume", actor, "execution_service_resume")
        raise ValueError(f"当前状态不允许开始执行: {current.value}")

    def complete(self, task_id: str, actor: str) -> Dict[str, Any]:
        task = self.workflow.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        current = TaskStatus(task["status"])
        if current != TaskStatus.EXECUTING:
            raise ValueError(f"当前状态不允许提交完成: {current.value}")
        return self.workflow.transition(task_id, TaskStatus.REVIEW, actor)

    def block(self, task_id: str, actor: str, reason: str) -> Dict[str, Any]:
        return self.workflow.intervene(task_id, "pause", actor, reason)


class AgentMonitorService:
    """基于任务历史推导 Agent 健康状态。"""

    def __init__(
        self,
        task_service: Optional[TaskService] = None,
        agent_service: Optional["AgentCatalogService"] = None,
    ):
        self.task_service = task_service or TaskService()
        self.agent_service = agent_service or AgentCatalogService(self.task_service.data_dir)

    def list_health(self) -> List[Dict[str, Any]]:
        tasks = self.task_service.all_tasks()
        agents = self.agent_service.list_agents()
        health_items: List[Dict[str, Any]] = []
        for agent in agents:
            agent_id = agent["id"]
            owned_tasks = [
                task
                for task in tasks
                if task.get("current_owner") == agent_id or task.get("execution_owner") == agent_id
            ]
            blocked_tasks = [task for task in owned_tasks if task["status"] == TaskStatus.BLOCKED.value]
            executing_tasks = [task for task in owned_tasks if task["status"] == TaskStatus.EXECUTING.value]
            completed_tasks = [task for task in owned_tasks if task["status"] == TaskStatus.COMPLETED.value]
            last_active_at = self._find_last_active_at(tasks, agent_id)
            status = "idle"
            if blocked_tasks:
                status = "blocked"
            elif executing_tasks:
                status = "busy"
            elif owned_tasks:
                status = "active"

            health_items.append(
                {
                    **agent,
                    "health_status": status,
                    "owned_task_count": len(owned_tasks),
                    "executing_task_count": len(executing_tasks),
                    "blocked_task_count": len(blocked_tasks),
                    "completed_task_count": len(completed_tasks),
                    "last_active_at": last_active_at,
                }
            )
        return health_items

    def _find_last_active_at(self, tasks: List[Dict[str, Any]], agent_id: str) -> Optional[str]:
        timestamps: List[str] = []
        for task in tasks:
            for event in task.get("history", []):
                actor = event.get("actor")
                detail = event.get("detail", {})
                if actor == agent_id or detail.get("owner") == agent_id:
                    timestamps.append(event["timestamp"])
        return max(timestamps) if timestamps else None

    def get_department_health(self) -> Dict[str, Any]:
        """聚合各部门所有 Agent 的健康状态。供 /api/departments 端点调用。"""
        from hr_scaling import load_or_bootstrap_roster, merge_runtime_dynamic_agents

        roster = load_or_bootstrap_roster(self.task_service.data_dir, AGENT_ROLES)
        tasks = self.task_service.all_tasks()
        merge_runtime_dynamic_agents(roster, tasks)
        depts: Dict[str, Any] = roster.get("departments", {})

        for dept_id, dept_data in depts.items():
            all_agents: List[Dict[str, Any]] = []
            if dept_data.get("head"):
                all_agents.append(dept_data["head"])
            all_agents.extend(dept_data.get("default_roles", []))
            all_agents.extend(dept_data.get("dynamic_agents", []))

            active_count = busy_count = blocked_count = 0
            for agent in all_agents:
                aid = agent.get("agent_id", "")
                if agent.get("is_dynamic") and agent.get("task_id"):
                    owned = [t for t in tasks if t.get("task_id") == agent["task_id"]]
                else:
                    owned = [
                        t for t in tasks
                        if t.get("current_owner") == aid or t.get("execution_owner") == aid
                    ]
                blocked = [t for t in owned if t.get("status") == TaskStatus.BLOCKED.value]
                executing = [t for t in owned if t.get("status") == TaskStatus.EXECUTING.value]
                if blocked:
                    health = "blocked"
                    blocked_count += 1
                elif executing:
                    health = "busy"
                    busy_count += 1
                elif owned:
                    health = "active"
                    active_count += 1
                else:
                    health = "idle"
                agent["health_status"] = health
                agent["owned_task_count"] = len(owned)
                agent["executing_task_count"] = len(executing)
                agent["blocked_task_count"] = len(blocked)
                agent["completed_task_count"] = len(
                    [t for t in owned if t.get("status") == TaskStatus.COMPLETED.value]
                )
            dept_data["stats"] = {
                "total_agents": len(all_agents),
                "active_agents": active_count,
                "busy_agents": busy_count,
                "blocked_agents": blocked_count,
                "runtime_dynamic_agents": dept_data.get("runtime_dynamic_count", 0),
            }
        return roster




class BoardRoom:
    """董事会会议室服务。"""

    PASS_THRESHOLD = 0.5
    MEMBERS = BOARD_MEMBERS

    def __init__(self, data_dir: Optional[Path | str] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.store = JsonStore(self.data_dir / "proposals.json")
        self.event_log = EventLogService(self.data_dir)

    def _load(self) -> List[Dict[str, Any]]:
        return self.store.read([])

    def _save(self, proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.store.write(proposals)

    def _generate_proposal_id(self, proposals: Iterable[Dict[str, Any]]) -> str:
        stamp = datetime.now().strftime("%Y%m%d")
        count = sum(1 for item in proposals if str(item.get("id", "")).startswith(f"PROP-{stamp}-")) + 1
        return f"PROP-{stamp}-{count:03d}"

    def create_proposal(
        self,
        title: str,
        content: str,
        proposer: str,
        decision_type: DecisionType = DecisionType.STRATEGIC,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        proposal_dict: Dict[str, Any] = {}

        def mutate(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal proposal_dict
            proposal = Proposal(
                id=self._generate_proposal_id(proposals),
                title=title,
                content=content,
                proposer=proposer,
                decision_type=decision_type.value,
                created_at=utc_now_iso(),
            )
            proposal_dict = asdict(proposal)
            if task_id:
                proposal_dict["task_id"] = task_id
            proposals.append(proposal_dict)
            return proposals

        self.store.update([], mutate)
        self.event_log.append(
            "proposal",
            "created",
            proposer,
            proposal_dict["id"],
            {"decision_type": decision_type.value, "title": title, "task_id": task_id},
        )
        return proposal_dict

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self._load() if item["id"] == proposal_id), None)

    def list_proposals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        proposals = self._load()
        if status:
            proposals = [item for item in proposals if item["status"] == status]
        proposals.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return proposals

    def add_discussion(self, proposal_id: str, member_id: str, opinion: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        def mutate(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal result
            proposal = next((item for item in proposals if item["id"] == proposal_id), None)
            if not proposal:
                result = {"error": f"提案不存在: {proposal_id}"}
                return proposals
            if proposal["status"] not in {"pending", "discussing"}:
                result = {"error": "提案当前状态不允许讨论"}
                return proposals
            member = self.MEMBERS.get(member_id, BoardMember(member_id, member_id, "unknown"))
            proposal["status"] = "discussing"
            proposal["discussion"].append(
                {
                    "member_id": member_id,
                    "member_name": member.name,
                    "opinion": opinion,
                    "timestamp": utc_now_iso(),
                }
            )
            result = proposal
            return proposals

        self.store.update([], mutate)
        if "error" in result:
            return result
        self.event_log.append("proposal", "discussed", member_id, proposal_id, {"opinion": opinion})
        return result

    def cast_vote(self, proposal_id: str, member_id: str, vote: VoteResult, reason: str = "") -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        def mutate(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal result
            proposal = next((item for item in proposals if item["id"] == proposal_id), None)
            if not proposal:
                result = {"error": f"提案不存在: {proposal_id}"}
                return proposals
            if proposal["status"] not in {"discussing", "voting", "pending"}:
                result = {"error": "提案当前状态不允许投票"}
                return proposals
            if any(existing["member_id"] == member_id for existing in proposal["votes"]):
                result = {"error": "该成员已经投票"}
                return proposals
            member = self.MEMBERS.get(member_id, BoardMember(member_id, member_id, "unknown"))
            proposal["status"] = "voting"
            proposal["votes"].append(
                {
                    "member_id": member_id,
                    "member_name": member.name,
                    "vote": vote.value,
                    "weight": member.vote_weight,
                    "reason": reason,
                    "timestamp": utc_now_iso(),
                }
            )
            result = proposal
            return proposals

        self.store.update([], mutate)
        if "error" in result:
            return result
        self.event_log.append("proposal", "voted", member_id, proposal_id, {"vote": vote.value, "reason": reason})
        return result

    def tally_votes(self, proposal_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        def mutate(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal result
            proposal = next((item for item in proposals if item["id"] == proposal_id), None)
            if not proposal:
                result = {"error": f"提案不存在: {proposal_id}"}
                return proposals

            if proposal["decision_type"] == DecisionType.EMERGENCY.value:
                proposal["status"] = "approved"
                proposal["result"] = "\u7d27\u6025\u51b3\u7b56\uff0c\u8463\u4e8b\u957f\u76f4\u63a5\u4e0b\u4ee4\u901a\u8fc7"
                result = {"proposal_id": proposal_id, "result": "approved", "message": proposal["result"], "votes": proposal["votes"]}
                return proposals

            total_weight = sum(float(vote["weight"]) for vote in proposal["votes"])
            agree_weight = sum(float(vote["weight"]) for vote in proposal["votes"] if vote["vote"] == VoteResult.AGREE.value)
            if total_weight <= 0:
                result = {"error": "无人投票"}
                return proposals
            approval_rate = agree_weight / total_weight
            passed = approval_rate > self.PASS_THRESHOLD
            proposal["status"] = "approved" if passed else "rejected"
            proposal["result"] = (
                f"\u6295\u7968\u901a\u8fc7\uff0c\u8d5e\u6210\u7387 {approval_rate:.1%}"
                if passed
                else f"\u6295\u7968\u672a\u901a\u8fc7\uff0c\u8d5e\u6210\u7387 {approval_rate:.1%}\uff0c\u9700\u5927\u4e8e {self.PASS_THRESHOLD:.0%}"
            )
            result = {
                "proposal_id": proposal_id,
                "result": proposal["status"],
                "approval_rate": approval_rate,
                "threshold": self.PASS_THRESHOLD,
                "total_weight": total_weight,
                "agree_weight": agree_weight,
                "message": proposal["result"],
                "votes": proposal["votes"],
            }
            return proposals

        self.store.update([], mutate)
        if "error" in result:
            return result
        if result["result"] == "approved" and "approval_rate" not in result:
            self.event_log.append("proposal", "approved", "chairman", proposal_id, {"decision_type": DecisionType.EMERGENCY.value})
            return result
        self.event_log.append(
            "proposal",
            result["result"],
            "board_room",
            proposal_id,
            {
                "approval_rate": result["approval_rate"],
                "agree_weight": result["agree_weight"],
                "total_weight": result["total_weight"],
            },
        )
        return result

    def direct_order(
        self,
        proposal_id: str,
        order: str,
        task_id: Optional[str] = None,
        step_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        def mutate(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal result
            proposal = next((item for item in proposals if item["id"] == proposal_id), None)
            if not proposal:
                result = {"error": f"提案不存在: {proposal_id}"}
                return proposals
            proposal["decision_type"] = DecisionType.EMERGENCY.value
            proposal["status"] = "approved"
            proposal["result"] = f"\u8463\u4e8b\u957f\u76f4\u63a5\u4e0b\u4ee4\uff1a{order}"
            if task_id:
                proposal["task_id"] = task_id
            if step_ids:
                proposal["skip_step_ids"] = step_ids
            result = {
                "proposal_id": proposal_id,
                "result": "approved",
                "message": proposal["result"],
                "task_id": proposal.get("task_id"),
            }
            return proposals

        self.store.update([], mutate)
        if "error" in result:
            return result
        self.event_log.append(
            "proposal",
            "direct_order",
            "chairman",
            proposal_id,
            {"order": order, "task_id": task_id, "step_ids": step_ids},
        )
        return result

    def get_summary(self) -> Dict[str, Any]:
        proposals = self._load()
        by_status: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for proposal in proposals:
            by_status[proposal["status"]] = by_status.get(proposal["status"], 0) + 1
            by_type[proposal["decision_type"]] = by_type.get(proposal["decision_type"], 0) + 1
        return {"total_proposals": len(proposals), "by_status": by_status, "by_type": by_type}


class AgentCatalogService:
    """Agent 配置同步服务。"""

    def __init__(self, data_dir: Optional[Path | str] = None, agents_dir: Optional[Path | str] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.agents_dir = Path(agents_dir) if agents_dir else AGENTS_DIR
        self.store = JsonStore(self.data_dir / "agent_config.json")

    def parse_soul_md(self, agent_dir: Path) -> Dict[str, Any]:
        soul_file = agent_dir / "SOUL.md"
        if not soul_file.exists():
            return {"has_soul": False, "line_count": 0, "sections": []}
        content = soul_file.read_text(encoding="utf-8")
        sections = [line[3:].strip() for line in content.splitlines() if line.startswith("## ")]
        return {"has_soul": True, "line_count": len(content.splitlines()), "sections": sections}

    def sync_all_agents(self) -> Dict[str, Any]:
        config = {"version": "2.0", "synced_at": utc_now_iso(), "agents": {}}
        for role_id, role_info in AGENT_ROLES.items():
            agent_dir = self.agents_dir / role_id
            config["agents"][role_id] = {
                **role_info,
                "id": role_id,
                "directory": str(agent_dir.relative_to(PROJECT_ROOT)) if agent_dir.exists() else f"agents/{role_id}",
                **self.parse_soul_md(agent_dir),
            }
        return self.store.write(config)

    def list_agents(self, layer: Optional[str] = None) -> List[Dict[str, Any]]:
        config = self.store.read(None) or self.sync_all_agents()
        agents = list(config["agents"].values())
        if layer:
            agents = [agent for agent in agents if agent.get("layer") == layer]
        return agents

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        config = self.store.read(None) or self.sync_all_agents()
        return config["agents"].get(agent_id)


class SkillCatalogService:
    """Skill 管理服务。"""

    def __init__(self, data_dir: Optional[Path | str] = None, skills_dir: Optional[Path | str] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self.store = JsonStore(self.data_dir / "skills.json")

    def _load(self) -> Dict[str, Any]:
        return self.store.read({"skills": {}})

    def _save(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return self.store.write(config)

    def add_local_skill(
        self, skill_id: str, name: str, description: str, agents: List[str], skill_content: str
    ) -> Dict[str, Any]:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = self.skills_dir / f"{skill_id}.md"
        skill_file.write_text(skill_content, encoding="utf-8")
        config = self._load()
        now = utc_now_iso()
        config["skills"][skill_id] = {
            "id": skill_id,
            "name": name,
            "description": description,
            "type": "local",
            "agents": agents,
            "path": str(skill_file.relative_to(PROJECT_ROOT)),
            "created_at": config["skills"].get(skill_id, {}).get("created_at", now),
            "updated_at": now,
        }
        self._save(config)
        return config["skills"][skill_id]

    def add_remote_skill(self, skill_id: str, name: str, description: str, agents: List[str], url: str) -> Dict[str, Any]:
        config = self._load()
        now = utc_now_iso()
        config["skills"][skill_id] = {
            "id": skill_id,
            "name": name,
            "description": description,
            "type": "remote",
            "agents": agents,
            "url": url,
            "created_at": config["skills"].get(skill_id, {}).get("created_at", now),
            "updated_at": now,
        }
        self._save(config)
        return config["skills"][skill_id]

    def update_skill(self, skill_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        config = self._load()
        skill = config["skills"].get(skill_id)
        if not skill:
            return None
        skill.update(kwargs)
        skill["updated_at"] = utc_now_iso()
        self._save(config)
        return skill

    def remove_skill(self, skill_id: str) -> bool:
        config = self._load()
        skill = config["skills"].get(skill_id)
        if not skill:
            return False
        if skill.get("type") == "local" and skill.get("path"):
            skill_file = PROJECT_ROOT / str(skill["path"])
            if skill_file.exists():
                skill_file.unlink()
        del config["skills"][skill_id]
        self._save(config)
        return True

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return self._load()["skills"].get(skill_id)

    def list_skills(self, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        skills = list(self._load()["skills"].values())
        if agent:
            skills = [skill for skill in skills if agent in skill.get("agents", [])]
        return skills


def build_sample_skills() -> List[Dict[str, Any]]:
    """返回内置示例 Skill 清单。"""
    now = utc_now_iso()
    return [
        {
            "id": "code_review",
            "name": "\u4ee3\u7801\u5ba1\u67e5",
            "description": "\u5bf9\u4ee3\u7801\u8fdb\u884c\u8d28\u91cf\u3001\u53ef\u7ef4\u62a4\u6027\u548c\u98ce\u9669\u5ba1\u67e5",
            "type": "local",
            "agents": ["rd_center", "risk_center"],
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "data_analysis",
            "name": "\u6570\u636e\u5206\u6790",
            "description": "\u5bf9\u4e1a\u52a1\u6570\u636e\u8fdb\u884c\u6d1e\u5bdf\u5206\u6790\u5e76\u751f\u6210\u7ed3\u8bba",
            "type": "local",
            "agents": ["data_center", "operation_center"],
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "market_research",
            "name": "\u5e02\u573a\u8c03\u7814",
            "description": "\u5bf9\u7ade\u54c1\u548c\u5e02\u573a\u8d8b\u52bf\u505a\u5feb\u901f\u8c03\u7814",
            "type": "local",
            "agents": ["marketing_center", "product_center"],
            "created_at": now,
            "updated_at": now,
        },
    ]



