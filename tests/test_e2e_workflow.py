#!/usr/bin/env python3
"""
CorpPilot 端到端验证，覆盖任务流、编排路由与董事会流程。
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from core import AgentCatalogService, AgentMonitorService, BoardRoom, DecisionType, EventLogService, ExecutionService, TaskPriority, TaskService, TaskStatus, TaskType, VoteResult, WorkflowEngine


class CorpPilotWorkflowTest(unittest.TestCase):
    """验证统一编排骨架。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "runtime-test-data"
        if cls.temp_dir.exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        tasks_file = self.temp_dir / "tasks.json"
        proposals_file = self.temp_dir / "proposals.json"
        if tasks_file.exists():
            tasks_file.unlink()
        if proposals_file.exists():
            proposals_file.unlink()
        self.task_service = TaskService(self.temp_dir)
        self.workflow = WorkflowEngine(self.task_service)
        self.board_room = BoardRoom(self.temp_dir)
        self.agent_monitor = AgentMonitorService(self.task_service, AgentCatalogService(self.temp_dir))
        self.event_log = EventLogService(self.temp_dir)
        self.execution_service = ExecutionService(self.workflow)

    def test_task_creation_and_routing(self) -> None:
        task = self.task_service.create_task(
            title="娴嬭瘯浠诲姟",
            task_type=TaskType.RD,
            priority=TaskPriority.P1,
            requester="ceo",
            description="楠岃瘉鍒涘缓閫昏緫",
        )
        self.assertEqual(task["status"], TaskStatus.PENDING.value)
        self.assertEqual(task["current_owner"], "president_office")
        self.assertEqual(task["execution_owner"], "rd_center")

        snapshot = self.workflow.routing_snapshot(task["task_id"])
        self.assertEqual(snapshot["next_statuses"], [TaskStatus.CLASSIFIED.value])

    def test_full_task_workflow(self) -> None:
        task = self.task_service.create_task("\u72b6\u6001\u6d41\u8f6c", TaskType.PD, TaskPriority.P2, "ceo")
        transitions = [
            TaskStatus.CLASSIFIED,
            TaskStatus.PLANNED,
            TaskStatus.REVIEWING,
            TaskStatus.APPROVED,
            TaskStatus.DISPATCHED,
            TaskStatus.EXECUTING,
            TaskStatus.REVIEW,
            TaskStatus.COMPLETED,
        ]
        for status in transitions:
            task = self.workflow.transition(task["task_id"], status, actor=f"actor:{status.value}")
        self.assertEqual(task["status"], TaskStatus.COMPLETED.value)
        self.assertEqual(task["current_owner"], "ceo")
        self.assertGreaterEqual(len(task["history"]), 9)

    def test_rejected_flow(self) -> None:
        task = self.task_service.create_task("\u9a73\u56de\u6d41\u8f6c", TaskType.LG, TaskPriority.P1, "ceo")
        for status in [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING]:
            task = self.workflow.transition(task["task_id"], status, actor="test")
        rejected = self.workflow.transition(task["task_id"], TaskStatus.REJECTED, actor="risk_center")
        self.assertEqual(rejected["status"], TaskStatus.REJECTED.value)
        planned_again = self.workflow.transition(task["task_id"], TaskStatus.PLANNED, actor="strategy")
        self.assertEqual(planned_again["status"], TaskStatus.PLANNED.value)

    def test_invalid_transition(self) -> None:
        task = self.task_service.create_task("\u975e\u6cd5\u6d41\u8f6c", TaskType.DA, TaskPriority.P3, "ceo")
        with self.assertRaises(ValueError):
            self.workflow.transition(task["task_id"], TaskStatus.COMPLETED, actor="system")

    def test_board_room_voting(self) -> None:
        proposal = self.board_room.create_proposal("\u9884\u7b97\u8c03\u6574", "\u8ffd\u52a0\u9884\u7b97 50 \u4e07", "ceo", DecisionType.STRATEGIC)
        self.board_room.add_discussion(proposal["id"], "ceo", "\u652f\u6301\u63a8\u8fdb")
        self.board_room.cast_vote(proposal["id"], "chairman", VoteResult.AGREE, "\u7b26\u5408\u6218\u7565\u65b9\u5411")
        self.board_room.cast_vote(proposal["id"], "ceo", VoteResult.AGREE, "\u4e1a\u52a1\u5fc5\u987b")
        result = self.board_room.tally_votes(proposal["id"])
        self.assertEqual(result["result"], "approved")
        self.assertGreater(result["approval_rate"], 0.5)

    def test_board_room_emergency_order(self) -> None:
        proposal = self.board_room.create_proposal("\u7d27\u6025\u6545\u969c\u5904\u7406", "\u7acb\u5373\u7194\u65ad\u53d7\u5f71\u54cd\u6a21\u5757", "chairman", DecisionType.EMERGENCY)
        result = self.board_room.direct_order(proposal["id"], "\u7acb\u5373\u6267\u884c\u6545\u969c\u9694\u79bb")
        self.assertEqual(result["result"], "approved")
        fetched = self.board_room.get_proposal(proposal["id"])
        self.assertEqual(fetched["decision_type"], DecisionType.EMERGENCY.value)

    def test_task_intervention_and_timeline(self) -> None:
        task = self.task_service.create_task("intervention", TaskType.RD, TaskPriority.P1, "ceo")
        for status in [
            TaskStatus.CLASSIFIED,
            TaskStatus.PLANNED,
            TaskStatus.REVIEWING,
            TaskStatus.APPROVED,
            TaskStatus.DISPATCHED,
            TaskStatus.EXECUTING,
        ]:
            task = self.workflow.transition(task["task_id"], status, actor="flow")

        paused = self.workflow.intervene(task["task_id"], "pause", "pmo", "manual pause")
        self.assertEqual(paused["status"], TaskStatus.BLOCKED.value)

        resumed = self.workflow.intervene(task["task_id"], "resume", "pmo", "continue")
        self.assertEqual(resumed["status"], TaskStatus.EXECUTING.value)

        sent_back = self.workflow.intervene(task["task_id"], "send_back", "risk_center", "need redesign")
        self.assertEqual(sent_back["status"], TaskStatus.PLANNED.value)

        timeline = self.workflow.timeline(task["task_id"])
        actions = [item["action"] for item in timeline]
        self.assertIn("intervention:pause", actions)
        self.assertIn("intervention:resume", actions)
        self.assertIn("intervention:send_back", actions)

    def test_agent_health_snapshot(self) -> None:
        task = self.task_service.create_task("health", TaskType.RD, TaskPriority.P1, "ceo")
        for status in [
            TaskStatus.CLASSIFIED,
            TaskStatus.PLANNED,
            TaskStatus.REVIEWING,
            TaskStatus.APPROVED,
            TaskStatus.DISPATCHED,
            TaskStatus.EXECUTING,
        ]:
            task = self.workflow.transition(task["task_id"], status, actor="flow")
        self.workflow.intervene(task["task_id"], "pause", "pmo", "dependency blocked")

        health_items = self.agent_monitor.list_health()
        rd_center = next(item for item in health_items if item["id"] == "rd_center")
        pmo = next(item for item in health_items if item["id"] == "pmo")
        self.assertGreaterEqual(rd_center["owned_task_count"], 1)
        self.assertEqual(rd_center["blocked_task_count"], 1)
        self.assertEqual(rd_center["health_status"], "blocked")
        self.assertEqual(pmo["health_status"], "blocked")

    def test_event_log_records_task_and_proposal_changes(self) -> None:
        task = self.task_service.create_task("events", TaskType.RD, TaskPriority.P1, "ceo")
        self.workflow.transition(task["task_id"], TaskStatus.CLASSIFIED, actor="president_office")
        proposal = self.board_room.create_proposal("event proposal", "desc", "ceo", DecisionType.STRATEGIC)
        self.board_room.add_discussion(proposal["id"], "ceo", "ok")

        task_events = self.event_log.list_events(category="task", subject_id=task["task_id"], limit=10)
        proposal_events = self.event_log.list_events(category="proposal", subject_id=proposal["id"], limit=10)

        self.assertTrue(any(item["action"] == "created" for item in task_events))
        self.assertTrue(any(item["action"] == "status_changed" for item in task_events))
        self.assertTrue(any(item["action"] == "created" for item in proposal_events))
        self.assertTrue(any(item["action"] == "discussed" for item in proposal_events))

    def test_execution_service_protocol(self) -> None:
        task = self.task_service.create_task("execute", TaskType.RD, TaskPriority.P1, "ceo")
        for status in [TaskStatus.CLASSIFIED, TaskStatus.PLANNED, TaskStatus.REVIEWING, TaskStatus.APPROVED]:
            task = self.workflow.transition(task["task_id"], status, actor="flow")
        task = self.workflow.transition(task["task_id"], TaskStatus.DISPATCHED, actor="pmo")
        started = self.execution_service.start(task["task_id"], "rd_center")
        self.assertEqual(started["status"], TaskStatus.EXECUTING.value)
        completed = self.execution_service.complete(task["task_id"], "rd_center")
        self.assertEqual(completed["status"], TaskStatus.REVIEW.value)

    def test_board_summary_and_event_ordering(self) -> None:
        first = self.board_room.create_proposal("proposal-one", "desc-1", "ceo", DecisionType.STRATEGIC)
        second = self.board_room.create_proposal("proposal-two", "desc-2", "chairman", DecisionType.EMERGENCY)
        self.board_room.add_discussion(first["id"], "ceo", "需要继续评估")
        self.board_room.direct_order(second["id"], "立即执行隔离")

        summary = self.board_room.get_summary()
        self.assertEqual(summary["total_proposals"], 2)
        self.assertEqual(summary["by_type"][DecisionType.STRATEGIC.value], 1)
        self.assertEqual(summary["by_type"][DecisionType.EMERGENCY.value], 1)
        self.assertEqual(summary["by_status"]["approved"], 1)
        self.assertEqual(summary["by_status"]["discussing"], 1)

        proposal_events = self.event_log.list_events(category="proposal", limit=10)
        self.assertGreaterEqual(len(proposal_events), 4)
        self.assertEqual(proposal_events[0]["subject_id"], second["id"])
        self.assertEqual(proposal_events[0]["action"], "direct_order")
        self.assertTrue(all(item["category"] == "proposal" for item in proposal_events))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CorpPilotWorkflowTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)






