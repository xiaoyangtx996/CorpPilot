#!/usr/bin/env python3
"""可插拔执行后端：agent_loop / Claude Code CLI。"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class ExecutionResult:
    success: bool
    summary: str
    artifacts: List[str]
    backend: str


def resolve_backend_name(step: Optional[Dict[str, Any]] = None) -> str:
    if step and step.get("executor"):
        return str(step["executor"])
    sid = (step or {}).get("id", "")
    if sid in {"dev_loop", "dev_parallel", "frontend_dev", "backend_dev"}:
        env = os.environ.get("CORPPILOT_DEV_BACKEND", "").strip().lower()
        if env:
            return env
    return "agent_loop"


def claude_code_available() -> bool:
    cmd = os.environ.get("CORPPILOT_CLAUDE_CMD", "claude")
    return shutil.which(cmd.split()[0] if " " in cmd else cmd) is not None


class AgentLoopBackend:
    name = "agent_loop"

    def run(self, agent_id, prompt, task_id=None, skill_ids=None, manager=None, on_report_done=None):
        if not manager:
            raise ValueError("需要 AgentManager")
        manager.spawn(agent_id=agent_id, initial_task=prompt, task_id=task_id,
                      skill_ids=skill_ids, on_report_done=on_report_done)


class ClaudeCodeBackend:
    name = "claude_code"

    def run_sync(self, prompt: str, task_id: Optional[str] = None, timeout: int = 600) -> ExecutionResult:
        if os.environ.get("CORPPILOT_CLAUDE_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
            arts: List[str] = []
            if task_id:
                d = PROJECT_ROOT / "artifacts" / task_id
                d.mkdir(parents=True, exist_ok=True)
                log = d / "claude_code_dry_run.txt"
                log.write_text(f"[dry-run]\n{prompt[:2000]}\n", encoding="utf-8")
                arts.append(str(log.relative_to(PROJECT_ROOT)))
            return ExecutionResult(True, "Claude Code dry-run OK", arts, self.name)

        cmd = os.environ.get("CORPPILOT_CLAUDE_CMD", "claude")
        if not claude_code_available():
            return ExecutionResult(False, "Claude Code CLI 不可用", [], self.name)
        full = f"{prompt}\n\n产出写入 artifacts/{task_id}/ 或 src/" if task_id else prompt
        argv = [cmd, "-p", full] if " " not in cmd else [*cmd.split(), "-p", full]
        arts: List[str] = []
        try:
            proc = subprocess.run(argv, cwd=str(PROJECT_ROOT), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
            out = (proc.stdout or "") + (proc.stderr or "")
            if task_id:
                d = PROJECT_ROOT / "artifacts" / task_id
                d.mkdir(parents=True, exist_ok=True)
                log = d / "claude_code_output.txt"
                log.write_text(out, encoding="utf-8")
                arts.append(str(log.relative_to(PROJECT_ROOT)))
            return ExecutionResult(proc.returncode == 0, out[:2000] or "done", arts, self.name)
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, f"超时 {timeout}s", arts, self.name)
        except Exception as exc:
            return ExecutionResult(False, str(exc), arts, self.name)

    def run(self, agent_id, prompt, task_id=None, skill_ids=None, manager=None, on_report_done=None):
        r = self.run_sync(prompt, task_id)
        if on_report_done:
            on_report_done(agent_id, r.summary, r.artifacts)


def get_backend(name: str):
    return ClaudeCodeBackend() if name == "claude_code" else AgentLoopBackend()
