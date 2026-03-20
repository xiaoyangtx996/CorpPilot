"""
patch_server.py — 向 server.py 注入新的 Runtime / Models / Traffic API
"""
import sys, re
from pathlib import Path

SRV = Path("dashboard/server.py")
content = SRV.read_text(encoding="utf-8")

# ------- 1. 在 do_GET 的最后一个 if 钱注入新路由 -------
NEW_GET_ROUTES = """
        if path == "/api/departments":
            self.send_json_response(self.agent_monitor.get_department_health())
            return
        if path == "/api/models":
            self.handle_get_models()
            return
        if path == "/api/traffic":
            self.handle_get_traffic(query)
            return
        if path == "/api/traffic/export":
            self.handle_export_traffic()
            return
        if path.startswith("/api/run/logs"):
            self.handle_sse_logs()
            return
"""

anchor = '        if path in {"/", "/dashboard"}:'
if "/api/models" not in content:
    content = content.replace(anchor, NEW_GET_ROUTES + anchor)
    print("GET routes injected")

# ------- 2. 在 do_POST 里注入新路由 -------
NEW_POST_ROUTES = """
        if path == "/api/models":
            self.handle_post_models(data)
            return
        if path == "/api/run/task":
            self.handle_run_task(data)
            return
"""

post_anchor = "        if path == \"/api/tasks\":"
if "/api/run/task" not in content:
    content = content.replace(post_anchor, NEW_POST_ROUTES + post_anchor)
    print("POST routes injected")

# ------- 3. 在 class 末尾（serve_dashboard 方法之前）注入新方法 -------
NEW_METHODS = '''
    # ──────────────────────────────────────────────────────────────────────── #
    # 模型配置 API
    # ──────────────────────────────────────────────────────────────────────── #

    def handle_get_models(self) -> None:
        try:
            from runtime.model_router import ModelRouter
            router = ModelRouter()
            self.send_json_response({"config": router.to_dict()})
        except ImportError:
            self.send_json_response({"config": {}, "error": "runtime 模块未安装"})

    def handle_post_models(self, data: dict) -> None:
        try:
            from runtime.model_router import ModelRouter
            router = ModelRouter()
            action = data.get("action")
            if action == "set_primary":
                router.set_global_primary(
                    data["provider"], data["model"],
                    data.get("api_key", ""), data.get("base_url", "")
                )
            elif action == "set_agent":
                router.set_agent_override(
                    data["agent_id"], data["provider"], data["model"],
                    **{k: v for k, v in data.items() if k not in ("action","agent_id","provider","model")}
                )
            elif action == "remove_agent":
                router.remove_agent_override(data["agent_id"])
            else:
                self.send_error_response(f"未知 action: {action}")
                return
            self.send_json_response({"ok": True, "config": router.to_dict()})
        except Exception as exc:
            self.send_error_response(str(exc))

    # ──────────────────────────────────────────────────────────────────────── #
    # 流量统计 API
    # ──────────────────────────────────────────────────────────────────────── #

    def handle_get_traffic(self, query: dict) -> None:
        try:
            from runtime.model_router import ModelRouter
            from runtime.traffic_monitor import TrafficMonitor
            router = ModelRouter()
            monitor = TrafficMonitor(router=router)
            window = (query.get("window", ["1h"]) or ["1h"])[0]
            stats = monitor.get_stats(window)
            recent = monitor.get_recent(20)
            self.send_json_response({"stats": stats, "recent": recent})
        except Exception as exc:
            self.send_json_response({"stats": {}, "error": str(exc)})

    def handle_export_traffic(self) -> None:
        import time
        from pathlib import Path as P
        log_file = P(__file__).resolve().parent.parent / "data" / "traffic_logs.jsonl"
        payload = log_file.read_bytes() if log_file.exists() else b""
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "application/jsonlines")
        self.send_header("Content-Disposition", f"attachment; filename=traffic_{ts}.jsonl")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ──────────────────────────────────────────────────────────────────────── #
    # 运行时任务触发 API
    # ──────────────────────────────────────────────────────────────────────── #

    def handle_run_task(self, data: dict) -> None:
        """触发 Agent 运行时执行任务。"""
        import threading
        try:
            from runtime.llm_client import LLMClient
            from runtime.model_router import ModelRouter
            from runtime.traffic_monitor import TrafficMonitor
            from runtime.message_bus import MessageBus
            from runtime.agent_manager import AgentManager
        except ImportError as exc:
            self.send_error_response(f"runtime 模块未安装: {exc}")
            return

        agent_id = data.get("agent_id", "ceo")
        task_desc = data.get("task", "")
        if not task_desc:
            self.send_error_response("task 字段不能为空")
            return

        def _bg() -> None:
            router = ModelRouter()
            monitor = TrafficMonitor(router=router)
            client = LLMClient()
            bus = MessageBus()
            manager = AgentManager(bus, router, monitor, client, self.task_service)
            manager.on_log(lambda aid, txt: None)  # 可扩展为 SSE 推送
            manager.spawn(agent_id=agent_id, initial_task=task_desc)

        threading.Thread(target=_bg, daemon=True).start()
        self.send_json_response({"ok": True, "agent_id": agent_id, "message": f"{agent_id} 已在后台启动"})

    def handle_sse_logs(self) -> None:
        """简化的 SSE 日志流（当前返回最近的流量记录）。"""
        try:
            from runtime.model_router import ModelRouter
            from runtime.traffic_monitor import TrafficMonitor
            router = ModelRouter()
            monitor = TrafficMonitor(router=router)
            recent = monitor.get_recent(30)
            data_str = ""
            for r in recent:
                import json as _json
                data_str += f"data: {_json.dumps(r, ensure_ascii=False)}\\n\\n"
            payload = data_str.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            self.send_json_response({"error": str(exc)})

'''

serve_dashboard_anchor = "    def serve_dashboard(self)"
if "handle_get_models" not in content:
    content = content.replace(serve_dashboard_anchor, NEW_METHODS + serve_dashboard_anchor)
    print("Methods injected")

SRV.write_text(content, encoding="utf-8")
print("server.py patched successfully")
