#!/usr/bin/env python3
"""Skill 自进化提案（M6）含版本备份与回滚。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROPOSED_DIR = PROJECT_ROOT / "proposed_skills"
HISTORY_DIR = PROJECT_ROOT / "skills" / ".history"


def _archive_skill(skill_id: str, skill_service) -> Optional[str]:
    skill = skill_service.get_skill(skill_id)
    if not skill or skill.get("type") != "local" or not skill.get("path"):
        return None
    src = PROJECT_ROOT / skill["path"]
    if not src.exists():
        return None
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = HISTORY_DIR / f"{skill_id}_{ts}.md"
    shutil.copy2(src, dest)
    meta = HISTORY_DIR / f"{skill_id}_{ts}.json"
    meta.write_text(json.dumps({"skill_id": skill_id, "archived_at": ts, "path": str(dest)}, ensure_ascii=False), encoding="utf-8")
    return str(dest.relative_to(PROJECT_ROOT))


def list_proposals(status: Optional[str] = None) -> List[Dict[str, Any]]:
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, Any]] = []
    for p in sorted(PROPOSED_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if status and data.get("status") != status:
            continue
        out.append(data)
    return out


def create_proposal_from_task(task: Dict[str, Any]) -> Dict[str, Any]:
    tid = task["task_id"]
    pid = f"prop-{tid}"
    paths = [a.get("path", "") for a in (task.get("artifacts") or []) if isinstance(a, dict)]
    content = "\n".join([f"# 从 {tid} 蒸馏", "", task.get("description", ""), ""] + [f"- {x}" for x in paths[:12]])
    proposal = {
        "id": pid,
        "task_id": tid,
        "status": "pending",
        "name": f"经验 — {task.get('title', tid)[:36]}",
        "description": f"任务 {tid} 自动提案",
        "suggested_agents": [task.get("execution_owner", "rd_center")],
        "skill_id": f"learned_{tid.replace('-', '_').lower()}",
        "content": content,
    }
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROPOSED_DIR / f"{pid}.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    return proposal


def approve_proposal(proposal_id: str, skill_service) -> Dict[str, Any]:
    path = PROPOSED_DIR / f"{proposal_id}.json"
    if not path.exists():
        raise ValueError(f"提案不存在: {proposal_id}")
    proposal = json.loads(path.read_text(encoding="utf-8"))
    sid = proposal["skill_id"]
    _archive_skill(sid, skill_service)
    skill = skill_service.add_local_skill(
        sid, proposal["name"], proposal["description"],
        proposal.get("suggested_agents", ["rd_center"]), proposal["content"],
    )
    proposal["status"] = "approved"
    proposal["skill"] = skill
    proposal["version_archived"] = True
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    return proposal


def list_skill_versions(skill_id: str) -> List[Dict[str, Any]]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for meta in sorted(HISTORY_DIR.glob(f"{skill_id}_*.json"), reverse=True):
        try:
            out.append(json.loads(meta.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def rollback_skill(skill_id: str, skill_service, version_file: Optional[str] = None) -> Dict[str, Any]:
    versions = list_skill_versions(skill_id)
    if not versions:
        raise ValueError(f"无历史版本: {skill_id}")
    target = versions[0]
    if version_file:
        target = next((v for v in versions if version_file in v.get("path", "")), target)
    hist_path = PROJECT_ROOT / target["path"]
    if not hist_path.exists():
        raise ValueError(f"历史文件缺失: {hist_path}")
    content = hist_path.read_text(encoding="utf-8")
    _archive_skill(skill_id, skill_service)
    skill = skill_service.get_skill(skill_id)
    if not skill:
        raise ValueError(f"Skill 不存在: {skill_id}")
    skill_path = PROJECT_ROOT / str(skill.get("path", f"skills/{skill_id}.md"))
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content, encoding="utf-8")
    skill_service.update_skill(skill_id, updated_at=datetime.now().isoformat(timespec="microseconds"))
    return skill_service.get_skill(skill_id) or skill


def reject_proposal(proposal_id: str, reason: str = "") -> Dict[str, Any]:
    path = PROPOSED_DIR / f"{proposal_id}.json"
    if not path.exists():
        raise ValueError(f"提案不存在: {proposal_id}")
    proposal = json.loads(path.read_text(encoding="utf-8"))
    proposal["status"] = "rejected"
    proposal["reject_reason"] = reason
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    return proposal
