"""Single local controller dispatches durable replies with bounded concurrency."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from runtime.traffic_monitor import TrafficMonitor
from .provider import ProviderError, run_reply
from .runs import Runs
from .cli_controller import CLIController


class ReplyController:
    def __init__(self, store, settings):
        self.settings = settings
        self.lock_file = open(store.data_dir / "controller.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.lock_file.tell() == 0:
                    self.lock_file.write(b"0")
                    self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise ValueError("此数据目录已有运行中的控制服务") from None
        try:
            self.runs = Runs(store)
            self.runs.recover()
            self.monitor = TrafficMonitor(store.data_dir / "reply-usage.jsonl")
            self.stop = threading.Event()
            self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="reply")
            self.futures = {}
            self.unsettled = set()
            self.error = ""
            self.cli = CLIController(store)
            self.thread = threading.Thread(target=self._dispatch, daemon=True, name="reply-dispatch")
            self.thread.start()
        except Exception:
            if cli := getattr(self, "cli", None):
                cli.close()
            if pool := getattr(self, "pool", None):
                pool.shutdown(wait=True)
            self.lock_file.close()
            raise

    def _dispatch(self):
        while not self.stop.wait(0.2):
            try:
                self.cli.tick()
                self._reconcile()
                pending = self.runs.pending()
                self.error = ""
                if not pending:
                    continue
                try:
                    config = self.settings.resolve()
                except ValueError:
                    # Leave pending work cancellable until the Owner fixes configuration.
                    self.error = "模型配置或凭据未就绪，队列等待配置"
                    continue
                for run in pending:
                    if self.stop.is_set() or len(self.futures) >= config["max_concurrency"]:
                        break
                    if not self.monitor.reserve_call(config["rpm"]):
                        break
                    if self.runs.claim(run["id"]):
                        self.unsettled.add(run["id"])
                        future = self.pool.submit(self._execute, run["id"], config)
                        self.futures[future] = run["id"]
                        self.unsettled.remove(run["id"])
            except Exception:
                # Do not print arbitrary database/provider exceptions containing user data.
                # The next poll can recover temporary SQLite contention; claims stay observable.
                self.error = "回复调度异常，请检查运行记录；未自动重发已开始请求"

    def _reconcile(self):
        for future, identity in list(self.futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception:
                    self.unsettled.add(identity)
                del self.futures[future]
        for identity in list(self.unsettled):
            self.runs.fail(identity, "请求已退出但结果未能保存，结果未知；未自动重发", state="unknown")
            self.unsettled.remove(identity)

    def status(self):
        return {"error": self.error, "active_requests": sum(not future.done() for future in tuple(self.futures)),
                "running": self.thread.is_alive() and not self.stop.is_set()}

    def _execute(self, identity, config):
        try:
            snapshot = self.runs.snapshot(identity)
            selected_model = snapshot["agent"]["model"]
            if selected_model != "default":
                config = {**config, "model": selected_model}
            result = run_reply(config, snapshot)
            self.runs.finish(identity, **result)
        except ProviderError as exc:
            self.runs.fail(identity, str(exc), state="unknown" if exc.unknown else "failed")
        except (PermissionError, ValueError, KeyError):
            self.runs.fail(identity, "会话或身份授权已变化，回复未发布")
        except Exception:
            self.runs.fail(identity, "回复执行异常，结果未知；未自动重试", state="unknown")

    def close(self):
        self.stop.set()
        self.thread.join()
        self.cli.close()
        # Active requests have a child-process deadline. Keep the controller lock
        # until completion so another process cannot misclassify a live request.
        self.pool.shutdown(wait=True)
        try:
            self._reconcile()
        except Exception:
            self.error = "停止时状态写入失败，下次启动会将未确认请求标为未知"
        self.lock_file.close()
