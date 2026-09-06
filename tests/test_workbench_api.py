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

TOKENS = {}


@contextmanager
def running(data_dir):
    server = WorkbenchServer(Store(data_dir), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    TOKENS[server.server_port] = server.access_token
    try:
        yield server.server_port
    finally:
        TOKENS.pop(server.server_port, None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(port, method, path, payload=None, headers=None, raw=None):
    body = json.dumps(payload).encode() if payload is not None else raw
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body, {"Content-Type": "application/json",
                           "Authorization": "Bearer " + TOKENS.get(port, ""), **(headers or {})})
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
            "name": "小林", "template_id": templates[0]["id"], "skills": ["coding"], "tools": ["read"],
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
                connection.putheader("Authorization", "Bearer " + TOKENS[port])
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


def test_conversation_http_lifecycle_and_restart(tmp_path):
    base = "/api/workbench/conversations"
    with running(tmp_path) as port:
        _, agents = request(port, "GET", "/api/workbench/agents")
        first, second = [agent["id"] for agent in agents[:2]]
        status, room = request(port, "POST", base, {
            "type": "project", "title": "发布讨论", "member_ids": [first],
        })
        assert status == 201
        path = base + "/" + room["id"]
        assert request(port, "GET", path) == (200, room)
        assert request(port, "GET", base)[1] == [room]
        assert request(port, "PATCH", path + "/members/" + second, {"joined": True})[1]["member_ids"] == sorted([first, second])
        payload = {"content": "确认范围", "request_id": "client-request-1"}
        status, message = request(port, "POST", path + "/messages", payload)
        assert status == 201 and message["sender_kind"] == "owner" and message["sender_id"] is None
        assert request(port, "POST", path + "/messages", payload) == (201, message)
        assert request(port, "GET", path + "/messages?after=0&limit=1") == (200, [message])
        assert request(port, "GET", path + f'/messages?after={message["sequence"]}')[1] == []
        assert request(port, "PATCH", path, {"archived": True})[1]["archived"]
        assert request(port, "POST", path + "/messages", {"content": "不得发送", "request_id": "2"})[0] == 400
    with running(tmp_path) as port:
        assert request(port, "GET", path)[1]["archived"]
        assert request(port, "GET", path + "/messages")[1] == [message]
        assert request(port, "PATCH", path, {"archived": False})[0] == 200
        assert request(port, "PATCH", path + "/members/" + second, {"joined": False})[1]["member_ids"] == [first]


def test_conversation_http_rejects_impersonation_and_bad_paging(tmp_path):
    base = "/api/workbench/conversations"
    with running(tmp_path) as port:
        _, agents = request(port, "GET", "/api/workbench/agents")
        actor = agents[0]["id"]
        _, room = request(port, "POST", base, {"type": "dm", "title": "私聊", "member_ids": [actor]})
        path = base + "/" + room["id"]
        for query in ("actor_id=" + actor, "after=-1", "limit=201", "limit=", "after=a", "after=0&after=1"):
            assert request(port, "GET", path + "/messages?" + query)[0] == 400
        for field in ("actor_id", "sender_id", "sender_kind"):
            assert request(port, "POST", path + "/messages", {"content": "伪造", "request_id": "1", field: actor})[0] == 400
        assert request(port, "GET", base + "?actor_id=" + actor)[0] == 400
        assert request(port, "PATCH", path + "/members/" + actor, {"joined": False})[0] == 400
        assert request(port, "GET", base + "/missing/messages")[0] == 404
        assert request(port, "GET", path + "/messages")[1] == []


def test_model_settings_api_never_returns_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("CORPPILOT_TEST_KEY", "test-secret-not-for-output")
    path = "/api/workbench/model-settings"
    with running(tmp_path) as port:
        status, initial = request(port, "GET", path)
        assert status == 200 and not initial["enabled"] and not initial["configured"]
        payload = {"model": "test-model", "base_url": "http://127.0.0.1:8999/v1",
                   "api_key_env": "CORPPILOT_TEST_KEY", "max_output_tokens": 512,
                   "timeout_seconds": 30, "rpm": 10, "max_concurrency": 2, "enabled": True}
        status, saved = request(port, "PATCH", path, payload)
        assert status == 200 and saved["credential_available"] and saved["configured"]
        assert "test-secret-not-for-output" not in json.dumps(saved)
        assert request(port, "PATCH", path, {"api_key": "test-secret-not-for-output"})[0] == 400
        assert request(port, "PATCH", path, {"rpm": 0})[0] == 400
    with running(tmp_path) as port:
        assert request(port, "GET", path) == (200, saved)
        monkeypatch.delenv("CORPPILOT_TEST_KEY")
        assert not request(port, "GET", path)[1]["credential_available"]
