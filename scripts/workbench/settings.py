"""Explicit, secret-free model settings in the existing workbench database."""
from __future__ import annotations

import ipaddress
import json
import os
import re
from urllib.parse import urlsplit


LIMITS = {"max_output_tokens": (1, 131072), "timeout_seconds": (1, 300),
          "rpm": (1, 600), "max_concurrency": (1, 16)}
DEFAULTS = {"enabled": False, "model": "", "base_url": "", "api_key_env": "",
            **{field: None for field in LIMITS}}


def _base_url(value):
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in "\\?#")):
        raise ValueError("base_url 必须是无查询参数或片段的有效 URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("base_url 无效") from None
    if (not host or parsed.username is not None or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535) or parsed.netloc.endswith(":")):
        raise ValueError("base_url 主机或端口无效，不允许 URL 凭据")
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}:
        return value.rstrip("/")
    if parsed.scheme != "https":
        raise ValueError("公网 API 必须使用 HTTPS；HTTP 只允许 localhost 或 127.0.0.1")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (len(labels) < 2 or len(host) > 253 or not re.fullmatch(r"[A-Za-z]{2,63}", labels[-1])
                or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                       for label in labels)
                or labels[-1] in {"local", "localhost", "internal", "lan", "home", "test", "invalid"}):
            raise ValueError("HTTPS 地址必须使用公网主机名或 IP")
    else:
        if not address.is_global:
            raise ValueError("HTTPS 地址必须使用公网 IP")
    return value.rstrip("/")


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) - DEFAULTS.keys():
        raise ValueError("包含不支持的模型配置字段；只允许保存密钥环境变量名")
    values = dict(payload)
    for field, value in values.items():
        if field == "enabled":
            if type(value) is not bool:
                raise ValueError("enabled 必须为布尔值")
        elif field in LIMITS:
            low, high = LIMITS[field]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{field} 必须为 {low}–{high} 的整数")
        elif field == "base_url":
            values[field] = _base_url(value)
        elif field == "api_key_env":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
                raise ValueError("api_key_env 必须为有效的环境变量名")
        elif (not isinstance(value, str) or not value.strip() or len(value) > 200
              or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError("model 必须为 1–200 字符的模型标识")
        else:
            values[field] = value.strip()
    return values


class Settings:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            # ponytail: one settings record needs no migration framework. Bump this
            # record's version and explicitly migrate here when its format changes.
            db.execute("""CREATE TABLE IF NOT EXISTS model_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                config TEXT NOT NULL)""")

    @staticmethod
    def _read(db):
        row = db.execute("SELECT version,config FROM model_settings WHERE id=1").fetchone()
        if row is None:
            return {}
        if row["version"] != 1:
            raise ValueError("不支持的模型配置版本，请使用匹配版本的软件")
        try:
            return _validate(json.loads(row["config"]))
        except (ValueError, TypeError):
            raise ValueError("已保存的模型配置无效") from None

    @staticmethod
    def _status(values):
        config = {**DEFAULTS, **values}
        return {**config, "configured": all(config[field] for field in DEFAULTS if field != "enabled"),
                "credential_available": bool(os.environ.get(config["api_key_env"], "").strip())}

    def get(self):
        with self.store.connect() as db:
            return self._status(self._read(db))

    def save(self, payload):
        patch = _validate(payload)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {**self._read(db), **patch}
            status = self._status(values)
            if status["enabled"] and not status["configured"]:
                raise ValueError("启用前必须填写完整模型配置")
            db.execute("""INSERT INTO model_settings(id,version,config) VALUES(1,1,?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config""", (json.dumps(values),))
            return status

    def resolve(self):
        """Internal only: callers must never serialize or log this credential."""
        config = self.get()
        if not config["enabled"] or not config["configured"]:
            raise ValueError("模型尚未完整配置并启用")
        key = os.environ.get(config["api_key_env"], "")
        if not key.strip():
            raise ValueError("模型密钥环境变量未设置")
        return {field: config[field] for field in DEFAULTS} | {"api_key": key}
