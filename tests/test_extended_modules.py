#!/usr/bin/env python3
"""新增模块单元测试。"""
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

from design_artifacts import validate_design_artifact
from flow_engine import FlowEngine, load_flow, normalize_steps
from hr_scaling import (
    expand_parallel_branches,
    register_dynamic_agents,
    release_dynamic_agents,
)
from runtime.execution_backends import get_backend, resolve_backend_name
from skill_evolution import (
    _archive_skill,
    approve_proposal,
    create_proposal_from_task,
    list_proposals,
    list_skill_versions,
    reject_proposal,
    rollback_skill,
)
from visual_diff import check_visual_diff, compute_visual_diff
from core import AgentMonitorService, SkillCatalogService, TaskPriority, TaskService, TaskStatus, TaskType, WorkflowEngine


class ExtendedModulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "extended-test"
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)
        os.environ["CORPPILOT_AUTO_RUNTIME"] = "0"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        shutil.rmtree(PROJECT_ROOT / "proposed_skills", ignore_errors=True)
        tid = "TASK-VIS-1"
        shutil.rmtree(PROJECT_ROOT / "artifacts" / tid, ignore_errors=True)
        tid = "TASK-VIS-TAG"
        shutil.rmtree(PROJECT_ROOT / "artifacts" / tid, ignore_errors=True)
        shutil.rmtree(PROJECT_ROOT / "skills" / ".history", ignore_errors=True)

    def test_design_validation(self) -> None:
        tid = "TASK-DESIGN-1"
        d = PROJECT_ROOT / "artifacts" / tid / "design"
        d.mkdir(parents=True, exist_ok=True)
        (d / "mock_a.html").write_text("<html></html>", encoding="utf-8")
        (d / "design_spec.md").write_text("组件与布局", encoding="utf-8")
        (d / "selected.option").write_text("mock_a.html", encoding="utf-8")
        self.assertTrue(validate_design_artifact(tid)["passed"])

    def test_dev_parallel_in_greenfield(self) -> None:
        flow = load_flow("greenfield")
        steps = normalize_steps(flow)
        par = next(s for s in steps if s.get("id") == "dev_parallel")
        self.assertEqual(par.get("type"), "parallel")
        self.assertEqual(len(par.get("parallel", [])), 2)

    def test_backend_resolve(self) -> None:
        step = {"id": "dev_loop", "executor": "claude_code"}
        self.assertEqual(resolve_backend_name(step), "claude_code")
        self.assertEqual(get_backend("claude_code").name, "claude_code")

    def test_skill_proposal_lifecycle(self) -> None:
        ts = TaskService(self.temp_dir)
        sk = SkillCatalogService(self.temp_dir)
        task = ts.create_task("skill-test", TaskType.RD, TaskPriority.P2, "ceo")
        task["artifacts"] = [{"path": "artifacts/x/out.md"}]
        prop = create_proposal_from_task(task)
        self.assertEqual(prop["status"], "pending")
        approved = approve_proposal(prop["id"], sk)
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(any(p["status"] == "approved" for p in list_proposals()))

    def test_skill_rollback(self) -> None:
        sk = SkillCatalogService(self.temp_dir)
        sid = "rollback_test"
        sk.add_local_skill(sid, "v1", "first", ["rd_center"], "# version 1\n")
        _archive_skill(sid, sk)
        sk.add_local_skill(sid, "v2", "second", ["rd_center"], "# version 2\n")
        versions_before = list_skill_versions(sid)
        self.assertGreaterEqual(len(versions_before), 1)
        rolled = rollback_skill(sid, sk)
        self.assertIn("rollback_test", rolled.get("id", sid))
        content = (PROJECT_ROOT / rolled["path"]).read_text(encoding="utf-8")
        self.assertIn("version 1", content)

    def test_visual_diff(self) -> None:
        tid = "TASK-VIS-1"
        d = PROJECT_ROOT / "artifacts" / tid / "design"
        d.mkdir(parents=True, exist_ok=True)
        (d / "mock_a.html").write_text("<html><body><h1>Hello CorpPilot</h1></body></html>", encoding="utf-8")
        (d / "selected.option").write_text("mock_a.html", encoding="utf-8")
        impl = PROJECT_ROOT / "artifacts" / tid / "index.html"
        impl.write_text("<html><body><h1>Hello CorpPilot</h1><p>extra</p></body></html>", encoding="utf-8")
        result = compute_visual_diff(tid)
        self.assertGreater(result["score"], 0.5)
        ok, msg = check_visual_diff(tid, threshold=0.5)
        self.assertTrue(ok, msg)

    def test_claude_code_dry_run(self) -> None:
        import os
        from runtime.execution_backends import ClaudeCodeBackend

        os.environ["CORPPILOT_CLAUDE_DRY_RUN"] = "1"
        try:
            tid = "TASK-CC-DRY"
            backend = ClaudeCodeBackend()
            result = backend.run_sync("implement feature X", task_id=tid)
            self.assertTrue(result.success)
            self.assertIn("dry-run", result.summary.lower())
            log = PROJECT_ROOT / "artifacts" / tid / "claude_code_dry_run.txt"
            self.assertTrue(log.exists())
        finally:
            os.environ.pop("CORPPILOT_CLAUDE_DRY_RUN", None)

    def test_claude_code_unavailable(self) -> None:
        import os
        from unittest.mock import patch
        from runtime.execution_backends import ClaudeCodeBackend

        os.environ.pop("CORPPILOT_CLAUDE_DRY_RUN", None)
        with patch("runtime.execution_backends.claude_code_available", return_value=False):
            result = ClaudeCodeBackend().run_sync("test", "TASK-NO-CC")
            self.assertFalse(result.success)
            self.assertIn("不可用", result.summary)
        branches = [{"id": "backend", "role": "rd_center", "replicas": 3}]
        expanded = expand_parallel_branches(branches)
        self.assertEqual(len(expanded), 3)
        self.assertEqual(expanded[0]["spawn_agent_id"], "rd_center_001")
        self.assertEqual(expanded[2]["spawn_agent_id"], "rd_center_003")
        ts = TaskService(self.temp_dir)
        task = ts.create_task("hr-scale", TaskType.RD, TaskPriority.P2, "ceo")
        register_dynamic_agents(task["task_id"], expanded, ts)
        updated = ts.get_task(task["task_id"])
        dynamic = (updated.get("runtime") or {}).get("hr_dynamic_agents") or []
        self.assertEqual(len(dynamic), 3)

    def test_department_runtime_dynamic_merge(self) -> None:
        ts = TaskService(self.temp_dir)
        wf = WorkflowEngine(ts, auto_runtime=False)
        task = ts.create_task("parallel-ui", TaskType.RD, TaskPriority.P1, "ceo")
        for status in (
            TaskStatus.CLASSIFIED,
            TaskStatus.PLANNED,
            TaskStatus.REVIEWING,
            TaskStatus.APPROVED,
            TaskStatus.DISPATCHED,
            TaskStatus.EXECUTING,
        ):
            wf.transition(task["task_id"], status, "test")
        branches = expand_parallel_branches([
            {"id": "frontend_dev", "role": "product_center", "replicas": 1},
            {"id": "backend_dev", "role": "rd_center", "replicas": 2},
        ])
        register_dynamic_agents(task["task_id"], branches, ts)
        monitor = AgentMonitorService(ts)
        roster = monitor.get_department_health()
        rd = roster["departments"]["rd_center"]
        product = roster["departments"]["product_center"]
        self.assertEqual(rd["stats"]["runtime_dynamic_agents"], 2)
        self.assertEqual(product["stats"]["runtime_dynamic_agents"], 1)
        dyn_ids = {a["agent_id"] for a in rd["dynamic_agents"] if a.get("is_dynamic")}
        self.assertIn("rd_center_001", dyn_ids)
        for status in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
            wf.transition(task["task_id"], status, "test")
        release_dynamic_agents(task["task_id"], ts)
        roster2 = monitor.get_department_health()
        self.assertEqual(roster2["departments"]["rd_center"]["stats"]["runtime_dynamic_agents"], 0)

    def test_visual_diff_compare_images(self) -> None:
        try:
            from PIL import Image
            from visual_diff import compare_images
        except ImportError:
            self.skipTest("Pillow 未安装")
        tid = "TASK-IMG-CMP"
        out = PROJECT_ROOT / "artifacts" / tid / "visual_diff"
        out.mkdir(parents=True, exist_ok=True)
        a = out / "a.png"
        b = out / "b.png"
        Image.new("RGB", (64, 64), color=(255, 0, 0)).save(a)
        Image.new("RGB", (64, 64), color=(255, 0, 0)).save(b)
        self.assertGreaterEqual(compare_images(a, b), 0.99)
        tid = "TASK-VIS-TAG"
        d = PROJECT_ROOT / "artifacts" / tid / "design"
        d.mkdir(parents=True, exist_ok=True)
        (d / "mock_a.html").write_text("<html><body><h1>Title</h1><nav></nav></body></html>", encoding="utf-8")
        (d / "selected.option").write_text("mock_a.html", encoding="utf-8")
        impl = PROJECT_ROOT / "artifacts" / tid / "index.html"
        impl.write_text("<html><body><h1>Title</h1><nav></nav><footer></footer></body></html>", encoding="utf-8")
        result = compute_visual_diff(tid)
        self.assertIn("tag_score", result)
        self.assertGreaterEqual(result["tag_score"], 0.8)

    def test_checklist_postcondition(self) -> None:
        from checklist import ensure_default_checklist
        from postcondition import check_postconditions

        tid = "TASK-CHK-1"
        base = PROJECT_ROOT / "artifacts" / tid
        base.mkdir(parents=True, exist_ok=True)
        ensure_default_checklist(tid)
        (base / "index.html").write_text(
            "<html><body><header><h1>Title</h1></header><nav></nav><main></main></body></html>",
            encoding="utf-8",
        )
        ok = check_postconditions(tid, ["checklist_pass == true"], [])
        self.assertTrue(ok["passed"])


if __name__ == "__main__":
    unittest.main()
