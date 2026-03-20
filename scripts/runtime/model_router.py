"""
Model Router — 层级化模型路由与模型池分发
按 Agent、岗位、部门、全局 四级优先级，解析并分配模型配置。
支持返回主模型与备用模型，以及重试次数。
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclass import dataclass
from typing import Any, Dict, List, Optional

from .llm_client import ModelConfig

_DEFAULT_CFG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "llm_config.json"


class RouteResult:
    """一次路由决策的结果，包含主模型、备用模型及重试策略。"""
    def __init__(self, primary: ModelConfig, fallback: Optional[ModelConfig], max_retries: int):
        self.primary = primary
        self.fallback = fallback
        self.max_retries = max_retries

    def get_attempts(self) -> List[ModelConfig]:
        """返回按顺序尝试的模型列表（主模型重试 max_retries 次，再试备用 1 次）。"""
        attempts = [self.primary] * self.max_retries
        if self.fallback:
            attempts.append(self.fallback)
        return attempts


class ModelRouter:
    """
    用法示例：
        router = ModelRouter()
        route = router.resolve("agent_risk", "risk_center", "risk_analyst", capability="chat")
    """

    def __init__(self, config_path: Optional[Path | str] = None):
        self.config_path = Path(config_path) if config_path else _DEFAULT_CFG_PATH
        self._config: Dict[str, Any] = {}
        self._load()

    # ---------------------------------------------------------------------- #
    # 公开接口
    # ---------------------------------------------------------------------- #

    def resolve(
        self,
        agent_id: str = "",
        department_id: str = "",
        role_id: str = "",
        capability: str = "chat",
    ) -> RouteResult:
        """
        按照 Agent -> Role -> Department -> Global 的顺序解析模型 ID。
        然后从 models 池中找出具体配置。
        """
        cfg = self._config
        primary_id = None

        # 1. Agent 级别覆盖
        agent_routes = cfg.get("agent_routes", {})
        if agent_id in agent_routes and capability in agent_routes[agent_id]:
            primary_id = agent_routes[agent_id][capability]

        # 2. 岗位级别覆盖
        if not primary_id:
            role_routes = cfg.get("role_routes", {})
            if role_id in role_routes and capability in role_routes[role_id]:
                primary_id = role_routes[role_id][capability]

        # 3. 部门级别覆盖
        if not primary_id:
            dept_routes = cfg.get("department_routes", {})
            if department_id in dept_routes and capability in dept_routes[department_id]:
                primary_id = dept_routes[department_id][capability]

        # 4. 全局路由默认
        global_route = cfg.get("global_routes", {}).get(capability, {})
        if not primary_id:
            primary_id = global_route.get("primary", "")

        fallback_id = global_route.get("fallback", "")
        max_retries = global_route.get("max_retries", 3)

        # 查字典
        models_pool = {m["id"]: m for m in cfg.get("models", [])}
        
        # 组装配置，缺少则返回空配置防止断言失败
        def _get_cfg(m_id: str) -> Optional[ModelConfig]:
            if not m_id or m_id not in models_pool:
                return None
            m = models_pool[m_id].copy()
            api_key = ""
            if "api_key_env" in m:
                import os
                api_key = os.environ.get(m["api_key_env"], "")
            m["api_key"] = api_key
            return ModelConfig(m)

        primary_cfg = _get_cfg(primary_id) or ModelConfig({"provider": "openai", "model": primary_id or "gpt-4o"})
        fallback_cfg = _get_cfg(fallback_id)

        return RouteResult(
            primary=primary_cfg,
            fallback=fallback_cfg,
            max_retries=max_retries,
        )

    def get_all_models(self) -> List[Dict[str, Any]]:
        return self._config.get("models", [])

    def get_traffic_config(self) -> Dict[str, Any]:
        return self._config.get("traffic", {})

    def get_pricing(self, model_name: str) -> Dict[str, float]:
        return (
            self._config.get("traffic", {})
            .get("token_pricing", {})
            .get(model_name, {"input_per_1k": 0.0, "output_per_1k": 0.0})
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._config)

    def save_config(self, new_config: Dict[str, Any]) -> None:
        """整体保存配置，供 Dashboard API 用"""
        self._config = new_config
        self._save()

    # ---------------------------------------------------------------------- #
    # 私有
    # ---------------------------------------------------------------------- #

    def _load(self) -> None:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        else:
            self._config = {"models": [], "global_routes": {}, "department_routes": {}, "role_routes": {}, "agent_routes": {}}

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

