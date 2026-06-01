#!/usr/bin/env python3
"""traffic_seed 与 bootstrap_skills 测试。"""
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

from bootstrap_skills import ensure_flow_skills
from cost_report import build_cost_report
from core import SkillCatalogService
from runtime.agent_loop import _load_skills
from traffic_seed import seed_task_traffic


class TrafficAndSkillsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "traffic-skills-test"
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        cls.log_path = cls.temp_dir / "traffic_logs.jsonl"
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_seed_traffic_for_cost_report(self) -> None:
        tid = "TASK-TRAFFIC-1"
        if self.log_path.exists():
            self.log_path.unlink()
        result = seed_task_traffic(tid, log_path=self.log_path)
        self.assertGreater(result["rows_written"], 0)
        report = build_cost_report(tid, log_path=self.log_path)
        self.assertGreater(report["total_tokens"], 0)
        self.assertGreater(report["total_cost_usd"], 0)

    def test_ensure_flow_skills(self) -> None:
        sk = SkillCatalogService(self.temp_dir)
        touched = ensure_flow_skills(sk)
        self.assertTrue(sk.get_skill("coding"))
        self.assertTrue(sk.get_skill("demo-generator"))
        self.assertTrue((PROJECT_ROOT / "skills" / "coding.md").exists())

    def test_load_skills_by_flow_step_ids(self) -> None:
        sk = SkillCatalogService(self.temp_dir)
        ensure_flow_skills(sk)
        text = _load_skills("rd_center", skill_ids=["coding"])
        self.assertIn("编码实现", text)
        self.assertIn("report_done", text)

    def test_skill_update_archives_content(self) -> None:
        from skill_evolution import _archive_skill

        skills_dir = self.temp_dir / "skills_md"
        sk = SkillCatalogService(self.temp_dir, skills_dir=skills_dir)
        sk.add_local_skill("edit-me", "Edit", "desc", ["rd_center"], "# v1\n")
        archived = _archive_skill("edit-me", sk)
        self.assertTrue(archived)
        updated = sk.add_local_skill("edit-me", "Edit v2", "new desc", ["rd_center"], "# v2\n")
        self.assertEqual(updated["name"], "Edit v2")
        self.assertEqual((skills_dir / "edit-me.md").read_text(encoding="utf-8"), "# v2\n")


if __name__ == "__main__":
    unittest.main()
