#!/usr/bin/env python3
"""design/ artifact 规范校验（M9）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def design_dir(task_id: str) -> Path:
    return ARTIFACTS_DIR / task_id / "design"


def validate_design_artifact(task_id: str) -> Dict[str, Any]:
    ddir = design_dir(task_id)
    errors: List[str] = []
    found: List[str] = []
    if not ddir.exists():
        return {"passed": False, "errors": ["design/ 目录不存在"], "found": []}
    for name in ("design_spec.md", "selected.option"):
        if (ddir / name).exists():
            found.append(name)
        else:
            errors.append(f"缺少 {name}")
    mocks = list(ddir.glob("mock*.html"))
    if not mocks:
        errors.append("缺少 mock HTML")
    else:
        found.extend([m.name for m in mocks])
    sel = ddir / "selected.option"
    if sel.exists():
        choice = sel.read_text(encoding="utf-8").strip()
        if choice and not (ddir / choice).exists() and not (ddir / f"{choice}.html").exists():
            errors.append(f"selected.option 无效: {choice}")
    return {"passed": not errors, "errors": errors, "found": found}


def check_design_selected(task_id: str) -> Tuple[bool, str]:
    r = validate_design_artifact(task_id)
    return r["passed"], "; ".join(r["errors"])
