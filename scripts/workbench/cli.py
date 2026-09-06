"""Fixed Codex CLI adapter for trusted local work; directories are not an OS sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import uuid

from .store import _text
from . import artifacts
from .tool_activities import parse_tools


class InputPreparationError(ValueError):
    """Workspace/input preparation failed before any CLI process was started."""


def prepare_workspace(data_dir: Path, execution_id: str, input_artifacts=None) -> dict[str, Path]:
    inputs = [] if input_artifacts is None else input_artifacts
    if not isinstance(inputs, list) or len(inputs) > artifacts.MAX_FILES:
        raise ValueError(artifacts.ERROR)
    verified, identities, total = [], set(), 0
    for item in inputs:
        if (not isinstance(item, dict)
                or set(item) != {"id", "execution_id", "path", "size", "sha256", "data"}
                or type(item["size"]) is not int or item["size"] < 0):
            raise ValueError(artifacts.ERROR)
        item = dict(item)
        identity = artifacts._identity(item["id"])
        artifacts._identity(item["execution_id"])
        if identity in identities:
            raise ValueError(artifacts.ERROR)
        identities.add(identity)
        item["content"] = item.pop("data")
        item = artifacts.verify_snapshot(item)
        total += item["size"]
        if total > artifacts.MAX_TOTAL_BYTES:
            raise ValueError(artifacts.ERROR)
        verified.append(item)
    if not isinstance(execution_id, str) or str(uuid.UUID(execution_id)) != execution_id:
        raise ValueError("执行 ID 必须是标准 UUID")
    base = Path(data_dir).absolute()
    # Redirected workspace paths cannot be treated as isolated run directories.
    for parent in (base, *base.parents):
        if parent.exists() and (parent.is_symlink() or getattr(parent.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("CLI 数据目录不能经过链接或重解析点")
    base.mkdir(parents=True, exist_ok=True)
    root = base / "execution-workspaces"
    root.mkdir(exist_ok=True)
    if root.is_symlink() or getattr(root.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("执行目录不能是链接或重解析点")
    run = root / execution_id
    run.mkdir()  # Existing execution directories are never silently reused or overwritten.
    paths = {"root": run, "work": run / "work", "home": run / "home", "tmp": run / "tmp"}
    paths.update(codex=paths["home"] / ".codex", appdata=paths["home"] / "AppData" / "Roaming",
                 localappdata=paths["home"] / "AppData" / "Local")
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    if verified:
        inputs_dir = paths["work"] / "inputs"
        inputs_dir.mkdir()
        for item in verified:
            # UUID filenames cannot become ambient executable configuration or instructions.
            with (inputs_dir / item["id"]).open("xb") as stream:
                stream.write(item["data"])
    return paths


def execution_environment(paths: dict[str, Path], api_key: str) -> dict[str, str]:
    key = _text(api_key, "CLI API 凭据", 4096)
    env = {name: value for name, value in os.environ.items()
           if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC"}}
    env.update(HOME=str(paths["home"]), USERPROFILE=str(paths["home"]),
               APPDATA=str(paths["appdata"]), LOCALAPPDATA=str(paths["localappdata"]),
               TEMP=str(paths["tmp"]), TMP=str(paths["tmp"]), CODEX_HOME=str(paths["codex"]),
               CODEX_API_KEY=key, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def parse_result(process: dict, api_key: str) -> dict:
    api_key = _text(api_key, "CLI API 凭据", 4096)
    result = {"success": False, "exit_code": process["exit_code"], "reason": process["reason"],
              "summary": "CLI 未产生已确认的完成结果", "usage": None,
              "tool_activities": parse_tools(process)}
    events = []
    try:
        events = [json.loads(line) for line in process['stdout'].decode('utf-8').splitlines() if line.strip()]
        completed = [event for event in events if isinstance(event, dict) and event.get('type') == 'turn.completed']
        # Only one observed turn is understood here; never guess multi-turn aggregation.
        if all(isinstance(event, dict) for event in events) and len(completed) == 1:
            usage = completed[0].get('usage')
            if isinstance(usage, dict):
                result['usage'] = {key: usage.get(key) if type(usage.get(key)) is int and usage[key] >= 0 else None
                                   for key in ('input_tokens', 'output_tokens', 'cached_input_tokens')}
                if result['usage']['input_tokens'] is not None and result['usage']['cached_input_tokens'] is not None and result['usage']['cached_input_tokens'] > result['usage']['input_tokens']:
                    result['usage']['cached_input_tokens'] = None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
        events = []
    if process["reason"] != "exited" or process["exit_code"] != 0:
        result["summary"] = {"cancelled": "CLI 已停止，请核查执行前已产生的副作用",
                             "timeout": "CLI 超时，请核查已产生的副作用",
                             "output_limit": "CLI 输出超过限制，已请求终止",
                             "start_failed": "CLI 无法启动，请检查工具和运行环境",
                             "unknown": "无法确认 CLI 进程树退出，结果未知"}.get(process["reason"], "CLI 返回非零退出码")
        return result
    result["reason"] = "protocol_error"
    try:
        if any(not isinstance(event, dict) for event in events):
            return result
        completed = [event for event in events if event.get("type") == "turn.completed"]
        if len(completed) != 1 or any(event.get("type") == "turn.failed" for event in events):
            return result
        messages = [event["item"].get("text") for event in events
                    if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
                    and event["item"].get("type") == "agent_message"]
        if not messages or not isinstance(messages[-1], str) or not messages[-1].strip():
            return result
        # A completion event must follow the final message, not precede a broken trailing turn.
        if events[-1].get("type") != "turn.completed":
            return result
        result.update(success=True, reason="exited", summary=messages[-1].strip().replace(api_key, "[凭据已隐藏]")[:2000])
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
        pass
    return result


def run_codex(executable: Path, data_dir: Path, execution_id: str, prompt: str,
              model: str, api_key: str, timeout_seconds: int, cancel=None, input_artifacts=None) -> dict:
    """No automatic login, ambient credentials, repository copying, or retry."""
    executable = Path(executable)
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("请选择存在的 Codex .exe 绝对路径")
    model = _text(model, "CLI 模型", 200)
    prompt = _text(prompt, "执行任务", 64000)
    api_key = _text(api_key, "CLI API 凭据", 4096)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError("CLI 总时限必须为1–3600秒")
    if cancel is not None and cancel.is_set():
        return {"success": False, "exit_code": None, "reason": "cancelled", "summary": "启动前已取消", "usage": None, "workspace": None}
    try:
        paths = prepare_workspace(data_dir, execution_id, input_artifacts)
    except (ValueError, OSError, TypeError) as exc:
        raise InputPreparationError("CLI 输入或工作目录准备失败，进程尚未启动") from exc
    env = execution_environment(paths, api_key)
    argv = [str(executable), "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
            "--sandbox", "workspace-write", "--skip-git-repo-check", "--color", "never", "--model", model,
            "-C", str(paths["work"]), "-c", 'shell_environment_policy.inherit="none"',
            "-c", "project_root_markers=[]", "-c", "allow_login_shell=false"]
    # Tool subprocesses get the run's paths, never its API key or arbitrary parent configuration.
    for name, value in env.items():
        if name != "CODEX_API_KEY":
            argv += ["-c", f"shell_environment_policy.set.{name}={json.dumps(value)}"]
    argv.append("-")
    from .process_tree import run_process
    process = run_process(argv, paths["work"], env, prompt.encode("utf-8"), timeout_seconds, cancel)
    result = parse_result(process, api_key)
    result["workspace"] = str(paths["work"])
    return result
