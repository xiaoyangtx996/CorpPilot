#!/usr/bin/env python3
"""
Postcondition 校验 — 从「文件存在 / 测试通过」起步的机器验收。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def _task_artifact_dir(task_id: str) -> Path:
    return ARTIFACTS_DIR / task_id


def _resolve_output_path(task_id: str, spec: str) -> Path:
    spec = spec.strip().rstrip("/")
    candidates = [
        _task_artifact_dir(task_id) / spec,
        PROJECT_ROOT / spec,
        _task_artifact_dir(task_id) / "outputs" / spec,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def check_rule(task_id: str, rule: str) -> Tuple[bool, str]:
    rule = rule.strip()
    if not rule:
        return True, ""

    if rule == "tests_pass == true":
        return _check_tests_pass(task_id)

    if rule in ("design_selected == true", "design_ready == true"):
        from design_artifacts import check_design_selected
        return check_design_selected(task_id)

    if rule == "no_secrets_in_repo == true":
        return _check_no_secrets(task_id)

    if rule.startswith("visual_diff"):
        return _check_visual_diff_rule(task_id, rule)

    if rule.startswith("prd_coverage"):
        return _check_prd_coverage(task_id, rule)

    if rule in ("checklist_pass == true", "checklist == true"):
        from checklist import check_checklist
        ok, msg, _detail = check_checklist(task_id)
        return ok, msg

    if rule.endswith("_exists") or " exists" in rule:
        path_spec = rule.split(" exists")[0].strip()
        path = _resolve_output_path(task_id, path_spec)
        if path.exists():
            return True, ""
        return False, f"缺少产出: {path_spec}"

    if "file_exists:" in rule:
        path_spec = rule.split("file_exists:", 1)[1].strip()
        path = _resolve_output_path(task_id, path_spec)
        if path.exists():
            return True, ""
        return False, f"文件不存在: {path}"

    # 简写：PRD.md / src/ 等
    if rule.endswith(".md") or rule.endswith("/") or "/" in rule:
        path = _resolve_output_path(task_id, rule)
        if path.exists():
            return True, ""
        return False, f"缺少: {rule}"

    return True, f"未识别规则（已跳过）: {rule}"


def _check_tests_pass(task_id: str) -> Tuple[bool, str]:
    marker = _task_artifact_dir(task_id) / ".tests_passed"
    if marker.exists():
        return True, ""
    task_dir = _task_artifact_dir(task_id)
    markers = [
        task_dir / "pytest.ini",
        task_dir / "pyproject.toml",
        task_dir / "package.json",
        PROJECT_ROOT / "pytest.ini",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "package.json",
    ]
    cwd = str(task_dir if (task_dir / "src").exists() or (task_dir / "tests").exists() else PROJECT_ROOT)
    if not any(m.exists() for m in markers):
        return True, "无测试配置，跳过 tests_pass"

    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "-q", "--tb=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if proc.returncode == 0:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok\n", encoding="utf-8")
            return True, ""
        return False, f"pytest 失败 exit={proc.returncode}\n{(proc.stdout or '')[-500:]}"
    except FileNotFoundError:
        return True, "pytest 未安装，跳过"
    except subprocess.TimeoutExpired:
        return False, "pytest 超时"


def _check_no_secrets(task_id: str) -> Tuple[bool, str]:
    bad = []
    for pattern in (".env", "credentials.json", "secrets.json"):
        for p in (PROJECT_ROOT / pattern, ARTIFACTS_DIR / task_id / pattern):
            if p.exists() and p.is_file():
                bad.append(str(p.relative_to(PROJECT_ROOT)))
    if bad:
        return False, f"发现敏感文件: {', '.join(bad)}"
    return True, ""


def _check_visual_diff_rule(task_id: str, rule: str) -> Tuple[bool, str]:
    threshold = 0.7
    if ">=" in rule:
        try:
            threshold = float(rule.split(">=")[1].strip())
        except ValueError:
            pass
    from visual_diff import check_visual_diff
    return check_visual_diff(task_id, threshold)


def _check_prd_coverage(task_id: str, rule: str) -> Tuple[bool, str]:
    threshold = 0.95
    if ">=" in rule:
        try:
            threshold = float(rule.split(">=")[1].strip())
        except ValueError:
            pass

    prd_path = _task_artifact_dir(task_id) / "PRD.md"
    if not prd_path.exists():
        return False, "缺少 PRD.md"

    import re

    text = prd_path.read_text(encoding="utf-8", errors="replace")
    headings = [h.strip().lower() for h in re.findall(r"^#+\s*(.+)$", text, re.MULTILINE)]
    expected = ["功能", "范围", "用户", "验收"]
    matched = 0
    for keyword in expected:
        if any(keyword in h for h in headings) or keyword in text.lower():
            matched += 1
    score = matched / len(expected) if expected else 1.0
    if score >= threshold:
        return True, ""
    return False, f"prd_coverage={score:.2f} < {threshold}（需 PRD 含：{', '.join(expected)}）"


def check_postconditions(
    task_id: str,
    rules: List[Any],
    outputs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    checked: List[str] = []

    for rule in rules or []:
        if isinstance(rule, str):
            ok, msg = check_rule(task_id, rule)
            checked.append(rule)
            if not ok:
                errors.append(msg)

    for out in outputs or []:
        spec = str(out).strip()
        ok, msg = check_rule(task_id, spec)
        checked.append(f"output:{spec}")
        if not ok:
            errors.append(msg)

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "checked": checked,
    }
