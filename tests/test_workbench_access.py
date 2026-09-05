"""Owner capability is required even when a worker spoofs browser headers."""
import http.client
import json
from types import SimpleNamespace
import pytest

from test_workbench_api import running, request, TOKENS


def test_all_management_routes_require_capability_before_dispatch(tmp_path):
    with running(tmp_path) as port:
        token = TOKENS[port]
        for method, path in [
            ("GET", "/api/workbench/agents"),
            ("GET", "/api/workbench/artifacts/missing/download"),
            ("GET", "/api/workbench/conversations"),
            ("PATCH", "/api/workbench/cli-settings"),
            ("POST", "/api/workbench/agents"),
            ("GET", "/api/workbench/unknown-future-route")]:
            for credential in ("", "Bearer wrong", "Basic " + token):
                status, body = request(port, method, path, {} if method != "GET" else None,
                    headers={"Authorization": credential, "Origin": f"http://127.0.0.1:{port}",
                             "Sec-Fetch-Site": "same-origin"})
                assert status == 401 and token not in json.dumps(body)
        assert request(port, "GET", "/api/workbench/agents")[0] == 200
        assert request(port, "GET", "/health", headers={"Authorization": ""}) == (200, {"status": "ok"})
        connection = http.client.HTTPConnection("127.0.0.1", port)
        try:
            connection.putrequest("GET", "/api/workbench/agents")
            connection.putheader("Authorization", "Bearer " + token)
            connection.putheader("Authorization", "Bearer " + token)
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 401 and token.encode() not in response.read()
        finally:
            connection.close()
    with running(tmp_path) as port:
        assert TOKENS[port] != token
        assert request(port, "GET", "/api/workbench/agents", headers={"Authorization": "Bearer " + token})[0] == 401
        assert request(port, "GET", "/api/workbench/agents")[0] == 200
    assert token.encode() not in (tmp_path / "workbench.sqlite3").read_bytes()


def test_browser_launch_keeps_secret_out_of_console_and_fallback_is_local(tmp_path, monkeypatch, capsys):
    from workbench import server

    instance = SimpleNamespace(server_port=7892, access_token="test-private-capability",
                               store=SimpleNamespace(data_dir=tmp_path))
    urls = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: urls.append(url) or True)
    assert server.open_owner_page(instance) is None
    assert urls == ["http://127.0.0.1:7892/#access_token=test-private-capability"]
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(server.webbrowser, "open", lambda url: False)
    path = server.open_owner_page(instance)
    assert path.parent == tmp_path and "test-private-capability" in path.read_text(encoding="utf-8")
    assert "test-private-capability" not in capsys.readouterr().out
    assert path.name.startswith("owner-access-")


def test_private_runtime_data_cannot_be_served_as_static_assets(tmp_path):
    from workbench.server import WorkbenchServer
    from workbench.store import Store

    web = tmp_path / "dist"
    with pytest.raises(ValueError, match="静态资源"):
        WorkbenchServer(Store(web / "assets" / "private-data"), 0, web)
