"""Exercise actual HTTP framing and persisted identities on loopback sockets."""
import http.client
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.server import MAX_BODY_BYTES, WorkbenchServer
from workbench.store import Store


@contextmanager
def running(data_dir):
    server = WorkbenchServer(Store(data_dir), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(port, method, path, payload=None, headers=None, raw=None):
    body = json.dumps(payload).encode() if payload is not None else raw
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        assert response.getheader("Access-Control-Allow-Origin") is None
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_identity_api_and_restart(tmp_path):
    with running(tmp_path) as port:
        assert request(port, "GET", "/health") == (200, {"status": "ok"})
        status, templates = request(port, "GET", "/api/workbench/templates")
        assert status == 200 and templates
        _, initial = request(port, "GET", "/api/workbench/agents")
        status, agent = request(port, "POST", "/api/workbench/agents", {
            "name": "小林", "template_id": templates[0]["id"], "skills": ["review"], "tools": ["read"],
        }, headers={"Origin": f"http://127.0.0.1:{port}"})
        assert status == 201
        path = "/api/workbench/agents/" + agent["id"]
        assert request(port, "GET", path) == (200, agent)
        status, edited = request(port, "PATCH", path, {"name": "小林二号", "enabled": False})
        assert status == 200 and not edited["enabled"] and edited["id"] == agent["id"]
    with running(tmp_path) as port:
        assert request(port, "GET", path) == (200, edited)
        _, restored = request(port, "GET", "/api/workbench/agents")
        assert len(restored) == len(initial) + 1
        assert request(port, "PATCH", path, {"enabled": True})[1]["enabled"]


@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "text/plain"}, b"{}"),
    ({"Content-Type": "application/json; charset=latin1"}, b"{}"),
    ({}, b"{"), ({}, b"[]"), ({}, b"\xff"), ({}, b""),
    ({}, b"x" * (MAX_BODY_BYTES + 1)),
    ({"Transfer-Encoding": "chunked"}, b"{}"),
    ({"Content-Length": "-1"}, b"{}"),
], ids=["content-type", "charset", "malformed", "array", "encoding", "empty", "oversize", "chunked", "negative-length"])
def test_invalid_json_requests(tmp_path, headers, body):
    with running(tmp_path) as port:
        status, value = request(port, "POST", "/api/workbench/agents", headers=headers, raw=body)
        assert status == 400 and value["error"]


def test_origin_host_and_unknown_routes(tmp_path):
    with running(tmp_path) as port:
        for headers in ({"Host": f"evil.example:{port}"}, {"Origin": "http://evil.example"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            assert request(port, "GET", "/api/workbench/agents", headers=headers)[0] == 400
        for path in ("/unknown", "/api/workbench/agents/missing", "/../../data/workbench.sqlite3"):
            assert request(port, "GET", path)[0] == 404
        assert request(port, "POST", "/api/workbench/agents", {"name": "bad"})[0] == 400
        assert request(port, "PATCH", "/api/workbench/agents/missing", {"name": "bad"})[0] == 404


def test_ambiguous_headers_rejected(tmp_path):
    with running(tmp_path) as port:
        for name, value in (("Host", f"127.0.0.1:{port}"), ("Content-Length", "2"),
                            ("Content-Type", "application/json"), ("Origin", f"http://127.0.0.1:{port}")):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.putrequest("POST", "/api/workbench/agents")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "2")
                if name == "Origin":
                    connection.putheader("Origin", value)
                connection.putheader(name, value)
                connection.endheaders(b"{}")
                response = connection.getresponse()
                assert response.status == 400
                assert json.loads(response.read())["error"]
            finally:
                connection.close()


def test_browser_assets_are_confined_to_build_directory(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<title>Workbench</title>")
    (web / "assets" / "app.js").write_text("export default 1")
    (tmp_path / "secret.txt").write_text("private")
    server = WorkbenchServer(Store(tmp_path / "data"), 0, web)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path, expected in (("/", 200), ("/assets/app.js", 200),
                               ("/assets/../../secret.txt", 404), ("/secret.txt", 404)):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                assert response.status == expected
                assert b"private" not in response.read()
            finally:
                connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
