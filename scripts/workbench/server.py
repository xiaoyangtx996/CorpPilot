"""Local browser API. Run: python -m workbench.server --port 7892."""
from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import Store

MAX_BODY_BYTES = 64 * 1024


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, store: Store, port: int = 7892):
        self.store = store
        super().__init__(("127.0.0.1", port), Handler)


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
            path = self.path
            if self.command == "GET" and path == "/health":
                return self.respond(200, {"status": "ok"})
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
            self.respond(404, {"error": "接口不存在"})
        except (ValueError, TimeoutError) as exc:
            self.respond(400, {"error": str(exc) if isinstance(exc, ValueError) else "读取请求超时"})
        except KeyError:
            self.respond(404, {"error": "Agent 不存在"})
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
