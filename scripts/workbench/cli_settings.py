"""Secret-free CLI settings and an explicit, credential-free version probe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

from .cli import execution_environment, prepare_workspace
from .process_tree import run_process

LIMITS = {"timeout_seconds": (1, 3600), "max_concurrency": (1, 16),
          "docker_cpus": (1, 16), "docker_memory_mb": (128, 32768), "docker_pids_limit": (16, 1024),
          "host_reserve_memory_mb": (0, 1048576), "local_worker_memory_mb": (128, 1048576), "local_worker_cpus": (1, 256)}
DEFAULTS = {"enabled": False, "executable": "", "model": "", "api_key_env": "",
            "timeout_seconds": 120, "max_concurrency": 2, "backend": "local",
            "docker_executable": "", "docker_image": "", "docker_cpus": 1,
            "docker_memory_mb": 1024, "docker_pids_limit": 128,
            "resource_admission_enabled": False, "host_reserve_memory_mb": 1024,
            "local_worker_memory_mb": 1024, "local_worker_cpus": 1}
# ponytail: one probe per server process; use shared admission if multiple servers are supported.
_PROBE_LOCK = threading.Lock()


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) - DEFAULTS.keys():
        raise ValueError("包含不支持的 CLI 配置字段；只允许保存密钥环境变量名")
    values = dict(payload)
    for field, value in values.items():
        if field in ("enabled", "resource_admission_enabled"):
            if type(value) is not bool:
                raise ValueError(f"{field} 必须为布尔值")
        elif field in LIMITS:
            low, high = LIMITS[field]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{field} 必须为 {low}–{high} 的整数")
        else:
            if (not isinstance(value, str) or len(value) > (32767 if field in ("executable", "docker_executable") else 200)
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError(f"{field} 必须为有效文本")
            if field == "api_key_env":
                if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
                    raise ValueError("api_key_env 必须为有效的环境变量名")
            elif field in ("executable", "docker_executable"):
                if value and (not Path(value).is_absolute() or Path(value).suffix.lower() != ".exe"):
                    raise ValueError("请选择 CLI .exe 的绝对路径")
            elif field == "backend":
                if value not in ("local", "docker"):
                    raise ValueError("backend 必须为 local 或 docker")
            elif field == "docker_image":
                if value and not re.fullmatch(r"(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}", value):
                    raise ValueError("Docker 镜像必须固定为 sha256 ID 或 repo@sha256 摘要")
            else:
                values[field] = value.strip()
    return values


class CLISettings:
    def __init__(self, store):
        self.store = store
        self.defaults = {**DEFAULTS, "executable": shutil.which("codex") or ""}
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cli_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                config TEXT NOT NULL)""")

    @staticmethod
    def _read(db):
        row = db.execute("SELECT version,config FROM cli_settings WHERE id=1").fetchone()
        if row is None:
            return {}
        if row["version"] != 1:
            raise ValueError("不支持的 CLI 配置版本，请使用匹配版本的软件")
        try:
            return _validate(json.loads(row["config"]))
        except (ValueError, TypeError):
            raise ValueError("已保存的 CLI 配置无效") from None

    def _status(self, values):
        config = {**self.defaults, **values}
        docker = config["backend"] == "docker"
        exe = Path(config["docker_executable"] if docker else config["executable"])
        return {**config,
                "configured": all(config[field] for field in (("docker_executable", "docker_image", "model", "api_key_env") if docker else ("executable", "model", "api_key_env"))),
                "credential_available": bool(os.environ.get(config["api_key_env"], "").strip()),
                "executable_available": exe.is_absolute() and exe.suffix.lower() == ".exe" and exe.is_file(),
                "platform_supported": os.name == "nt"}

    def get(self):
        with self.store.connect() as db:
            return self._status(self._read(db))

    @staticmethod
    def _require_ready(status):
        if not status["configured"]:
            raise ValueError("启用前必须填写完整 CLI 配置")
        if not status["platform_supported"]:
            raise ValueError("CLI 执行当前仅支持 Windows")
        if not status["executable_available"]:
            raise ValueError("请选择存在的 CLI .exe 绝对路径")

    def save(self, payload):
        patch = _validate(payload)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {**self._read(db), **patch}
            status = self._status(values)
            if status["enabled"]:
                self._require_ready(status)
            db.execute("""INSERT INTO cli_settings(id,version,config) VALUES(1,1,?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config""", (json.dumps(values),))
            return status

    def resolve(self):
        """Internal only: never serialize or log the resolved API key."""
        config = self.get()
        if not config["enabled"]:
            raise ValueError("CLI 尚未启用")
        self._require_ready(config)
        key = os.environ.get(config["api_key_env"], "")
        if not key.strip():
            raise ValueError("CLI 密钥环境变量未设置")
        return {field: config[field] for field in DEFAULTS} | {"api_key": key}

    def probe(self):
        unavailable = {"available": False, "version": None}
        if not _PROBE_LOCK.acquire(blocking=False):
            return {**unavailable, "message": "CLI 状态检查正在进行，请稍后重试"}
        try:
            config = self.get()
            if not config["platform_supported"]:
                return {**unavailable, "message": "CLI 状态检查当前仅支持 Windows"}
            if not config["executable_available"]:
                return {**unavailable, "message": "请选择并保存存在的 CLI .exe 绝对路径"}
            with tempfile.TemporaryDirectory(prefix="corppilot-cli-probe-") as directory:
                paths = prepare_workspace(Path(directory), str(uuid.uuid4()))
                env = execution_environment(paths, "probe-placeholder")
                env.pop("CODEX_API_KEY", None)
                if config["backend"] == "docker":
                    return self._probe_docker(config, paths, env)
                process = run_process([config["executable"], "--version"], paths["work"], env,
                                      b"", 10, output_limit_bytes=65536)
            version = re.fullmatch(rb"codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)",
                                   process["stdout"].strip())
            if process["reason"] == "exited" and process["exit_code"] == 0 and version:
                return {"available": True, "version": version[1].decode("ascii"),
                        "message": "Codex CLI 可用；仅检查版本，未调用模型"}
            message = {"timeout": "CLI 状态检查超时", "output_limit": "CLI 状态检查输出超过限制",
                       "start_failed": "CLI 无法启动，请检查工具和运行环境"}.get(
                           process["reason"], "未识别到有效的 Codex CLI 版本")
            return {**unavailable, "message": message}
        except (OSError, ValueError):
            return {**unavailable, "message": "CLI 状态检查失败，请检查配置和运行环境"}
        finally:
            _PROBE_LOCK.release()

    @staticmethod
    def _probe_docker(config, paths, env):
        unavailable = {"available": False, "version": None,
                       "message": "Docker 本地服务或固定镜像不可用；未拉取镜像、未调用模型"}
        if not config["docker_image"]:
            return unavailable
        # Explicit local pipe and empty config prevent ambient remote contexts and credentials.
        env = {key: value for key, value in env.items() if not key.upper().startswith("DOCKER_")}
        directory = paths["work"] / "docker-config"
        directory.mkdir()
        argv = [config["docker_executable"], "--host", "npipe:////./pipe/docker_engine", "--config", str(directory)]
        deadline = time.monotonic() + 10
        budget = 65536
        outputs = []
        for args in (["info", "--format", '{"ServerVersion":{{json .ServerVersion}},"OSType":{{json .OSType}}}'],
                     ["image", "inspect", "--format", '{"Id":{{json .Id}},"Os":{{json .Os}}}', config["docker_image"]]):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or budget <= 0:
                return unavailable
            result = run_process(argv + args, paths["work"], env, b"", remaining, output_limit_bytes=budget)
            budget -= len(result["stdout"]) + len(result["stderr"])
            if result["reason"] != "exited" or result["exit_code"] != 0:
                return unavailable
            try:
                output = json.loads(result["stdout"])
            except (ValueError, UnicodeError):
                return unavailable
            if not isinstance(output, dict):
                return unavailable
            outputs.append(output)
        version, identity = outputs[0].get('ServerVersion'), outputs[1].get('Id')
        if outputs[0].get('OSType') != 'linux' or outputs[1].get('Os') != 'linux':
            return unavailable
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version):
            return unavailable
        if not isinstance(identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            return unavailable
        if config["docker_image"].startswith("sha256:") and identity != config["docker_image"]:
            return unavailable
        return {"available": True, "version": version,
                "message": "Docker 本地服务与固定镜像可用；未拉取镜像、未调用模型"}
