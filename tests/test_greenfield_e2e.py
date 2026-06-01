#!/usr/bin/env python3
"""Greenfield Flow 端到端集成测试。"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import TaskPriority, TaskService, TaskType, WorkflowEngine
from demo_greenfield import advance_to_step, seed_greenfield_artifacts
from flow_engine import FlowEngine
from postcondition import check_postconditions
from visual_diff import check_visual_diff, compute_visual_diff


class GreenfieldE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "greenfield-e2e"
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)
        os.environ["CORPPILOT_AUTO_RUNTIME"] = "0"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        tf = self.temp_dir / "tasks.json"
        if tf.exists():
            tf.unlink()
        self.ts = TaskService(self.temp_dir)
        self.wf = WorkflowEngine(self.ts, auto_runtime=False)
        self.fe = FlowEngine(self.ts)

    def test_advance_to_qa_gate(self) -> None:
        task = self.ts.create_task(
            "gf-e2e",
            TaskType.RD,
            TaskPriority.P1,
            "ceo",
            flow_id="greenfield",
        )
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        updated = advance_to_step(self.wf, tid, "qa_gate")
        self.assertEqual(updated.get("flow_step_id"), "qa_gate")

    def test_qa_gate_postconditions_pass(self) -> None:
        task = self.ts.create_task(
            "gf-qa",
            TaskType.RD,
            TaskPriority.P1,
            "ceo",
            flow_id="greenfield",
        )
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        advance_to_step(self.wf, tid, "qa_gate")
        ok, msg = check_visual_diff(tid, threshold=0.7)
        self.assertTrue(ok, msg)
        check = check_postconditions(tid, ["tests_pass == true", "visual_diff >= 0.7"], None)
        self.assertTrue(check["passed"], check.get("errors"))

    def test_supervisor_pass_advances_flow(self) -> None:
        task = self.ts.create_task(
            "gf-sup",
            TaskType.RD,
            TaskPriority.P1,
            "ceo",
            flow_id="greenfield",
        )
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        advance_to_step(self.wf, tid, "qa_gate")
        after = self.fe.run_supervisor_step(tid, self.wf, actor="test", verdict="pass")
        self.assertNotEqual(after.get("flow_step_id"), "qa_gate")
        audit = PROJECT_ROOT / "artifacts" / tid / "delivery_audit.md"
        self.assertTrue(audit.exists())

    def test_visual_diff_text_mode(self) -> None:
        task = self.ts.create_task("gf-vis", TaskType.RD, TaskPriority.P2, "ceo", flow_id="greenfield")
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        result = compute_visual_diff(tid, use_screenshot=False)
        self.assertEqual(result.get("mode"), "text")
        self.assertGreaterEqual(result.get("score", 0), 0.7)

    def test_project_close_artifacts(self) -> None:
        from project_close import emit_project_close_artifacts, check_closeout_outputs

        task = self.ts.create_task("gf-close", TaskType.RD, TaskPriority.P1, "ceo", flow_id="greenfield")
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        advance_to_step(self.wf, tid, "project_close")
        self.fe.run_close_step(tid, self.wf, actor="test")
        check = check_closeout_outputs(tid)
        self.assertTrue(check["passed"], check.get("errors"))
        self.assertTrue((PROJECT_ROOT / "artifacts" / tid / "cost_report.json").exists())
        self.assertTrue((PROJECT_ROOT / "artifacts" / tid / "compliance_report.md").exists())
        self.assertTrue((PROJECT_ROOT / "artifacts" / tid / "finance_brief.md").exists())

    def test_full_close_and_complete(self) -> None:
        from core import TaskStatus

        task = self.ts.create_task("gf-full", TaskType.RD, TaskPriority.P1, "ceo", flow_id="greenfield")
        tid = task["task_id"]
        seed_greenfield_artifacts(tid)
        advance_to_step(self.wf, tid, "project_close")
        self.fe.run_close_step(tid, self.wf, actor="test")
        task = self.fe.approve_gate(tid, "founder", "ack")
        if task.get("flow_step_id") == "completed":
            for status in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
                try:
                    self.wf.transition(tid, status, "founder")
                except ValueError:
                    break
        final = self.ts.get_task(tid)
        self.assertEqual(final.get("status"), TaskStatus.COMPLETED.value)


if __name__ == "__main__":
    unittest.main()
