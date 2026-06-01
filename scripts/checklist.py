#!/usr/bin/env python3
"""交互验收 checklist — QA step 可选 postcondition。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"

DEFAULT_CHECKLIST = [
    {"id": "nav_present", "label": "导航区域存在", "selector": "nav"},
    {"id": "main_present", "label": "主内容区存在", "selector": "main"},
    {"id": "title_present", "label": "标题存在", "selector": "h1"},
]


def checklist_path(task_id: str) -> Path:
    return ARTIFACTS_DIR / task_id / "checklist.yaml"


def ensure_default_checklist(task_id: str) -> Path:
    path = checklist_path(task_id)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": DEFAULT_CHECKLIST}
    if yaml:
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        import json

        path = path.with_suffix(".json")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_checklist(task_id: str) -> List[Dict[str, Any]]:
    for ext in (".yaml", ".yml", ".json"):
        path = ARTIFACTS_DIR / task_id / f"checklist{ext}"
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        if ext == ".json":
            import json

            data = json.loads(raw)
        elif yaml:
            data = yaml.safe_load(raw) or {}
        else:
            continue
        items = data.get("items") or data.get("checklist") or []
        return list(items) if isinstance(items, list) else []
    return list(DEFAULT_CHECKLIST)


def _impl_html_path(task_id: str) -> Path:
    art = ARTIFACTS_DIR / task_id
    for name in ("index.html", "impl.html", "src/index.html"):
        p = art / name
        if p.exists():
            return p
    return art / "index.html"


def check_checklist(task_id: str) -> Tuple[bool, str, Dict[str, Any]]:
    items = load_checklist(task_id)
    impl = _impl_html_path(task_id)
    if not impl.exists():
        return False, "缺少实现 HTML（index.html）", {"passed": [], "failed": ["index.html"]}

    text = impl.read_text(encoding="utf-8", errors="replace").lower()
    passed: List[str] = []
    failed: List[str] = []
    for item in items:
        label = str(item.get("label") or item.get("id") or "item")
        selector = str(item.get("selector") or "").strip().lower()
        keyword = str(item.get("keyword") or selector or label).lower()
        ok = False
        if selector.startswith("<") or selector.endswith(">"):
            ok = selector.strip("<>/") in text
        elif selector:
            ok = f"<{selector}" in text or selector in text
        else:
            ok = keyword in text
        if ok:
            passed.append(label)
        else:
            failed.append(label)

    score = len(passed) / len(items) if items else 1.0
    detail = {
        "score": round(score, 3),
        "passed": passed,
        "failed": failed,
        "impl": str(impl.relative_to(PROJECT_ROOT)),
    }
    if failed:
        return False, f"checklist 未通过: {', '.join(failed)}", detail
    return True, "", detail
