#!/usr/bin/env python3
"""FlowEngine 与 postcondition 单元测试。"""
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
from flow_engine import FlowEngine, load_flow, list_flow_ids, normalize_steps
from postcondition import check_postconditions


class FlowEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "flow-test-data"
        if cls.temp_dir.exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)
        os.environ["CORPPILOT_AUTO_RUNTIME"] = "0"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        tasks_file = self.temp_dir / "tasks.json"
        if tasks_file.exists():
            tasks_file.unlink()
        self.task_service = TaskService(self.temp_dir)
        self.workflow = WorkflowEngine(self.task_service, auto_runtime=False)
        self.flow = FlowEngine(self.task_service)

    def test_list_and_load_hotfix(self) -> None:
        self.assertIn("hotfix", list_flow_ids())
        flow = load_flow("hotfix")
        steps = normalize_steps(flow)
        self.assertGreaterEqual(len(steps), 3)

    def test_create_task_with_hotfix_flow(self) -> None:
        task = self.task_service.create_task(
            "hotfix-bug",
            TaskType.RD,
            TaskPriority.P0,
            "ceo",
            flow_id="hotfix",
        )
        self.assertEqual(task.get("flow_id"), "hotfix")
        self.assertEqual(task.get("flow_step_id"), "triage")
        ctx = self.flow.get_flow_context(task)
        self.assertEqual(ctx["mode"], "flow")

    def test_hotfix_skips_product_demo_on_advance(self) -> None:
        task = self.task_service.create_task("skip-demo", TaskType.RD, TaskPriority.P1, "ceo", flow_id="hotfix")
        task = self.flow.advance(task["task_id"], actor="test", force=True)
        self.assertNotEqual(task.get("flow_step_id"), "product_demo")

    def test_gate_approve(self) -> None:
        task = self.task_service.create_task("gate", TaskType.PD, TaskPriority.P2, "ceo", flow_id="greenfield")
        self.assertEqual(task.get("flow_step_id"), "board_discussion")
        task = self.flow.approve_gate(task["task_id"], "founder", "ok")
        self.assertNotEqual(task.get("flow_step_id"), "board_discussion")

    def test_supervisor_step_detect(self) -> None:
        flow = load_flow("hotfix")
        qa = next(s for s in normalize_steps(flow) if s.get("id") == "qa_gate")
        self.assertEqual(qa.get("type"), "supervisor")
        self.assertTrue(self.flow.is_supervisor_step(qa))

    def test_summarize_flow(self) -> None:
        from flow_engine import summarize_flow

        summary = summarize_flow("greenfield")
        self.assertEqual(summary["id"], "greenfield")
        self.assertGreaterEqual(summary["step_count"], 5)
        ids = [s["id"] for s in summary["steps"]]
        self.assertIn("qa_gate", ids)

    def test_postcondition_file_check(self) -> None:
        task_id = "TASK-TEST-PC"
        art_dir = PROJECT_ROOT / "artifacts" / task_id
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "PRD.md").write_text("# PRD", encoding="utf-8")
        result = check_postconditions(task_id, [], ["PRD.md"])
        self.assertTrue(result["passed"])

    def test_steps_timeline(self) -> None:
        task = self.task_service.create_task("tl", TaskType.RD, TaskPriority.P2, "ceo", flow_id="greenfield")
        ctx = self.flow.get_flow_context(task)
        timeline = ctx.get("steps_timeline") or []
        self.assertGreaterEqual(len(timeline), 5)
        self.assertEqual(timeline[0]["status"], "current")
        self.assertEqual(timeline[0]["id"], "board_discussion")

    def test_parallel_merge_gate_preserved(self) -> None:
        flow = load_flow("greenfield")
        par = next(s for s in normalize_steps(flow) if s.get("id") == "dev_parallel")
        self.assertEqual(par.get("merge_gate"), ["tests_pass == true"])

    def test_tests_pass_marker(self) -> None:
        task_id = "TASK-MARKER"
        art_dir = PROJECT_ROOT / "artifacts" / task_id
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / ".tests_passed").write_text("ok", encoding="utf-8")
        result = check_postconditions(task_id, ["tests_pass == true"], [])
        self.assertTrue(result["passed"])

    def test_flow_export_import(self) -> None:
        from flow_io import export_flow, import_flow

        payload = export_flow("hotfix")
        self.assertEqual(payload["format"], "corppilot-flow")
        self.assertEqual(payload["flow"]["id"], "hotfix")

        custom = {
            "id": "test-import-flow",
            "name": "Import Test",
            "description": "unit test",
            "steps": [{"id": "only", "role": "rd_center", "gate_mode": "auto"}],
        }
        result = import_flow(custom, overwrite=False)
        self.assertEqual(result["flow_id"], "test-import-flow")
        self.assertTrue((PROJECT_ROOT / "flows" / "test-import-flow.json").exists())

        with self.assertRaises(ValueError):
            import_flow(custom, overwrite=False)

        result2 = import_flow(custom, overwrite=True)
        self.assertTrue(result2["overwritten"])

        (PROJECT_ROOT / "flows" / "test-import-flow.json").unlink(missing_ok=True)

    def test_flow_from_task_preserves_skips(self) -> None:
        from flow_io import flow_from_task, save_task_as_flow

        task = self.task_service.create_task("save-flow", TaskType.RD, TaskPriority.P1, "ceo", flow_id="hotfix")
        task["flow_state"] = {"skipped_steps": ["product_demo"]}
        flow = flow_from_task(task, new_id="hotfix-custom", flow_engine=self.flow)
        demo = next(s for s in flow["steps"] if s["id"] == "product_demo")
        self.assertEqual(demo.get("gate_mode"), "skip")

        result = save_task_as_flow(
            task,
            new_id="hotfix-from-task",
            flow_engine=self.flow,
            overwrite=True,
        )
        self.assertEqual(result["flow_id"], "hotfix-from-task")
        (PROJECT_ROOT / "flows" / "hotfix-from-task.json").unlink(missing_ok=True)

    def test_prd_coverage_postcondition(self) -> None:
        task_id = "TASK-PRD-COV"
        art_dir = PROJECT_ROOT / "artifacts" / task_id
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "PRD.md").write_text(
            "# PRD\n\n## 功能\n\n## 范围\n\n## 用户\n\n## 验收\n",
            encoding="utf-8",
        )
        ok = check_postconditions(task_id, ["prd_coverage >= 0.95"], [])
        self.assertTrue(ok["passed"])

        (art_dir / "PRD.md").write_text("# PRD\n\n## 功能\n", encoding="utf-8")
        bad = check_postconditions(task_id, ["prd_coverage >= 0.95"], [])
        self.assertFalse(bad["passed"])

    def test_rewind_to_dev_parallel_on_send_back(self) -> None:
        task = self.task_service.create_task("sb", TaskType.RD, TaskPriority.P1, "ceo", flow_id="greenfield")
        for _ in range(12):
            task = self.flow.advance(task["task_id"], actor="test", force=True)
            if task.get("flow_step_id") == "qa_gate":
                break
        self.assertEqual(task.get("flow_step_id"), "qa_gate")
        ctx = self.flow.get_flow_context(task)
        step = ctx.get("current_step") or {}
        check = {"passed": False, "errors": ["visual_diff low"]}
        self.flow.handle_step_failure(task["task_id"], self.workflow, step, check, "test")
        task = self.task_service.get_task(task["task_id"])
        self.assertEqual(task.get("flow_step_id"), "dev_parallel")

    def test_override_gate(self) -> None:
        task = self.task_service.create_task("og", TaskType.PD, TaskPriority.P2, "ceo", flow_id="greenfield")
        self.assertEqual(task.get("flow_step_id"), "board_discussion")
        task = self.flow.approve_gate(task["task_id"], "founder", "ok")
        self.assertNotEqual(task.get("flow_step_id"), "board_discussion")


if __name__ == "__main__":
    unittest.main()
