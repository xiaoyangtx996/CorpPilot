"""Actual loopback HTTP verifies protocol/failure boundaries, not model quality."""
import json
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.provider import ProviderError, reply, run_reply

SNAPSHOT = {"agent": {"id": "a"}, "instructions": "role instructions", "messages": [
    {"sender_kind": "owner", "sender_id": None, "content": "question"},
    {"sender_kind": "agent", "sender_id": "b", "content": "other member"},
    {"sender_kind": "agent", "sender_id": "a", "content": "previous reply"},
]}


@contextmanager
def endpoint(value, status=200, delay=0):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Location", "http://127.0.0.1:1/leak")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "explicit-model",
              "max_output_tokens": 100, "timeout_seconds": 2, "api_key": "dummy-test-secret"}
    try:
        yield config, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_protocol_context_and_missing_usage():
    result = {"choices": [{"finish_reason": "stop", "message": {"content": "actual response"}}]}
    with endpoint(result) as (config, requests):
        response = reply(config, SNAPSHOT)
    assert response == {"content": "actual response", "model": "explicit-model",
                        "prompt_tokens": None, "completion_tokens": None}
    assert len(requests) == 1
    path, auth, payload = requests[0]
    assert path == "/v1/chat/completions" and auth == "Bearer dummy-test-secret"
    assert payload["max_tokens"] == 100 and payload["stream"] is False and "tools" not in payload
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "user", "assistant"]
    assert payload["messages"][2]["content"] == "[群成员 b]\nother member"


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_no_redirect_retry_or_secret_error_echo(status):
    with endpoint(b"dummy-test-secret sensitive upstream body", status) as (config, requests):
        with pytest.raises(ProviderError) as error:
            reply(config, SNAPSHOT)
        assert str(status) in str(error.value)
        assert "dummy-test-secret" not in str(error.value) and "sensitive" not in str(error.value)
        assert len(requests) == 1


@pytest.mark.parametrize("result", [b"not json", {}, {"choices": []},
    {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "ok", "tool_calls": [{}]}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {"prompt_tokens": -1}},
])
def test_invalid_incomplete_or_tool_reply_is_not_success(result):
    with endpoint(result) as (config, _):
        with pytest.raises(ProviderError):
            reply(config, SNAPSHOT)


def test_real_request_process_and_total_deadline():
    value = {"choices": [{"finish_reason": "stop", "message": {"content": "child result"}}]}
    with endpoint(value) as (config, requests):
        assert run_reply(config, SNAPSHOT)["content"] == "child result"
        assert len(requests) == 1
    with endpoint(value, delay=2) as (config, requests):
        config["timeout_seconds"] = 1
        started = time.monotonic()
        with pytest.raises(ProviderError) as error:
            run_reply(config, SNAPSHOT)
        assert error.value.unknown
        assert time.monotonic() - started < 1.8
        assert len(requests) == 1
