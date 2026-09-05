"""CLI failure must never complete a serial or parallel workflow step."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from core import TaskPriority, TaskService, TaskStatus, TaskType, WorkflowEngine
from runtime.execution_backends import ClaudeCodeBackend, ExecutionResult
from runtime_bridge import RuntimeOrchestrator


class RuntimeFailureTest(unittest.TestCase):
    def test_cli_failure_routes_only_to_failure_callback(self):
        for failure in ("missing", "nonzero", "timeout", "exception"):
            with self.subTest(failure=failure), patch.dict("os.environ", {"CORPPILOT_CLAUDE_DRY_RUN": "0"}), \
                    patch("runtime.execution_backends.claude_code_available", return_value=failure != "missing"), \
                    patch("runtime.execution_backends.subprocess.run") as process:
                process.return_value = subprocess.CompletedProcess([], 2, "", "failed")
                if failure == "timeout":
                    process.side_effect = subprocess.TimeoutExpired("claude", 600)
                elif failure == "exception":
                    process.side_effect = OSError("process unavailable")
                done, failed = Mock(), Mock()
                result = ClaudeCodeBackend().run("dev", "build", on_report_done=done, on_report_failed=failed)
                self.assertFalse(result.success)
                done.assert_not_called()
                failed.assert_called_once_with("dev", result.summary, result.artifacts)
                self.assertFalse(ClaudeCodeBackend().run("dev", "build").success)

    def test_success_keeps_original_callback(self):
        result = ExecutionResult(True, "built", ["result.txt"], "claude_code")
        with patch.object(ClaudeCodeBackend, "run_sync", return_value=result):
            done, failed = Mock(), Mock()
            self.assertIs(ClaudeCodeBackend().run("dev", "build", on_report_done=done,
                                                on_report_failed=failed), result)
            done.assert_called_once_with("dev", "built", ["result.txt"])
            failed.assert_not_called()

    def test_serial_and_parallel_failure_cannot_advance(self):
        for parallel in (False, True):
            for success in (False, True):
                with self.subTest(parallel=parallel, success=success), tempfile.TemporaryDirectory() as tmp:
                    service = TaskService(Path(tmp))
                    task = service.create_task("test", TaskType.RD, TaskPriority.P2, "owner", flow_id="legacy")
                    task_id = task["task_id"]
                    def executing(tasks):
                        tasks[0]["status"] = "executing"
                        return tasks
                    service.store.update([], executing)
                    task = service.get_task(task_id)
                    workflow = WorkflowEngine(service, auto_runtime=False)
                    orchestrator = RuntimeOrchestrator(workflow)
                    queued = []
                    def thread_factory(*args, **kwargs):
                        queued.append(kwargs["target"])
                        return Mock()
                    results = [ExecutionResult(success, "CLI result", ["cli.log"], "claude_code")]
                    if parallel:
                        results.append(ExecutionResult(True, "other branch built", ["ok.txt"], "claude_code"))
                    with patch("runtime_bridge.threading.Thread", side_effect=thread_factory), \
                            patch.object(orchestrator, "_get_manager", return_value=Mock()), \
                            patch.object(orchestrator, "on_agent_report_done") as done, \
                            patch("runtime.execution_backends.resolve_backend_name", return_value="claude_code"), \
                            patch.object(ClaudeCodeBackend, "run_sync", side_effect=results):
                        if parallel:
                            flow = Mock()
                            flow.parallel_branches.return_value = [
                                {"role": "frontend", "executor": "claude_code"},
                                {"role": "backend", "executor": "claude_code"},
                            ]
                            orchestrator._spawn_parallel(task, {}, flow)
                        else:
                            orchestrator._spawn_for_task(task)
                        queued[0]()
                        if parallel:
                            self.assertIn(task_id, orchestrator._spawned)
                            queued[1]()
                        self.assertNotIn(task_id, orchestrator._spawned)
                        updated = service.get_task(task_id)
                        if success:
                            done.assert_called_once()
                            self.assertEqual(updated["status"], "executing")
                        else:
                            done.assert_not_called()
                            self.assertEqual(updated["status"], TaskStatus.BLOCKED.value)
                            self.assertEqual(updated["runtime"]["execution_error"], "CLI result")
                            self.assertEqual(updated["runtime"]["failure_artifacts"], ["cli.log"])
                            self.assertFalse(updated.get("artifacts"))


if __name__ == "__main__":
    unittest.main()
