#!/usr/bin/env python3
"""注册 Flow 引用的内置 Skill（coding / demo-generator）。"""
from __future__ import annotations

from pathlib import Path
from typing import List

from core import SkillCatalogService, utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"

FLOW_SKILLS = [
    {
        "id": "coding",
        "name": "编码实现",
        "description": "按 PRD/design 编写可运行代码，含测试与 git commit",
        "agents": ["rd_center", "product_center"],
        "content": """# 编码实现 Skill

## 适用场景
Flow dev 步：前端/后端实现、hotfix 修复。

## 步骤
1. 阅读 `design/` 或 PRD 与已有 artifacts
2. 在 `artifacts/{task_id}/` 下产出代码
3. 运行测试，确保通过
4. 调用 `report_done` 并列出产出路径

## 产出要求
- 可运行的源码与 `index.html`（前端）
- 测试通过或 `.tests_passed` 标记（演示环境）

## 禁止事项
- 不要提交 `.env` 或密钥文件
""",
    },
    {
        "id": "demo-generator",
        "name": "Demo 生成",
        "description": "生成 HTML mock 与设计说明，写入 design/",
        "agents": ["product_center"],
        "content": """# Demo 生成 Skill

## 适用场景
product_demo 步：从零生成可评审的 UI mock。

## 步骤
1. 阅读 idea_brief
2. 在 `artifacts/{task_id}/design/` 创建 `mock_a.html`、`mock_b.html`
3. 编写 `design_spec.md`
4. 写入 `selected.option` 默认选中方案

## 产出要求
- 至少一个 mock HTML
- design_spec.md 描述布局与组件
""",
    },
]


def ensure_flow_skills(skill_service: SkillCatalogService | None = None) -> List[str]:
    """确保 Flow 依赖 Skill 已注册；返回新建/更新的 skill id 列表。"""
    svc = skill_service or SkillCatalogService()
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    touched: List[str] = []
    for spec in FLOW_SKILLS:
        path = SKILLS_DIR / f"{spec['id']}.md"
        if not path.exists():
            path.write_text(spec["content"], encoding="utf-8")
        existing = svc.get_skill(spec["id"])
        if not existing:
            svc.add_local_skill(
                spec["id"], spec["name"], spec["description"], spec["agents"], spec["content"]
            )
            touched.append(spec["id"])
    return touched
