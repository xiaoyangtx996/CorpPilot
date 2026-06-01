#!/usr/bin/env python3
"""董事会下令与 Flow skip 测试。"""
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

from board_flow import apply_direct_order_to_task, parse_skip_steps_from_order
from core import BoardRoom, TaskPriority, TaskService, TaskType, WorkflowEngine
from flow_engine import FlowEngine


class BoardFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = PROJECT_ROOT / ".codex" / "board-flow-test"
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CORPPILOT_DATA_DIR"] = str(cls.temp_dir)
        os.environ["CORPPILOT_AUTO_RUNTIME"] = "0"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        tf = self.temp_dir / "tasks.json"
        pf = self.temp_dir / "proposals.json"
        if tf.exists():
            tf.unlink()
        if pf.exists():
            pf.unlink()
        self.ts = TaskService(self.temp_dir)
        self.workflow = WorkflowEngine(self.ts, auto_runtime=False)
        self.flow = FlowEngine(self.ts)
        self.board = BoardRoom(self.temp_dir)

    def test_parse_skip_from_order(self) -> None:
        skips = parse_skip_steps_from_order("紧急 hotfix 跳过 product_demo", "hotfix")
        self.assertIn("product_demo", skips)

    def test_direct_order_with_task_skip(self) -> None:
        task = self.ts.create_task("board-skip", TaskType.RD, TaskPriority.P0, "ceo", flow_id="hotfix")
        prop = self.board.create_proposal("紧急", "跳过审批", "chairman", task_id=task["task_id"])
        result = self.board.direct_order(
            prop["id"],
            "紧急：skip:product_demo,prd_generation",
            task_id=task["task_id"],
        )
        self.assertEqual(result["result"], "approved")
        applied = apply_direct_order_to_task(
            task["task_id"],
            "skip:product_demo",
            self.flow,
            step_ids=["product_demo"],
        )
        self.assertIn("product_demo", applied["skipped_steps"])


if __name__ == "__main__":
    unittest.main()
