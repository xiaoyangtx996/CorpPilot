"""Model settings persist atomically and never expose environment credentials."""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.settings import Settings
from workbench.store import Store


CONFIG = {"model": "chosen-model", "base_url": "https://api.example.com/v1",
          "api_key_env": "CORPPILOT_TEST_API_KEY", "max_output_tokens": 1024,
          "timeout_seconds": 30, "rpm": 20, "max_concurrency": 2}


def test_explicit_configuration_persists_without_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(CONFIG["api_key_env"], "test-secret-never-store")
    store = Store(tmp_path)
    settings = Settings(store)
    assert not settings.get()["enabled"]
    assert not settings.get()["configured"]
    with pytest.raises(ValueError):
        settings.resolve()
    with pytest.raises(ValueError):
        settings.save({"enabled": True})
    saved = settings.save(CONFIG)
    assert saved["configured"] and saved["credential_available"] and not saved["enabled"]
    with pytest.raises(ValueError):
        settings.resolve()
    settings.save({"enabled": True})
    restored = Settings(Store(tmp_path))
    assert restored.get() == {**saved, "enabled": True}
    assert restored.resolve()["api_key"] == "test-secret-never-store"
    monkeypatch.setenv(CONFIG["api_key_env"], "rotated-secret")
    assert restored.resolve()["api_key"] == "rotated-secret"
    with store.connect() as db:
        dumped = "\n".join(db.iterdump())
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert "test-secret-never-store" not in dumped
    assert "rotated-secret" not in dumped
    assert "api_key\"" not in json.dumps(restored.get())
    assert capsys.readouterr() == ("", "")
    monkeypatch.setenv(CONFIG["api_key_env"], "  ")
    assert not restored.get()["credential_available"]
    with pytest.raises(ValueError, match="密钥环境变量未设置"):
        restored.resolve()


@pytest.mark.parametrize("patch", [
    {"api_key": "never-echo-this"}, {"provider": "openai"}, {"configured": True},
    {"enabled": 1}, {"model": ""}, {"model": "a\nb"}, {"model": 3},
    {"api_key_env": "bad-name"}, {"api_key_env": "9BAD"}, {"api_key_env": " KEY "},
    {"api_key_env": "X" * 129}, {"max_output_tokens": True}, {"max_output_tokens": 0},
    {"max_output_tokens": 131073}, {"timeout_seconds": 0.5}, {"timeout_seconds": 301},
    {"rpm": 0}, {"rpm": 601}, {"max_concurrency": 17}, {"max_concurrency": None},
    [], None,
])
def test_invalid_patch_is_atomic_and_error_does_not_echo_values(tmp_path, patch):
    settings = Settings(Store(tmp_path))
    before = settings.save(CONFIG)
    with pytest.raises(ValueError) as error:
        settings.save(patch)
    assert "never-echo-this" not in str(error.value)
    assert settings.get() == before


@pytest.mark.parametrize("url", [
    "http://api.example.com/v1", "ftp://api.example.com", "https://localhost/v1",
    "https://127.0.0.1/v1", "https://10.0.0.1/v1", "https://[::1]/v1",
    "https://169.254.169.254/", "https://api.internal/v1", "https://service/v1",
    "https://user:never-echo-this@api.example.com/v1", "https://api.example.com/v1?key=secret",
    "https://api.example.com/v1#fragment", "https://api.example.com/v1?",
    "https://api.example.com:0/v1", "https://api.example.com:65536/v1",
    "https://api.example.com:/v1", "https://api.example.com\\@127.0.0.1/v1",
    " https://api.example.com/v1", "https://api.\nexample.com/v1",
    "http://127.1/v1", "https://2130706433/", "https://0177.0.0.1/",
])
def test_reject_unsafe_or_ambiguous_url(tmp_path, url):
    settings = Settings(Store(tmp_path))
    with pytest.raises(ValueError) as error:
        settings.save({"base_url": url})
    assert "never-echo-this" not in str(error.value)
    assert not settings.get()["configured"]


@pytest.mark.parametrize("url", ["https://api.example.com/v1/", "https://8.8.8.8:443/v1",
                                 "http://localhost:8000/v1", "http://127.0.0.1:11434/v1"])
def test_public_https_or_explicit_local_http(tmp_path, url):
    settings = Settings(Store(tmp_path))
    assert settings.save({"base_url": url})["base_url"] == url.rstrip("/")


def test_concurrent_patches_merge_and_unknown_schema_is_preserved(tmp_path):
    store = Store(tmp_path)
    Settings(store).save(CONFIG)
    barrier = threading.Barrier(2)

    def patch(values):
        settings = Settings(store)
        barrier.wait(timeout=5)
        settings.save(values)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(patch, [{"model": "replacement"}, {"rpm": 37}]))
    config = Settings(store).get()
    assert config["model"] == "replacement" and config["rpm"] == 37
    with store.connect() as db:
        db.execute("UPDATE model_settings SET version=99")
    settings = Settings(store)
    for operation in (settings.get, lambda: settings.save({"rpm": 42}), settings.resolve):
        with pytest.raises(ValueError, match="版本"):
            operation()
    with store.connect() as db:
        assert db.execute("SELECT version FROM model_settings").fetchone()[0] == 99
