#!/usr/bin/env python3
"""
Greenfield Flow 演示数据脚本 — 创建任务、播种 artifacts、推进至指定 step。

用法:
  python scripts/demo_greenfield.py                    # 创建 + 播种 artifacts
  python scripts/demo_greenfield.py --advance qa_gate  # 推进至 qa_gate 前
  python scripts/demo_greenfield.py --approve-gates    # 自动批准 gate 步骤
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import TaskPriority, TaskService, TaskStatus, TaskType, WorkflowEngine

ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"


def seed_greenfield_artifacts(task_id: str) -> None:
    """播种 greenfield 全流程演示产出。"""
    base = ARTIFACTS_DIR / task_id
    base.mkdir(parents=True, exist_ok=True)

    (base / "idea_brief.md").write_text(
        f"# 想法简报 — {task_id}\n\nCorpPilot 演示：从零到一产品开发。\n",
        encoding="utf-8",
    )

    design = base / "design"
    design.mkdir(exist_ok=True)
    mock_html = """<!DOCTYPE html>
<html><head><title>CorpPilot Demo</title></head>
<body><header><h1>CorpPilot</h1></header><nav><a href="#">Home</a></nav>
<main><p>企业智脑协同平台</p></main></body></html>"""
    (design / "mock_a.html").write_text(mock_html, encoding="utf-8")
    (design / "design_spec.md").write_text("布局：Header + Nav + Main\n", encoding="utf-8")
    (design / "selected.option").write_text("mock_a.html", encoding="utf-8")

    (base / "PRD.md").write_text(
        f"# PRD — {task_id}\n\n## 功能\n- Dashboard\n- Flow 编排\n\n## 范围\n\n## 用户\n\n## 验收\n",
        encoding="utf-8",
    )

    impl_html = mock_html.replace("</main>", "<section>实现区</section></main>")
    (base / "index.html").write_text(impl_html, encoding="utf-8")
    (base / ".tests_passed").write_text("ok\n", encoding="utf-8")

    from checklist import ensure_default_checklist

    ensure_default_checklist(task_id)

    src = base / "src"
    src.mkdir(exist_ok=True)
    (src / "app.py").write_text('print("demo")\n', encoding="utf-8")


def advance_to_step(wf: WorkflowEngine, task_id: str, target_step: str, *, max_iter: int = 48) -> dict:
    """推进 legacy 状态机至 executing，并 Flow 前进至 target_step。"""
    task = wf.task_service.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")

    for status in (
        TaskStatus.CLASSIFIED,
        TaskStatus.PLANNED,
        TaskStatus.REVIEWING,
        TaskStatus.APPROVED,
        TaskStatus.DISPATCHED,
        TaskStatus.EXECUTING,
    ):
        if TaskStatus(task["status"]) == status:
            continue
        try:
            task = wf.transition(task_id, status, "demo_greenfield")
        except ValueError:
            break

    fe = wf.flow_engine
    if not fe or not target_step:
        return task

    for _ in range(max_iter):
        task = wf.task_service.get_task(task_id) or task
        if task.get("flow_step_id") == target_step:
            break
        ctx = fe.get_flow_context(task)
        if ctx.get("gate_pending"):
            task = fe.approve_gate(task_id, "demo", "auto approve")
            continue
        try:
            task = fe.advance(task_id, actor="demo", force=True)
        except ValueError:
            break
        task = wf.task_service.get_task(task_id) or task
        if task.get("flow_step_id") == target_step:
            break
        try:
            fe.start_current_step(task_id, wf)
        except ValueError:
            pass
        task = wf.task_service.get_task(task_id) or task
        if task.get("flow_step_id") == "completed":
            break

    return wf.task_service.get_task(task_id) or task


# 兼容旧名
advance_workflow = advance_to_step


def main() -> None:
    parser = argparse.ArgumentParser(description="Greenfield Flow 演示")
    parser.add_argument("--title", default="Greenfield 演示 — CorpPilot 从零到一")
    parser.add_argument("--advance", default="", help="推进至指定 step_id，如 qa_gate")
    parser.add_argument("--approve-gates", action="store_true", help="自动批准当前 gate")
    parser.add_argument("--seed-only", action="store_true", help="仅播种 artifacts（需已有 task_id）")
    parser.add_argument("--task-id", default="", help="配合 --seed-only 指定任务 ID")
    parser.add_argument("--run-dev-cc", action="store_true", help="推进至 dev_parallel 并调用 Claude Code 后端")
    parser.add_argument("--real-cc", action="store_true", help="配合 --run-dev-cc 使用真实 Claude CLI")
    parser.add_argument("--run-supervisor", action="store_true", help="在 qa_gate 执行监督验收")
    parser.add_argument("--run-close", action="store_true", help="推进至 project_close 并生成结案报告")
    parser.add_argument("--ack-close", action="store_true", help="批准结案 Gate 并归档")
    parser.add_argument("--seed-traffic", action="store_true", help="写入模拟 traffic 日志（cost_report 演示）")
    args = parser.parse_args()

    os.environ.setdefault("CORPPILOT_AUTO_RUNTIME", "0")

    from bootstrap_skills import ensure_flow_skills
    ensure_flow_skills()

    ts = TaskService()
    wf = WorkflowEngine(ts, auto_runtime=False)

    if args.seed_only:
        if not args.task_id:
            print("错误: --seed-only 需要 --task-id")
            sys.exit(1)
        seed_greenfield_artifacts(args.task_id)
        print(json.dumps({"task_id": args.task_id, "seeded": True}, ensure_ascii=False, indent=2))
        return

    task = ts.create_task(
        title=args.title,
        task_type=TaskType.RD,
        priority=TaskPriority.P1,
        requester="ceo",
        description="Greenfield 演示任务 — 由 demo_greenfield.py 创建",
        flow_id="greenfield",
    )
    task_id = task["task_id"]
    seed_greenfield_artifacts(task_id)

    traffic_info = None
    if args.seed_traffic:
        from traffic_seed import seed_task_traffic
        traffic_info = seed_task_traffic(task_id)

    if args.advance:
        task = advance_workflow(wf, task_id, args.advance)
    elif args.approve_gates:
        fe = wf.flow_engine
        if fe:
            ctx = fe.get_flow_context(task)
            if ctx.get("gate_pending"):
                task = fe.approve_gate(task_id, "demo", "auto")

    supervisor_result = None
    close_result = None
    dev_cc_result = None
    if args.run_dev_cc:
        if args.real_cc:
            os.environ.pop("CORPPILOT_CLAUDE_DRY_RUN", None)
        else:
            os.environ["CORPPILOT_CLAUDE_DRY_RUN"] = "1"
        task = advance_workflow(wf, task_id, "dev_parallel")
        from runtime.execution_backends import ClaudeCodeBackend

        backend = ClaudeCodeBackend()
        cc = backend.run_sync(
            f"【任务 {task_id}】按 PRD 与 design/ 实现后端与前端，写入 artifacts/{task_id}/",
            task_id=task_id,
            timeout=120,
        )
        dev_cc_result = {
            "success": cc.success,
            "backend": cc.backend,
            "summary_preview": cc.summary[:300],
            "dry_run": not args.real_cc,
        }

    if args.run_supervisor:
        if task.get("flow_step_id") != "qa_gate":
            task = advance_workflow(wf, task_id, "qa_gate")
        fe = wf.flow_engine
        if fe:
            supervisor_result = fe.run_supervisor_step(task_id, wf, actor="demo", verdict="pass")
            task = supervisor_result

    if args.run_close or args.ack_close:
        if task.get("flow_step_id") != "project_close":
            task = advance_workflow(wf, task_id, "project_close")
        fe = wf.flow_engine
        if fe:
            close_result = fe.run_close_step(task_id, wf, actor="demo")
            task = close_result
        if args.ack_close and fe:
            task = fe.approve_gate(task_id, "demo", "ack close")
            if task.get("flow_step_id") == "completed":
                for status in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
                    try:
                        task = wf.transition(task_id, status, "demo")
                    except ValueError:
                        break

    from visual_diff import compute_visual_diff

    result = {
        "task_id": task_id,
        "status": task.get("status"),
        "flow_id": task.get("flow_id"),
        "flow_step_id": task.get("flow_step_id"),
        "flow_step_index": task.get("flow_step_index"),
        "artifacts_dir": str(ARTIFACTS_DIR / task_id),
        "visual_diff": compute_visual_diff(task_id, use_screenshot=False),
        "supervisor_ran": supervisor_result is not None,
        "closeout_ran": close_result is not None,
        "dev_cc": dev_cc_result,
        "traffic_seed": traffic_info,
        "dashboard": "打开 Dashboard → 任务中心 → 查看详情",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
