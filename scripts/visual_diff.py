#!/usr/bin/env python3
"""HTML 视觉相似度：文本 + 标签结构 + Playwright 截图（可选）。"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
VIEWPORT = {"width": 1280, "height": 720}


def _strip_html(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return " ".join(html.split()).lower()


def _extract_tag_set(html: str) -> set:
    return set(re.findall(r"<(\w+)", html, flags=re.I))


def _read_raw(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_text(path: Path) -> str:
    return _strip_html(_read_raw(path))


def _find_mock(task_id: str) -> Optional[Path]:
    ddir = ARTIFACTS_DIR / task_id / "design"
    if not ddir.exists():
        return None
    sel = ddir / "selected.option"
    if sel.exists():
        name = sel.read_text(encoding="utf-8").strip()
        for c in (ddir / name, ddir / f"{name}.html", ddir / f"mock_{name}.html"):
            if c.exists():
                return c
    mocks = sorted(ddir.glob("mock*.html"))
    return mocks[0] if mocks else None


def _find_implementation(task_id: str) -> Optional[Path]:
    base = ARTIFACTS_DIR / task_id
    for rel in ("src/index.html", "index.html", "demo/index.html", "frontend/index.html"):
        p = base / rel
        if p.exists():
            return p
    for p in sorted(base.rglob("index.html")):
        if "design" not in p.parts:
            return p
    return None


def _screenshot_dir(task_id: str) -> Path:
    return ARTIFACTS_DIR / task_id / "visual_diff"


def capture_html_screenshot(html_path: Path, out_png: Path) -> bool:
    """Playwright 无头截图；未安装 playwright 时返回 False。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    if not html_path.exists():
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport=VIEWPORT)
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=30000)
            page.screenshot(path=str(out_png), full_page=True)
            browser.close()
        return out_png.exists()
    except Exception:
        return False


def compare_images(path_a: Path, path_b: Path) -> Optional[float]:
    """PNG 相似度 0~1；需要 Pillow。"""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    if not path_a.exists() or not path_b.exists():
        return None
    img_a = Image.open(path_a).convert("RGB")
    img_b = Image.open(path_b).convert("RGB")
    if img_a.size != img_b.size:
        img_b = img_b.resize(img_a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(img_a, img_b).convert("L")
    hist = diff.histogram()
    total = sum(hist)
    if total == 0:
        return 1.0
    mean_diff = sum(i * count for i, count in enumerate(hist)) / total / 255.0
    return round(max(0.0, 1.0 - mean_diff), 4)


def _screenshot_scores(task_id: str, mock: Path, impl: Path) -> Tuple[Optional[float], Dict[str, str]]:
    """尝试截图对比，返回 (score, paths_dict)。"""
    out_dir = _screenshot_dir(task_id)
    mock_png = out_dir / "mock.png"
    impl_png = out_dir / "impl.png"
    if not capture_html_screenshot(mock, mock_png):
        return None, {}
    if not capture_html_screenshot(impl, impl_png):
        return None, {}
    score = compare_images(mock_png, impl_png)
    paths = {
        "mock_screenshot": str(mock_png.relative_to(PROJECT_ROOT)),
        "impl_screenshot": str(impl_png.relative_to(PROJECT_ROOT)),
    }
    return score, paths


def compute_visual_diff(task_id: str, *, use_screenshot: bool = True) -> Dict[str, Any]:
    mock = _find_mock(task_id)
    impl = _find_implementation(task_id)
    if not mock:
        return {"score": 0.0, "passed": False, "error": "未找到 design mock", "mock": None, "impl": None, "mode": "none"}
    if not impl:
        return {"score": 0.0, "passed": False, "error": "未找到实现 HTML", "mock": str(mock), "impl": None, "mode": "none"}

    a, b = _read_text(mock), _read_text(impl)
    if not a or not b:
        return {"score": 0.0, "passed": False, "error": "HTML 内容为空", "mock": str(mock), "impl": str(impl), "mode": "none"}

    text_score = SequenceMatcher(None, a, b).ratio()
    mock_tags = _extract_tag_set(_read_raw(mock))
    impl_tags = _extract_tag_set(_read_raw(impl))
    union = mock_tags | impl_tags
    tag_score = (len(mock_tags & impl_tags) / len(union)) if union else 1.0

    screenshot_score: Optional[float] = None
    screenshot_paths: Dict[str, str] = {}
    if use_screenshot:
        screenshot_score, screenshot_paths = _screenshot_scores(task_id, mock, impl)

    if screenshot_score is not None:
        score = round(0.35 * text_score + 0.15 * tag_score + 0.5 * screenshot_score, 4)
        mode = "hybrid"
    else:
        score = round(0.6 * text_score + 0.4 * tag_score, 4)
        mode = "text"

    result: Dict[str, Any] = {
        "score": score,
        "text_score": round(text_score, 4),
        "tag_score": round(tag_score, 4),
        "passed": score >= 0.0,
        "mode": mode,
        "mock": str(mock.relative_to(PROJECT_ROOT)),
        "impl": str(impl.relative_to(PROJECT_ROOT)),
    }
    if screenshot_score is not None:
        result["screenshot_score"] = screenshot_score
        result.update(screenshot_paths)
    return result


def check_visual_diff(task_id: str, threshold: float = 0.7) -> Tuple[bool, str]:
    r = compute_visual_diff(task_id)
    if r.get("error"):
        return False, str(r["error"])
    score = float(r.get("score", 0))
    if score >= threshold:
        return True, ""
    extra = ""
    if r.get("screenshot_score") is not None:
        extra = f", screenshot={r['screenshot_score']:.2%}"
    return False, (
        f"visual_diff={score:.2%} < {threshold:.0%}{extra} "
        f"(mock={r.get('mock')}, impl={r.get('impl')}, mode={r.get('mode')})"
    )
