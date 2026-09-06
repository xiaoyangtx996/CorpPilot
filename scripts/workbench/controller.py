"""Single local controller dispatches durable replies with bounded concurrency."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from runtime.traffic_monitor import TrafficMonitor
from .provider import ProviderError, run_reply
from .runs import Runs
from .peer_reviews import PeerReviews
from .planning import PlanningError
from .retrospectives import RetrospectiveError
from .cli_controller import CLIController
from .model_reconciliations import ModelReconciliations
from .goal_executions import GoalExecutions


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
            self.peer_reviews = PeerReviews(store)
            self.runs.recover()
            self.model_reconciliations = ModelReconciliations(store)
            self.state_lock = threading.RLock()
            self.monitor = TrafficMonitor(store.data_dir / "reply-usage.jsonl")
            self.stop = threading.Event()
            self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="reply")
            self.futures = {}
            self.unsettled = set()
            # A submit exception can occur after a work item was queued. Without a
            # reliable Future, keep ownership until this pool is fully shut down.
            self.uncertain_submissions = set()
            self.error = ""
            self.cli = CLIController(store)
            self.goals = GoalExecutions(store, self.cli)
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
                self.goals.tick()
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
                    with self.state_lock:
                        if self.stop.is_set() or len(self.futures) + len(self.uncertain_submissions) >= config["max_concurrency"]:
                            break
                        if not self.monitor.reserve_call(config["rpm"]):
                            break
                        if self.runs.claim(run["id"]):
                            self.unsettled.add(run["id"])
                            self.uncertain_submissions.add(run["id"])
                            future = self.pool.submit(self._execute, run["id"], config)
                            self.futures[future] = run["id"]
                            self.uncertain_submissions.remove(run["id"])
                            self.unsettled.remove(run["id"])
            except Exception:
                # Do not print arbitrary database/provider exceptions containing user data.
                # The next poll can recover temporary SQLite contention; claims stay observable.
                self.error = "回复调度异常，请检查运行记录；未自动重发已开始请求"

    def _reconcile(self):
        with self.state_lock:
            self._reconcile_locked()

    def _reconcile_locked(self):
        for future, identity in list(self.futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception:
                    self.unsettled.add(identity)
                del self.futures[future]
        for identity in list(self.unsettled):
            reason = ("提交结果不确定，本地请求可能仍在处理；结果未知，未自动重发"
                      if identity in self.uncertain_submissions else "请求已退出但结果未能保存，结果未知；未自动重发")
            self.runs.fail(identity, reason, state="unknown")
            self.unsettled.remove(identity)

    def status(self):
        with self.state_lock:
            return {"error": self.error or ("模型线程提交结果不确定，仍保留并发槽；请完整停止服务并核查后恢复" if self.uncertain_submissions else ""),
                    "active_requests": sum(not future.done() for future in self.futures) + len(self.uncertain_submissions),
                    "running": self.thread.is_alive() and not self.stop.is_set()}

    def reconcile_unknown(self, identity, payload):
        with self.state_lock:
            # Historical acknowledgements remain available after shutdown or
            # authorization/configuration changes; save still checks exact payload.
            if self.model_reconciliations.get(identity) is not None:
                return self.model_reconciliations.save(identity, payload)
            if self.stop.is_set():
                raise ValueError("模型控制器正在关闭，不能提交新的核查声明")
            if (identity in self.futures.values() or identity in self.unsettled
                    or identity in self.uncertain_submissions):
                raise ValueError("本地模型请求仍被控制器持有或提交结果不确定；请等待，必要时完整停止服务后再核查")
            return self.model_reconciliations.save(identity, payload)

    def enqueue(self, conversation_id, payload):
        with self.state_lock:
            if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                with self.runs.store.connect() as db:
                    existing = db.execute("SELECT 1 FROM runs WHERE conversation_id=? AND request_id=?",
                                          (conversation_id, payload["request_id"].strip())).fetchone()
                if existing:
                    return self.runs.create(conversation_id, payload)
            if self.stop.is_set():
                raise ValueError("模型控制器正在关闭，不能创建新的请求")
            self.settings.resolve()
            return self.runs.create(conversation_id, payload)

    def enqueue_peer(self, conversation_id, payload):
        with self.state_lock:
            if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                with self.runs.store.connect() as db:
                    existing = db.execute("SELECT 1 FROM runs WHERE conversation_id=? AND request_id=?",
                                          (conversation_id, payload["request_id"])).fetchone()
                if existing:
                    return self.peer_reviews.create(conversation_id, payload)
            if self.stop.is_set():
                raise ValueError("模型控制器正在关闭，不能创建新的评议")
            self.settings.resolve()
            return self.peer_reviews.create(conversation_id, payload)

    def enqueue_goal(self, conversation_id, payload):
        with self.state_lock:
            if isinstance(payload, dict) and isinstance(payload.get('request_id'), str):
                with self.runs.store.connect() as db:
                    existing = db.execute('SELECT 1 FROM goal_executions WHERE source_conversation_id=? AND request_id=?',
                                          (conversation_id, payload['request_id'])).fetchone()
                if existing:
                    return self.goals.create(conversation_id, payload)
            if self.stop.is_set():
                raise ValueError('控制器正在关闭，不能创建目标执行')
            self.settings.resolve()
            self.cli.settings.resolve()
            return self.goals.create(conversation_id, payload)

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
        except RetrospectiveError:
            self.runs.fail(identity, "模型复盘格式无效，未创建记忆候选或自动重试")
        except PlanningError:
            self.runs.fail(identity, "模型协作提案无效，未创建项目或自动重试")
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
