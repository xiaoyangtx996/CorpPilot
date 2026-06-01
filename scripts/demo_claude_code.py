#!/usr/bin/env python3
"""
Claude Code 后端联调脚本。

用法:
  python scripts/demo_claude_code.py              # dry-run（默认）
  python scripts/demo_claude_code.py --real       # 调用真实 CLI（需安装 claude）
  CORPPILOT_CLAUDE_DRY_RUN=1 python scripts/demo_claude_code.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import TaskPriority, TaskService, TaskType, WorkflowEngine
from runtime.execution_backends import ClaudeCodeBackend, claude_code_available


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code 后端联调")
    parser.add_argument("--real", action="store_true", help="调用真实 Claude Code CLI")
    parser.add_argument("--prompt", default="在 artifacts 目录创建一个 hello.txt 并写入 Hello CorpPilot")
    args = parser.parse_args()

    if args.real:
        os.environ.pop("CORPPILOT_CLAUDE_DRY_RUN", None)
    else:
        os.environ["CORPPILOT_CLAUDE_DRY_RUN"] = "1"

    os.environ.setdefault("CORPPILOT_AUTO_RUNTIME", "0")

    ts = TaskService()
    wf = WorkflowEngine(ts, auto_runtime=False)
    task = ts.create_task(
        "claude-code-smoke",
        TaskType.RD,
        TaskPriority.P2,
        "ceo",
        description="Claude Code 联调任务",
        flow_id="hotfix",
    )
    tid = task["task_id"]
    (PROJECT_ROOT / "artifacts" / tid).mkdir(parents=True, exist_ok=True)

    backend = ClaudeCodeBackend()
    available = claude_code_available()
    result = backend.run_sync(
        f"【任务 {tid}】{args.prompt}",
        task_id=tid,
        timeout=120,
    )

    out = {
        "task_id": tid,
        "backend": result.backend,
        "success": result.success,
        "summary_preview": result.summary[:500],
        "artifacts": result.artifacts,
        "claude_cli_available": available,
        "dry_run": not args.real,
        "hint": "设置 --real 且安装 claude CLI 可进行真实调用",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
