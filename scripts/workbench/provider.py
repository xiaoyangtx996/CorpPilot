"""Bounded text dispatch; HTTP has no retries, OpenCode owns its internal retries."""
from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import os
from pathlib import Path
from urllib.parse import urlsplit

from .settings import _base_url

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ProviderError(RuntimeError):
    """Safe public failure text. Never include upstream body or credentials."""

    def __init__(self, message, unknown=False, receipt=None):
        super().__init__(message)
        self.unknown = unknown
        self.receipt = receipt


def reply(config, snapshot):
    url = urlsplit(_base_url(config["base_url"]))
    messages = [{"role": "system", "content": snapshot["instructions"]}]
    actor_id = snapshot["agent"]["id"]
    for message in snapshot["messages"]:
        # Other members' messages are conversation data, never this agent's instructions.
        own = message["sender_kind"] == "agent" and message["sender_id"] == actor_id
        content = message["content"]
        if message["sender_kind"] == "agent" and not own:
            content = f"[群成员 {message['sender_id']}]\n{content}"
        messages.append({"role": "assistant" if own else "user", "content": content})
    body = json.dumps({"model": config["model"], "messages": messages,
                       "max_tokens": config["max_output_tokens"], "stream": False}).encode("utf-8")
    # The legacy SDK path logs raw provider errors and retries. This no-tool path
    # uses stdlib HTTP for bounded responses and a single observable attempt.
    connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=config["timeout_seconds"])
    try:
        connection.request("POST", url.path.rstrip("/") + "/chat/completions", body,
                           {"Content-Type": "application/json", "Authorization": "Bearer " + config["api_key"]})
        response = connection.getresponse()
        if response.status != 200:
            raise ProviderError(f"模型服务返回 HTTP {response.status}，未自动重试")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("模型响应超过大小上限")
    except ProviderError:
        raise
    except (TimeoutError, socket.timeout):
        raise ProviderError("模型请求超时，远端执行结果未知；未自动重试", unknown=True) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise ProviderError("无法完成模型请求，请检查连接与凭据；未自动重试", unknown=True) from None
    finally:
        connection.close()
    receipt = None
    try:
        result = json.loads(raw)
        usage = result.get("usage") or {}
        tokens = {}
        for field in ("prompt_tokens", "completion_tokens"):
            value = usage.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ProviderError("模型用量格式无效")
            tokens[field] = value
        model = result.get("model", config["model"])
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ProviderError("模型响应标识无效")
        if config.get('api_key') and config['api_key'] in model:
            model = '[模型标识已隐藏]'
        receipt = {"model": model, **tokens}
        choice = result["choices"][0]
        message = choice["message"]
        content = message["content"]
        if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("refusal"):
            raise ProviderError("模型未完整完成文本回复，请检查输出上限或模型状态")
        if not isinstance(content, str) or not content.strip() or len(content.strip()) > 16000:
            raise ProviderError("模型回复为空或超过消息上限")
        return {"content": content.strip(), "model": model, **tokens}
    except ProviderError as exc:
        exc.receipt = receipt
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ProviderError("模型响应格式无效", receipt=receipt) from None


def run_reply(config, snapshot):
    """A process deadline also bounds DNS/header trickling, unlike socket timeout."""
    if config.get('transport', 'http') == 'opencode':
        from .opencode_provider import run_reply as run_opencode_reply
        return run_opencode_reply(config, snapshot)
    script_dir = str(Path(__file__).resolve().parents[1])
    command = [sys.executable, "-I", "-c",
               "import sys; sys.path.insert(0, sys.argv[1]); from workbench.provider import main; main()", script_dir]
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}}
    try:
        result = subprocess.run(command, input=json.dumps({"config": config, "snapshot": snapshot}).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=config["timeout_seconds"],
                                env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ProviderError("模型调用超过总时限，本地请求进程已终止，远端结果未知", unknown=True) from None
    except OSError:
        raise ProviderError("无法启动模型请求进程") from None
    try:
        value = json.loads(result.stdout)
        if result.returncode:
            raise ProviderError(value["error"], unknown=value.get("unknown", False), receipt=value.get("receipt"))
        return value
    except (ValueError, KeyError, TypeError):
        raise ProviderError("模型请求进程异常退出，结果未知", unknown=True) from None


def main():
    try:
        request = json.load(sys.stdin)
        result = reply(request["config"], request["snapshot"])
    except ProviderError as exc:
        print(json.dumps({"error": str(exc), "unknown": exc.unknown, "receipt": exc.receipt}))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"error": "模型请求进程异常，结果未知", "unknown": True}))
        raise SystemExit(1)
    print(json.dumps(result))
