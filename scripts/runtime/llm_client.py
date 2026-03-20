"""
LLM Client — 多协议统一客户端
支持：OpenAI-compatible (OpenAI / 本地Ollama / 任意中转) + Anthropic 原生协议
"""
from __future__ import annotations

import os
import re
import time
import json
import threading
from typing import Any, Dict, List, Optional, Callable

# --------------------------------------------------------------------------- #
# 可选依赖（运行时懒加载）
# --------------------------------------------------------------------------- #
def _resolve_env(value: str) -> str:
    """展开 ${ENV_VAR} 形式的环境变量引用。"""
    def _repl(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{([^}]+)\}", _repl, str(value))


class ModelConfig:
    """单个模型的配置快照。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.provider: str = cfg.get("provider", "openai")
        self.model: str = cfg.get("model", "gpt-4o-mini")
        self.api_key: str = _resolve_env(cfg.get("api_key", ""))
        self.base_url: str = _resolve_env(
            cfg.get("base_url", "https://api.openai.com/v1")
        )
        self.max_tokens: int = cfg.get("max_tokens", 4096)
        self.temperature: float = cfg.get("temperature", 0.3)
        self.extra: Dict[str, Any] = {
            k: v
            for k, v in cfg.items()
            if k not in ("provider", "model", "api_key", "base_url", "max_tokens", "temperature")
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    def __repr__(self) -> str:
        return f"<ModelConfig provider={self.provider} model={self.model}>"


class LLMResponse:
    """统一的 LLM 响应封装。"""

    def __init__(
        self,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        stop_reason: str = "end_turn",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        raw: Any = None,
    ):
        self.content = content
        self.tool_calls: List[Dict[str, Any]] = tool_calls or []
        self.stop_reason = stop_reason
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.raw = raw
        self.model_name = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# --------------------------------------------------------------------------- #
# 核心 Client 实现
# --------------------------------------------------------------------------- #

class LLMClient:
    """
    统一 LLM 调用客户端。

    用法示例：
        client = LLMClient()
        resp = client.call(
            messages=[{"role": "user", "content": "你好"}],
            system="你是一名优秀的产品经理。",
            model_cfg=ModelConfig({...}),
        )
    """

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        on_token_usage: Optional[Callable[[str, int, int], None]] = None,
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # 回调：on_token_usage(model_name, prompt_tokens, completion_tokens)
        self.on_token_usage = on_token_usage
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------- #
    # 公开接口
    # ---------------------------------------------------------------------- #

    def call(
        self,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        model_cfg: Optional[Any] = None,
    ) -> LLMResponse:
        """调用 LLM，按照给定的路由尝试策略自动重试与降级，返回统一响应对象。"""
        if hasattr(model_cfg, "get_attempts"):
            attempts = model_cfg.get_attempts()
        elif model_cfg is not None:
            attempts = [model_cfg] * self.max_retries
        else:
            from .model_router import ModelConfig
            attempts = [ModelConfig({})] * self.max_retries

        last_error: Optional[Exception] = None
        for attempt_idx, cfg in enumerate(attempts):
            try:
                if cfg.provider == "anthropic":
                    resp = self._call_anthropic(messages, system, tools, cfg)
                else:
                    # 默认 OpenAI-compatible
                    resp = self._call_openai(messages, system, tools, cfg)
                
                resp.model_name = cfg.model

                if self.on_token_usage:
                    self.on_token_usage(
                        cfg.model,
                        resp.prompt_tokens,
                        resp.completion_tokens,
                    )
                return resp

            except Exception as exc:
                last_error = exc
                wait = self.retry_delay * (2 ** (attempt_idx % 3))  # 避免指数退避过长
                print(f"[LLMClient] 尝试使用 {cfg.model} 第 {attempt_idx+1} 次调用失败: {exc}，{wait:.1f}s 后继续…")
                import time
                time.sleep(wait)

        raise RuntimeError(
            f"LLM 调用失败，已重试 {len(attempts)} 次。最后错误: {last_error}"
        )

    # ---------------------------------------------------------------------- #
    # 私有：OpenAI-compatible
    # ---------------------------------------------------------------------- #

    def _call_openai(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        cfg: ModelConfig,
    ) -> LLMResponse:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "请先安装 openai SDK：pip install openai"
            )

        client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

        # 将 system 注入 messages 头部
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "messages": full_messages,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        result = client.chat.completions.create(**kwargs)
        choice = result.choices[0]
        msg = choice.message

        # 解析工具调用
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments or "{}"),
                })

        content = msg.content or ""
        usage = result.usage or type("U", (), {"prompt_tokens": 0, "completion_tokens": 0})()

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "end_turn",
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            raw=result,
        )

    # ---------------------------------------------------------------------- #
    # 私有：Anthropic 原生
    # ---------------------------------------------------------------------- #

    def _call_anthropic(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
        cfg: ModelConfig,
    ) -> LLMResponse:
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "请先安装 anthropic SDK：pip install anthropic"
            )

        client = anthropic.Anthropic(api_key=cfg.api_key)

        # Anthropic 的 tools 格式略有不同，做简单转换
        anthropic_tools = []
        if tools:
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    anthropic_tools.append({
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                    })

        kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "max_tokens": cfg.max_tokens,
            "system": system,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        result = client.messages.create(**kwargs)

        content_text = ""
        tool_calls = []
        for block in result.content:
            if hasattr(block, "text"):
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=result.stop_reason or "end_turn",
            prompt_tokens=result.usage.input_tokens,
            completion_tokens=result.usage.output_tokens,
            raw=result,
        )
