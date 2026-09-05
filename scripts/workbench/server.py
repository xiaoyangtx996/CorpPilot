"""Local browser API. Run: python -m workbench.server --port 7892."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote

from .store import REPO_ROOT, Store
from .settings import Settings
from .controller import ReplyController
from .tasks import Tasks, TaskVersionConflict
from .cli_settings import CLISettings
from . import artifacts
from .reviews import Reviews
from .memories import Memories
from .collaboration import Collaboration
from .planning import Planning

MAX_BODY_BYTES = 64 * 1024


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, store: Store, port: int = 7892, frontend_dir: Path | None = None):
        self.store = store
        self.settings = Settings(store)
        self.tasks = Tasks(store)
        self.cli_settings = CLISettings(store)
        self.reviews = Reviews(store)
        self.memories = Memories(store)
        self.collaboration = Collaboration(store)
        self.planning = Planning(store)
        self.frontend_dir = (frontend_dir or REPO_ROOT / "frontend" / "dist").resolve()
        super().__init__(("127.0.0.1", port), Handler)
        try:
            self.controller = ReplyController(store, self.settings)
        except Exception:
            super().server_close()
            raise

    def server_close(self):
        if controller := getattr(self, "controller", None):
            controller.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        # Request URLs may contain user content; keep access logging opt-in later.
        pass

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed.update({"127.0.0.1", "localhost"})
        if len(hosts) != 1 or hosts[0] not in allowed:
            raise ValueError("不允许的 Host")
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [f"http://{hosts[0]}"]:
            raise ValueError("不允许跨来源请求")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ValueError("不允许跨站请求")

    def read_json(self):
        if self.headers.get_all("Transfer-Encoding"):
            raise ValueError("不支持 Transfer-Encoding")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError("必须提供有效 Content-Length")
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY_BYTES:
            raise ValueError("JSON 请求体必须为 1–65536 字节")
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type 必须为 application/json")
        if self.headers.get_content_charset("utf-8").lower() != "utf-8":
            raise ValueError("JSON 必须使用 UTF-8")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("请求体不完整")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("请求体不是有效 UTF-8 JSON") from None
        if not isinstance(value, dict):
            raise ValueError("JSON 请求体必须是对象")
        return value

    def dispatch(self):
        try:
            self.check_origin()
            store = self.server.store
            url = urlsplit(self.path)
            path = url.path
            query = parse_qs(url.query, keep_blank_values=True)
            if query and not (self.command == "GET" and path.endswith("/messages")):
                raise ValueError("不支持的查询参数")
            if self.command == "GET" and (path == "/" or path.startswith("/assets/")):
                relative = "index.html" if path == "/" else path.lstrip("/")
                target = (self.server.frontend_dir / relative).resolve()
                if target.is_relative_to(self.server.frontend_dir) and target.is_file():
                    body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return self.respond(404, {"error": "浏览器界面尚未构建，请先在 frontend 运行 npm run build"})
            if self.command == "GET" and path == "/health":
                return self.respond(200, {"status": "ok"})
            if self.command == "GET" and path == "/api/workbench/runtime":
                return self.respond(200, self.server.controller.status())
            if self.command == "GET" and path == "/api/workbench/cli-runtime":
                return self.respond(200, self.server.controller.cli.status())
            if self.command == "GET" and path == "/api/workbench/execution-reconciliations/pending":
                return self.respond(200, self.server.controller.cli.reconciliations.pending())
            if path == "/api/workbench/model-settings":
                if self.command == "GET":
                    return self.respond(200, self.server.settings.get())
                if self.command == "PATCH":
                    return self.respond(200, self.server.settings.save(self.read_json()))
            if path == "/api/workbench/cli-settings":
                if self.command == "GET":
                    return self.respond(200, self.server.cli_settings.get())
                if self.command == "PATCH":
                    return self.respond(200, self.server.cli_settings.save(self.read_json()))
            if path == "/api/workbench/cli-settings/probe" and self.command == "POST":
                if self.read_json():
                    raise ValueError("CLI 检查请求体必须为空对象")
                return self.respond(200, self.server.cli_settings.probe())
            prefix = "/api/workbench/memories/"
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) in (2, 3) and all(parts):
                    scope, identity = parts[:2]
                    memory = self.server.memories
                    if len(parts) == 2 and self.command == "GET":
                        return self.respond(200, memory.get(scope, identity))
                    if len(parts) == 3:
                        if parts[2] == "history" and self.command == "GET":
                            return self.respond(200, memory.history(scope, identity))
                        if parts[2] == "candidates":
                            if self.command == "GET":
                                return self.respond(200, memory.candidates(scope, identity))
                            if self.command == "POST":
                                return self.respond(201, memory.propose(scope, identity, self.read_json()))
                        if parts[2] == "rollback" and self.command == "POST":
                            return self.respond(201, memory.rollback(scope, identity, self.read_json()))
            prefix = "/api/workbench/memory-candidates/"
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) == 1 and parts[0] and self.command == "GET":
                    return self.respond(200, self.server.memories.candidate(parts[0]))
                if len(parts) == 2 and parts[0] and parts[1] == "decision" and self.command == "POST":
                    return self.respond(201, self.server.memories.decide(parts[0], self.read_json()))
            prefix = "/api/workbench/collaboration-plans/"
            if path.startswith(prefix):
                identity = path[len(prefix):]
                if identity and "/" not in identity and self.command == "GET":
                    return self.respond(200, self.server.collaboration.get(identity))
            if self.command == "GET" and path == "/api/workbench/templates":
                return self.respond(200, store.templates())
            if path == "/api/workbench/agents":
                if self.command == "GET":
                    return self.respond(200, store.agents())
                if self.command == "POST":
                    return self.respond(201, store.save_agent(self.read_json()))
            prefix = "/api/workbench/agents/"
            if path.startswith(prefix) and path[len(prefix):] and "/" not in path[len(prefix):]:
                agent_id = path[len(prefix):]
                if self.command == "GET":
                    return self.respond(200, store.agent(agent_id))
                if self.command == "PATCH":
                    return self.respond(200, store.save_agent(self.read_json(), agent_id))
            prefix = "/api/workbench/conversations"
            if path == prefix:
                if self.command == "GET":
                    return self.respond(200, store.conversations())
                if self.command == "POST":
                    return self.respond(201, store.save_conversation(self.read_json()))
            if path.startswith(prefix + "/"):
                parts = path[len(prefix) + 1:].split("/")
                conversation_id = parts[0]
                if len(parts) == 1 and conversation_id:
                    if self.command == "GET":
                        return self.respond(200, store.conversation(conversation_id))
                    if self.command == "PATCH":
                        return self.respond(200, store.save_conversation(self.read_json(), conversation_id))
                if len(parts) == 2 and parts[1] == "messages":
                    if self.command == "GET":
                        if set(query) - {"after", "limit"} or any(len(v) != 1 for v in query.values()):
                            raise ValueError("不支持的消息分页参数")
                        after = int(query.get("after", ["0"])[0])
                        limit = int(query.get("limit", ["100"])[0])
                        return self.respond(200, store.messages(conversation_id, after=after, limit=limit))
                    if self.command == "POST":
                        # This is the local Owner API. Agent identity is never taken from HTTP input.
                        return self.respond(201, store.send_message(conversation_id, self.read_json()))
                if len(parts) == 2 and parts[1] == "runs":
                    if self.command == "GET":
                        return self.respond(200, self.server.controller.runs.list(conversation_id))
                    if self.command == "POST":
                        self.server.settings.resolve()
                        return self.respond(202, self.server.controller.runs.create(conversation_id, self.read_json()))
                if len(parts) == 2 and parts[1] == "tasks":
                    if self.command == "GET":
                        return self.respond(200, self.server.tasks.list(conversation_id))
                    if self.command == "POST":
                        return self.respond(201, self.server.tasks.create(conversation_id, self.read_json()))
                if len(parts) == 2 and parts[1] == "collaboration-plans":
                    if self.command == "GET":
                        return self.respond(200, self.server.collaboration.list(conversation_id))
                    if self.command == "POST":
                        return self.respond(201, self.server.collaboration.create(conversation_id, self.read_json()))
                if len(parts) == 2 and parts[1] == "collaboration-proposals":
                    if self.command == "GET":
                        return self.respond(200, self.server.planning.list(conversation_id))
                    if self.command == "POST":
                        return self.respond(202, self.server.planning.create(conversation_id, self.read_json()))
                if len(parts) == 3 and parts[1] == "members" and parts[2] and self.command == "PATCH":
                    payload = self.read_json()
                    if set(payload) != {"joined"}:
                        raise ValueError("成员请求必须只含 joined")
                    return self.respond(200, store.set_member(conversation_id, parts[2], payload["joined"]))
            prefix = "/api/workbench/tasks/"
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) == 1 and parts[0]:
                    if self.command == "GET":
                        return self.respond(200, self.server.tasks.get(parts[0]))
                    if self.command == "PATCH":
                        return self.respond(200, self.server.tasks.revise(parts[0], self.read_json()))
                if len(parts) == 2 and parts[0] and parts[1] == "revisions" and self.command == "GET":
                    return self.respond(200, self.server.tasks.history(parts[0]))
                if len(parts) == 2 and parts[0] and parts[1] == "dependencies":
                    if self.command == "GET":
                        return self.respond(200, self.server.tasks.dependencies(parts[0]))
                    if self.command == "PATCH":
                        return self.respond(200, self.server.tasks.set_dependencies(parts[0], self.read_json()))
                if len(parts) == 2 and parts[0] and parts[1] == "executions":
                    if self.command == "GET":
                        return self.respond(200, self.server.controller.cli.executions.list(parts[0]))
                    if self.command == "POST":
                        return self.respond(202, self.server.controller.cli.enqueue(parts[0], self.read_json()))
            prefix = "/api/workbench/executions/"
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) == 1 and parts[0] and self.command == "GET":
                    return self.respond(200, self.server.controller.cli.executions.get(parts[0]))
                if len(parts) == 2 and parts[0] and parts[1] == "reconciliation":
                    if self.command == "GET":
                        return self.respond(200, self.server.controller.cli.reconciliations.get(parts[0]))
                    if self.command == "POST":
                        return self.respond(201, self.server.controller.cli.reconcile_unknown(parts[0], self.read_json()))
                if len(parts) == 2 and parts[0] and parts[1] == "artifacts" and self.command == "GET":
                    return self.respond(200, artifacts.list_for(store, parts[0]))
                if len(parts) == 2 and parts[0] and parts[1] == "review":
                    if self.command == "GET":
                        return self.respond(200, self.server.reviews.get(parts[0]))
                    if self.command == "POST":
                        return self.respond(201, self.server.reviews.save(parts[0], self.read_json()))
                if len(parts) == 2 and parts[0] and parts[1] == "cancel" and self.command == "POST":
                    if self.read_json():
                        raise ValueError("取消请求体必须为空对象")
                    return self.respond(200, self.server.controller.cli.executions.cancel(parts[0]))
            prefix = "/api/workbench/artifacts/"
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) == 2 and parts[0] and parts[1] == "download" and self.command == "GET":
                    artifact = artifacts.get(store, parts[0])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(artifact["path"].rsplit("/", 1)[-1], safe=""))
                    self.send_header("Content-Length", str(len(artifact["data"])))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(artifact["data"])
                    return
            prefix = "/api/workbench/runs/"
            proposal_prefix = "/api/workbench/collaboration-proposals/"
            if path.startswith(proposal_prefix) and self.command == "GET":
                identity = path[len(proposal_prefix):]
                if identity and "/" not in identity:
                    return self.respond(200, self.server.planning.get(identity))
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if len(parts) == 1 and parts[0] and self.command == "GET":
                    return self.respond(200, self.server.controller.runs.get(parts[0]))
                if len(parts) == 2 and parts[1] == "cancel" and self.command == "POST":
                    if self.read_json():
                        raise ValueError("取消请求体必须为空对象")
                    return self.respond(200, self.server.controller.runs.cancel(parts[0]))
            self.respond(404, {"error": "接口不存在"})
        except TaskVersionConflict as exc:
            self.respond(409, {"error": str(exc)})
        except (ValueError, TimeoutError) as exc:
            self.respond(400, {"error": str(exc) if isinstance(exc, ValueError) else "读取请求超时"})
        except KeyError:
            self.respond(404, {"error": "对象不存在"})
        except PermissionError as exc:
            self.respond(403, {"error": str(exc)})
        except Exception:
            logging.exception("Workbench API request failed")
            self.respond(500, {"error": "服务暂时不可用"})

    do_GET = do_POST = do_PATCH = do_DELETE = do_OPTIONS = dispatch


def main():
    parser = argparse.ArgumentParser(description="CorpPilot 本机工作台 API")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=7892)
    args = parser.parse_args()
    server = WorkbenchServer(Store(args.data_dir), args.port)
    print(f"CorpPilot API http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
