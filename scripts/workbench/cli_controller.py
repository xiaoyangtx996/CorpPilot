"""CLI queue owned and polled under the parent controller's lifetime lock."""
from concurrent.futures import Future, ThreadPoolExecutor
import json
import threading

from .cli import run_codex
from .cli_settings import CLISettings
from .executions import Executions


class CLIController:
    def __init__(self, store):
        # Only construct after acquiring controller.lock; never recover a live worker.
        self.store = store
        self.settings = CLISettings(store)
        self.executions = Executions(store)
        self.executions.recover()
        self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="task-cli")
        self.active = {}
        self.error = ""
        self.closed = False

    @staticmethod
    def _unstarted(summary):
        return {"exit_code": None, "summary": summary, "success": False, "not_started": True}

    def _execute(self, run, config, cancel):
        try:
            snapshot = self.executions.snapshot(run["id"])
            selected = snapshot["agent"]["model"]
            model = config["model"] if selected == "default" else selected
            prompt = "执行以下已授权任务。只使用本次工作目录，结果交给 Owner 评审。\n" + json.dumps({
                "role": snapshot["instructions"], "task": snapshot["task"],
                "source": snapshot["source_message"]["content"],
            }, ensure_ascii=False)
            if len(prompt) > 64000:
                return self._unstarted("任务上下文超过 CLI 上限，未启动")
            if cancel.is_set():
                return self._unstarted("启动前已请求停止")
        except Exception:
            return self._unstarted("无法取得有效授权任务上下文，未启动")
        try:
            result = run_codex(executable=config["executable"], data_dir=self.store.data_dir,
                               execution_id=run["id"], prompt=prompt, model=model,
                               api_key=config["api_key"], timeout_seconds=config["timeout_seconds"], cancel=cancel)
            if result["reason"] == "cancelled" and result.get("workspace") is None:
                return self._unstarted("启动前已请求停止")
            return {"exit_code": result["exit_code"], "summary": result["summary"],
                    "success": result["success"] is True, "not_started": False}
        except Exception:
            # Never infer that an arbitrary runner exception happened before spawning.
            return {"exit_code": None, "summary": "CLI 执行异常，实例与结果待核实；未重试",
                    "success": False, "not_started": False}

    def _reconcile(self):
        failed_write = False
        for identity, (run, cancel, future) in list(self.active.items()):
            if future.done():
                try:
                    result = future.result()
                except Exception:
                    result = {"exit_code": None, "summary": "执行线程异常，实例与结果待核实",
                              "success": False, "not_started": False}
                try:
                    self.executions.report(identity, run["attempt"], run["requirement_version"], **result)
                except Exception:
                    failed_write = True
                    continue
                # Keep the same completed future if writing fails; retry only the report.
                del self.active[identity]
            else:
                try:
                    self.executions.snapshot(identity)
                except (ValueError, PermissionError, KeyError):
                    cancel.set()
                except Exception:
                    # If authority cannot be checked, stop conservatively without fabricating a result.
                    cancel.set()
                    failed_write = True
        if failed_write:
            raise RuntimeError("CLI 状态尚未落库")

    def tick(self):
        if self.closed:
            return
        try:
            self._reconcile()
            self.error = ""
            with self.store.connect() as db:
                unknown = db.execute("SELECT 1 FROM task_executions WHERE state='unknown' LIMIT 1").fetchone()
            if unknown:
                self.error = "存在未核实的 CLI 实例或结果，执行队列暂停；请核查后再恢复"
                return
            pending = self.executions.pending()
            if not pending:
                return
            try:
                config = self.settings.resolve()
            except ValueError:
                self.error = "CLI 配置或凭据未就绪，队列等待配置"
                return
            for run in pending:
                if len(self.active) >= config["max_concurrency"]:
                    break
                if self.executions.claim(run["id"]):
                    cancel = threading.Event()
                    try:
                        future = self.pool.submit(self._execute, run, config, cancel)
                    except Exception:
                        # submit may queue work before failing to start another thread.
                        cancel.set()
                        future = Future()
                        future.set_result({"exit_code": None, "summary": "执行线程提交异常，实例与结果待核实",
                                           "success": False, "not_started": False})
                    self.active[run["id"]] = (run, cancel, future)
        except Exception:
            self.error = "CLI 调度或状态写入异常；已开始的执行不会自动重发"

    def status(self):
        return {"error": self.error, "active_requests": len(self.active), "running": not self.closed}

    def close(self):
        self.closed = True
        for identity, (_, cancel, _) in list(self.active.items()):
            cancel.set()
            try:
                self.executions.cancel(identity)
            except Exception:
                self.error = "停止请求写入失败，仍已通知本地进程退出"
        self.pool.shutdown(wait=True)
        try:
            self._reconcile()
        except Exception:
            self.error = "停止时结果写入失败，下次启动将标记未知"
