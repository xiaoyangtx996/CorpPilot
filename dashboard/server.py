#!/usr/bin/env python3
"""
CorpPilot Dashboard Server
统一暴露任务、董事会、Agent 和 Skill 的 HTTP API。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import (  # noqa: E402
    AgentMonitorService,
    AgentCatalogService,
    BoardRoom,
    DecisionType,
    EventLogService,
    ExecutionService,
    SkillCatalogService,
    TaskPriority,
    TaskService,
    TaskStatus,
    TaskType,
    VoteResult,
    WorkflowEngine,
)


class CorpPilotAPI(BaseHTTPRequestHandler):
    """CorpPilot API 处理器。"""

    task_service = TaskService()
    workflow = WorkflowEngine(task_service)
    board_room = BoardRoom()
    agent_service = AgentCatalogService()
    skill_service = SkillCatalogService()
    agent_monitor = AgentMonitorService(task_service, agent_service)
    event_log = EventLogService(task_service.data_dir)
    execution_service = ExecutionService(workflow)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {fmt % args}")

    def send_json_response(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_response(self, message: str, status: int = 400) -> None:
        self.send_json_response({"error": message}, status)

    def parse_int_query(self, query: Dict[str, Any], key: str, default: int, minimum: int = 0) -> int:
        raw_value = query.get(key, [default])[0]
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid query parameter: {key}") from exc
        if value < minimum:
            raise ValueError(f"Invalid query parameter: {key}")
        return value

    def read_request_json(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        if not raw_body.strip():
            return {}
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON") from exc

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)


        if path == "/api/flows":
            self.handle_list_flows()
            return
        if path.startswith("/api/flows/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[-1] == "export":
                self.handle_export_flow(parts[-2])
            else:
                self.handle_get_flow_detail(path.split("/")[-1])
            return
        if path == "/api/tasks":
            self.handle_get_tasks(query)
            return
        if path.startswith("/api/tasks/"):
            segments = path.split("/")
            if path.endswith("/timeline"):
                self.handle_get_task_timeline(segments[-2])
            elif path.endswith("/artifacts"):
                self.handle_get_task_artifacts(segments[-2])
            elif path.endswith("/flow"):
                self.handle_get_task_flow(segments[-2])
            elif path.endswith("/cost_report"):
                self.handle_get_cost_report(segments[-2])
            elif path.endswith("/design/validate"):
                self.handle_validate_design(segments[-3])
            elif path.endswith("/visual-diff"):
                self.handle_visual_diff(segments[-2])
            elif path.endswith("/postcondition"):
                self.handle_check_postcondition(segments[-2])
            else:
                self.handle_get_task(segments[-1])
            return
        if path == "/api/agents":
            self.send_json_response({"agents": self.agent_monitor.list_health()})
            return
        if path.startswith("/api/agents/"):
            agent = self.agent_service.get_agent(path.split("/")[-1])
            if agent:
                self.send_json_response(agent)
            else:
                self.send_error_response("Agent not found", 404)
            return
        if path == "/api/skills":
            self.send_json_response({"skills": self.skill_service.list_skills(query.get("agent", [None])[0])})
            return
        if path == "/api/skills/proposals":
            self.handle_list_skill_proposals(query)
            return
        if path.startswith("/api/skills/") and path.endswith("/versions"):
            self.handle_skill_versions(path.split("/")[3])
            return
        if path.startswith("/api/skills/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[3] not in ("proposals",):
                self.handle_get_skill(parts[3])
                return
        if path == "/api/stats":
            self.send_json_response(self.task_service.get_stats())
            return
        if path == "/api/events":
            self.handle_get_events(query)
            return
        if path == "/api/health":
            self.send_json_response({"status": "healthy", "timestamp": datetime.now().isoformat(timespec="seconds")})
            return
        if path == "/api/board/proposals":
            self.send_json_response({"proposals": self.board_room.list_proposals(query.get("status", [None])[0])})
            return
        if path == "/api/board/summary":
            self.send_json_response(self.board_room.get_summary())
            return
        if path.startswith("/api/board/proposals/"):
            proposal = self.board_room.get_proposal(path.split("/")[-1])
            if proposal:
                self.send_json_response(proposal)
            else:
                self.send_error_response("Proposal not found", 404)
            return
        if path == "/api/board/members":
            members = {key: value.__dict__ for key, value in BoardRoom.MEMBERS.items()}
            self.send_json_response({"members": members})
            return

        if path == "/api/departments":
            self.send_json_response(self.agent_monitor.get_department_health())
            return
        if path.startswith("/api/departments/") and path.endswith("/skills"):
            self.handle_get_department_skills(path.split("/")[3])
            return
        if path == "/api/models":
            self.handle_get_models()
            return
        if path == "/api/traffic":
            self.handle_get_traffic(query)
            return
        if path == "/api/traffic/export":
            self.handle_export_traffic()
            return
        if path.startswith("/api/run/logs"):
            self.handle_sse_logs()
            return
        if path.startswith("/artifacts/"):
            self.serve_static_artifact(path)
            return
        if path in {"/", "/dashboard"}:
            self.serve_dashboard()
            return
        self.send_error_response("Not Found", 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            data = self.read_request_json()
        except ValueError as exc:
            self.send_error_response(str(exc))
            return


        if path == "/api/models":
            self.handle_post_models(data)
            return
        if path == "/api/run/task":
            self.handle_run_task(data)
            return
        if path == "/api/flows/import":
            self.handle_import_flow(data)
            return
        if path == "/api/skills":
            self.handle_create_skill(data)
            return
        if path == "/api/tasks":
            self.handle_create_task(data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/status"):
            self.handle_update_task_status(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/intervene"):
            self.handle_intervene_task(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/execute/start"):
            self.handle_execute_start(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/execute/complete"):
            self.handle_execute_complete(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/execute/block"):
            self.handle_execute_block(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/flow/advance"):
            self.handle_flow_advance(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/gate/approve"):
            self.handle_gate_approve(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/flow/skip"):
            self.handle_flow_skip(path.split("/")[3], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/flow/save-as-template"):
            self.handle_save_task_as_flow(path.split("/")[3], data)
            return
        if "/supervisor/" in path and path.endswith("/verdict"):
            parts = path.split("/")
            # /api/tasks/{id}/supervisor/{step}/verdict
            if len(parts) >= 6:
                self.handle_supervisor_verdict(parts[3], parts[5], data)
            return
        if path == "/api/board/proposals":
            self.handle_create_proposal(data)
            return
        if path.startswith("/api/board/proposals/") and path.endswith("/discuss"):
            self.handle_add_discussion(path.split("/")[4], data)
            return
        if path.startswith("/api/board/proposals/") and path.endswith("/vote"):
            self.handle_cast_vote(path.split("/")[4], data)
            return
        if path.startswith("/api/board/proposals/") and path.endswith("/tally"):
            self.handle_tally_votes(path.split("/")[4])
            return
        if path.startswith("/api/board/proposals/") and path.endswith("/order"):
            self.handle_direct_order(path.split("/")[4], data)
            return
        if path.startswith("/api/tasks/") and path.endswith("/close/run"):
            self.handle_run_project_close(path.split("/")[3], data)
            return
        if path.startswith("/api/skills/proposals/") and path.endswith("/approve"):
            self.handle_approve_skill_proposal(path.split("/")[4], data)
            return
        if path.startswith("/api/skills/proposals/") and path.endswith("/reject"):
            self.handle_reject_skill_proposal(path.split("/")[4], data)
            return
        if path.startswith("/api/skills/") and path.endswith("/rollback"):
            self.handle_skill_rollback(path.split("/")[3], data)
            return
        self.send_error_response("Not Found", 404)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            data = self.read_request_json()
        except ValueError as exc:
            self.send_error_response(str(exc))
            return

        if path.startswith("/api/tasks/"):
            self.handle_update_task(path.split("/")[-1], data)
            return
        if path.startswith("/api/skills/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[3] not in ("proposals",):
                self.handle_update_skill(parts[3], data)
                return
        self.send_error_response("Not Found", 404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tasks/"):
            self.handle_delete_task(path.split("/")[-1])
            return
        self.send_error_response("Not Found", 404)

    def handle_get_tasks(self, query: Dict[str, Any]) -> None:
        tasks = self.task_service.list_tasks(limit=10000)
        status = query.get("status", [None])[0]
        task_type = query.get("type", [None])[0]
        priority = query.get("priority", [None])[0]
        if status:
            tasks = [task for task in tasks if task["status"] == status]
        if task_type:
            tasks = [task for task in tasks if task["type"] == task_type]
        if priority:
            tasks = [task for task in tasks if task["priority"] == priority]
        try:
            limit = self.parse_int_query(query, "limit", 20)
            offset = self.parse_int_query(query, "offset", 0)
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        fe = self.workflow.flow_engine
        if fe:
            for task in tasks:
                if task.get("flow_id") and task.get("flow_id") != "legacy":
                    ctx = fe.get_flow_context(task)
                    task["flow"] = {
                        "gate_pending": ctx.get("gate_pending"),
                        "flow_step_id": ctx.get("flow_step_id"),
                        "step_index": ctx.get("step_index"),
                        "step_total": ctx.get("step_total"),
                    }
        self.send_json_response({"total": len(tasks), "tasks": tasks[offset : offset + limit]})

    def handle_get_task(self, task_id: str) -> None:
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        task = dict(task)
        task["routing"] = self.workflow.routing_snapshot(task_id)
        if self.workflow.flow_engine:
            task["flow"] = self.workflow.flow_engine.get_flow_context(task)
        self.send_json_response(task)

    def handle_list_flows(self) -> None:
        try:
            from flow_engine import list_flow_ids, summarize_flow

            flows = []
            for fid in list_flow_ids():
                if fid == "legacy":
                    flows.append({"id": fid, "name": "默认十三部门链", "step_count": 0, "steps": []})
                else:
                    flows.append(summarize_flow(fid))
            self.send_json_response({"flows": flows})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_get_flow_detail(self, flow_id: str) -> None:
        try:
            from flow_engine import summarize_flow

            self.send_json_response(summarize_flow(flow_id))
        except ValueError as exc:
            self.send_error_response(str(exc), 404)
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_get_task_flow(self, task_id: str) -> None:
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        self.send_json_response({"task_id": task_id, "flow": fe.get_flow_context(task)})

    def handle_get_cost_report(self, task_id: str) -> None:
        try:
            from cost_report import build_cost_report
            from runtime.model_router import ModelRouter

            report = build_cost_report(task_id, router=ModelRouter())
            self.send_json_response({"task_id": task_id, "cost_report": report})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_flow_advance(self, task_id: str, data: dict) -> None:
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        try:
            task = fe.advance(task_id, actor=data.get("actor", "founder"), force=data.get("force", False))
            self.send_json_response(task)
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_gate_approve(self, task_id: str, data: dict) -> None:
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        try:
            task = fe.approve_gate(task_id, actor=data.get("actor", "founder"), note=data.get("note", ""))
            if task.get("flow_step_id") == "completed":
                actor = data.get("actor", "founder")
                for status in (TaskStatus.REVIEW, TaskStatus.COMPLETED):
                    try:
                        task = self.workflow.transition(task_id, status, actor)
                    except ValueError:
                        break
            self.send_json_response(task)
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_flow_skip(self, task_id: str, data: dict) -> None:
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        step_ids = data.get("step_ids", [])
        if not step_ids:
            self.send_error_response("step_ids 不能为空")
            return
        try:
            task = fe.skip_remaining(task_id, step_ids, actor=data.get("actor", "chairman"))
            self.send_json_response(task)
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_list_skill_proposals(self, query: dict) -> None:
        try:
            from skill_evolution import list_proposals
            status = (query.get("status", [None]) or [None])[0]
            self.send_json_response({"proposals": list_proposals(status)})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_approve_skill_proposal(self, proposal_id: str, data: dict) -> None:
        try:
            from skill_evolution import approve_proposal
            result = approve_proposal(proposal_id, self.skill_service)
            self.send_json_response(result)
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_run_project_close(self, task_id: str, data: dict) -> None:
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        try:
            task = fe.run_close_step(task_id, self.workflow, actor=data.get("actor", "founder"))
            self.send_json_response({"task_id": task_id, "task": task})
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_reject_skill_proposal(self, proposal_id: str, data: dict) -> None:
        try:
            from skill_evolution import reject_proposal
            result = reject_proposal(proposal_id, data.get("reason", ""))
            self.send_json_response(result)
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_skill_versions(self, skill_id: str) -> None:
        try:
            from skill_evolution import list_skill_versions
            self.send_json_response({"skill_id": skill_id, "versions": list_skill_versions(skill_id)})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_skill_rollback(self, skill_id: str, data: dict) -> None:
        try:
            from skill_evolution import rollback_skill
            skill = rollback_skill(skill_id, self.skill_service, data.get("version_file"))
            self.send_json_response({"skill_id": skill_id, "skill": skill})
        except ValueError as exc:
            self.send_error_response(str(exc))

    def handle_get_skill(self, skill_id: str) -> None:
        skill = self.skill_service.get_skill(skill_id)
        if not skill:
            self.send_error_response("Skill not found", 404)
            return
        skill = dict(skill)
        if skill.get("type") == "local" and skill.get("path"):
            skill_path = PROJECT_ROOT / str(skill["path"])
            if skill_path.exists():
                skill["content"] = skill_path.read_text(encoding="utf-8")
        self.send_json_response(skill)

    def handle_create_skill(self, data: dict) -> None:
        skill_id = str(data.get("id", "")).strip()
        if not skill_id:
            self.send_error_response("id 必填")
            return
        if self.skill_service.get_skill(skill_id):
            self.send_error_response(f"Skill 已存在: {skill_id}", 409)
            return
        try:
            skill = self.skill_service.add_local_skill(
                skill_id,
                data.get("name", skill_id),
                data.get("description", ""),
                data.get("agents") or [],
                data.get("content") or f"# {skill_id}\n",
            )
            self.send_json_response(skill, status=201)
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_update_skill(self, skill_id: str, data: dict) -> None:
        skill = self.skill_service.get_skill(skill_id)
        if not skill:
            self.send_error_response("Skill not found", 404)
            return
        try:
            from skill_evolution import _archive_skill

            if skill.get("type") == "local":
                _archive_skill(skill_id, self.skill_service)
                content = data.get("content")
                if content is None and skill.get("path"):
                    skill_path = PROJECT_ROOT / str(skill["path"])
                    content = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
                updated = self.skill_service.add_local_skill(
                    skill_id,
                    data.get("name", skill.get("name", skill_id)),
                    data.get("description", skill.get("description", "")),
                    data.get("agents", skill.get("agents", [])),
                    content or "",
                )
            else:
                updated = self.skill_service.update_skill(
                    skill_id,
                    name=data.get("name", skill.get("name")),
                    description=data.get("description", skill.get("description")),
                    agents=data.get("agents", skill.get("agents")),
                )
            self.send_json_response(updated)
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_export_flow(self, flow_id: str) -> None:
        try:
            from flow_io import export_flow

            self.send_json_response(export_flow(flow_id))
        except ValueError as exc:
            self.send_error_response(str(exc), 404)
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_import_flow(self, data: dict) -> None:
        try:
            from flow_io import import_flow

            overwrite = bool(data.get("overwrite", False))
            payload = data.get("flow") if isinstance(data.get("flow"), dict) else data
            result = import_flow(payload, overwrite=overwrite)
            self.send_json_response(result, status=201)
        except ValueError as exc:
            self.send_error_response(str(exc))
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_get_department_skills(self, dept_id: str) -> None:
        roster = self.agent_monitor.get_department_health()
        dept = roster.get("departments", {}).get(dept_id)
        if not dept:
            self.send_error_response("Department not found", 404)
            return
        agent_ids: set = set()
        head = dept.get("head") or {}
        if head.get("agent_id"):
            agent_ids.add(head["agent_id"])
        for agent in dept.get("default_roles", []) + dept.get("dynamic_agents", []):
            if agent.get("agent_id"):
                agent_ids.add(agent["agent_id"])
        skills = [
            sk
            for sk in self.skill_service.list_skills()
            if any(a in agent_ids for a in sk.get("agents", []))
        ]
        self.send_json_response(
            {
                "department_id": dept_id,
                "agent_ids": sorted(agent_ids),
                "skills": skills,
            }
        )

    def handle_save_task_as_flow(self, task_id: str, data: dict) -> None:
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        new_id = str(data.get("id", "")).strip()
        if not new_id:
            self.send_error_response("id 必填（新 Flow 模板 ID）")
            return
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        try:
            from flow_io import save_task_as_flow

            result = save_task_as_flow(
                task,
                new_id=new_id,
                name=data.get("name"),
                description=data.get("description"),
                flow_engine=fe,
                overwrite=bool(data.get("overwrite", False)),
            )
            self.send_json_response(result, status=201)
        except ValueError as exc:
            self.send_error_response(str(exc))
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_validate_design(self, task_id: str) -> None:
        try:
            from design_artifacts import validate_design_artifact
            self.send_json_response({"task_id": task_id, "validation": validate_design_artifact(task_id)})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_visual_diff(self, task_id: str) -> None:
        try:
            from visual_diff import compute_visual_diff
            self.send_json_response({"task_id": task_id, "visual_diff": compute_visual_diff(task_id)})
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_check_postcondition(self, task_id: str) -> None:
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        try:
            from postcondition import check_postconditions

            ctx = fe.get_flow_context(task)
            step = ctx.get("current_step") or {}
            rules = step.get("postcondition") or []
            outputs = step.get("inputs") or step.get("outputs")
            check = check_postconditions(task_id, rules, outputs)
            fs = task.get("flow_state") or {}
            self.send_json_response(
                {
                    "task_id": task_id,
                    "flow_step_id": task.get("flow_step_id"),
                    "step_type": step.get("type", "standard"),
                    "postcondition": check,
                    "on_fail": step.get("on_fail"),
                    "send_back_to": step.get("send_back_to"),
                    "max_retries": step.get("max_retries"),
                    "last_failure": fs.get("last_failure"),
                    "last_send_back": fs.get("last_send_back"),
                    "retries": fs.get("retries", {}),
                }
            )
        except Exception as exc:
            self.send_error_response(str(exc))

    def handle_get_task_timeline(self, task_id: str) -> None:
        try:
            timeline = self.workflow.timeline(task_id)
        except ValueError as exc:
            self.send_error_response(str(exc), 404)
            return
        self.send_json_response({"task_id": task_id, "timeline": timeline})

    def handle_get_task_artifacts(self, task_id: str) -> None:
        try:
            artifacts = self.task_service.get_task_artifacts(task_id)
        except ValueError as exc:
            self.send_error_response(str(exc), 404)
            return
        self.send_json_response({"task_id": task_id, "artifacts": artifacts})

    def handle_create_task(self, data: Dict[str, Any]) -> None:
        required = ["title", "type", "priority", "requester"]
        for field in required:
            if not data.get(field):
                self.send_error_response(f"Missing required field: {field}")
                return
        try:
            task = self.task_service.create_task(
                title=data["title"],
                task_type=TaskType(data["type"]),
                priority=TaskPriority(data["priority"]),
                requester=data["requester"],
                description=data.get("description", ""),
                flow_id=data.get("flow_id"),
            )
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        flow_id = data.get("flow_id")
        if flow_id and flow_id not in ("legacy", "") and self.workflow.flow_engine:
            try:
                fe = self.workflow.flow_engine
                first = fe.get_flow_context(task).get("current_step") or {}
                if first.get("gate_mode", "auto") == "auto" and not fe.is_supervisor_step(first):
                    for st in (TaskStatus.APPROVED, TaskStatus.DISPATCHED):
                        try:
                            task = self.workflow.transition(task["task_id"], st, "flow:auto_start")
                        except ValueError:
                            pass
                    task = fe.start_current_step(task["task_id"], self.workflow)
            except ValueError:
                pass
        self.send_json_response(task, 201)

    def handle_get_events(self, query: Dict[str, Any]) -> None:
        category = query.get("category", [None])[0]
        subject_id = query.get("subject_id", [None])[0]
        try:
            limit = self.parse_int_query(query, "limit", 30)
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        self.send_json_response({"events": self.event_log.list_events(category=category, subject_id=subject_id, limit=limit)})

    def handle_update_task_status(self, task_id: str, data: Dict[str, Any]) -> None:
        status = data.get("status")
        actor = data.get("actor", "system")
        if not status:
            self.send_error_response("Missing required field: status")
            return
        try:
            task = self.workflow.transition(task_id, TaskStatus(status), actor)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_intervene_task(self, task_id: str, data: Dict[str, Any]) -> None:
        action = data.get("action")
        actor = data.get("actor", "system")
        reason = data.get("reason", "")
        if not action:
            self.send_error_response("Missing required field: action")
            return
        try:
            task = self.workflow.intervene(task_id, action, actor, reason)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_execute_start(self, task_id: str, data: Dict[str, Any]) -> None:
        actor = data.get("actor", "execution_service")
        try:
            task = self.execution_service.start(task_id, actor)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_execute_complete(self, task_id: str, data: Dict[str, Any]) -> None:
        actor = data.get("actor", "execution_service")
        try:
            task = self.execution_service.complete(task_id, actor)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_execute_block(self, task_id: str, data: Dict[str, Any]) -> None:
        actor = data.get("actor", "execution_service")
        reason = data.get("reason", "")
        try:
            task = self.execution_service.block(task_id, actor, reason)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_update_task(self, task_id: str, data: Dict[str, Any]) -> None:
        try:
            task = self.task_service.update_task(task_id, data)
        except ValueError as exc:
            message = str(exc)
            self.send_error_response(message, 404 if "\u4e0d\u5b58\u5728" in message else 400)
            return
        self.send_json_response(task)

    def handle_delete_task(self, task_id: str) -> None:
        if not self.task_service.delete_task(task_id):
            self.send_error_response("Task not found", 404)
            return
        self.send_json_response({"message": f"Task deleted: {task_id}"})

    def handle_create_proposal(self, data: Dict[str, Any]) -> None:
        if not all(data.get(key) for key in ["title", "content", "proposer"]):
            self.send_error_response("Missing required fields: title, content, proposer")
            return
        try:
            proposal = self.board_room.create_proposal(
                data["title"],
                data["content"],
                data["proposer"],
                DecisionType(data.get("decision_type", DecisionType.STRATEGIC.value)),
                task_id=data.get("task_id"),
            )
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        self.send_json_response(proposal, 201)

    def handle_add_discussion(self, proposal_id: str, data: Dict[str, Any]) -> None:
        result = self.board_room.add_discussion(proposal_id, data.get("member_id", ""), data.get("opinion", ""))
        if "error" in result:
            self.send_error_response(result["error"])
            return
        self.send_json_response(result)

    def handle_cast_vote(self, proposal_id: str, data: Dict[str, Any]) -> None:
        if not data.get("member_id") or not data.get("vote"):
            self.send_error_response("Missing required fields: member_id, vote")
            return
        try:
            result = self.board_room.cast_vote(proposal_id, data["member_id"], VoteResult(data["vote"]), data.get("reason", ""))
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        if "error" in result:
            self.send_error_response(result["error"])
            return
        self.send_json_response(result)

    def handle_tally_votes(self, proposal_id: str) -> None:
        result = self.board_room.tally_votes(proposal_id)
        if "error" in result:
            self.send_error_response(result["error"])
            return
        self.send_json_response(result)

    def handle_direct_order(self, proposal_id: str, data: Dict[str, Any]) -> None:
        if not data.get("order"):
            self.send_error_response("Missing required field: order")
            return
        task_id = data.get("task_id")
        step_ids = data.get("step_ids")
        proposal = self.board_room.get_proposal(proposal_id)
        if not task_id and proposal:
            task_id = proposal.get("task_id")
        if not step_ids and proposal:
            step_ids = proposal.get("skip_step_ids")

        result = self.board_room.direct_order(
            proposal_id,
            data["order"],
            task_id=task_id,
            step_ids=step_ids,
        )
        if "error" in result:
            self.send_error_response(result["error"])
            return

        if task_id and self.workflow.flow_engine:
            try:
                from board_flow import apply_direct_order_to_task, parse_skip_steps_from_order

                task = self.task_service.get_task(task_id)
                skips = step_ids or parse_skip_steps_from_order(
                    data["order"], task.get("flow_id") if task else None
                )
                if skips:
                    flow_result = apply_direct_order_to_task(
                        task_id,
                        data["order"],
                        self.workflow.flow_engine,
                        actor=data.get("actor", "chairman"),
                        step_ids=skips,
                    )
                    result["flow_applied"] = flow_result
            except ValueError as exc:
                result["flow_error"] = str(exc)

        self.send_json_response(result)

    def handle_supervisor_verdict(self, task_id: str, step_id: str, data: dict) -> None:
        fe = self.workflow.flow_engine
        if not fe:
            self.send_error_response("FlowEngine 不可用", 503)
            return
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        if task.get("flow_step_id") != step_id:
            self.send_error_response(f"当前 step 为 {task.get('flow_step_id')}，非 {step_id}")
            return
        verdict = data.get("verdict")
        try:
            updated = fe.run_supervisor_step(
                task_id,
                self.workflow,
                actor=data.get("actor", "founder"),
                verdict=verdict,
            )
            self.send_json_response({"task_id": task_id, "step_id": step_id, "task": updated})
        except ValueError as exc:
            self.send_error_response(str(exc))

    # ─────────────────────────────────────────────────────── #
    # 模型配置 API
    # ─────────────────────────────────────────────────────── #

    def handle_get_models(self) -> None:
        try:
            from runtime.model_router import ModelRouter
            router = ModelRouter()
            self.send_json_response({"config": router.to_dict()})
        except ImportError as exc:
            self.send_json_response({"config": {}, "error": str(exc)})

    def handle_post_models(self, data: dict) -> None:
        try:
            from runtime.model_router import ModelRouter
            router = ModelRouter()
            action = data.get("action")
            if action == "save_all":
                new_cfg = data.get("config")
                if not isinstance(new_cfg, dict):
                    self.send_error_response("config 必须是一个字典")
                    return
                router.save_config(new_cfg)
                self.send_json_response({"ok": True, "config": router.to_dict()})
            else:
                self.send_error_response(f"未知 action: {action}")
        except Exception as exc:
            self.send_error_response(str(exc))

    # ─────────────────────────────────────────────────────── #
    # 流量统计 API
    # ─────────────────────────────────────────────────────── #

    def handle_get_traffic(self, query: dict) -> None:
        try:
            from runtime.model_router import ModelRouter
            from runtime.traffic_monitor import TrafficMonitor
            router = ModelRouter()
            monitor = TrafficMonitor(router=router)
            window = (query.get("window", ["1h"]) or ["1h"])[0]
            group_by = (query.get("group_by", [None]) or [None])[0]
            self.send_json_response({
                "stats": monitor.get_stats(window, group_by=group_by),
                "recent": monitor.get_recent(20),
            })
        except Exception as exc:
            self.send_json_response({"stats": {}, "error": str(exc)})

    def handle_export_traffic(self) -> None:
        import time as _time
        log_file = Path(__file__).resolve().parent.parent / "data" / "traffic_logs.jsonl"
        payload = log_file.read_bytes() if log_file.exists() else b""
        ts = _time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "application/jsonlines")
        self.send_header("Content-Disposition", f"attachment; filename=traffic_{ts}.jsonl")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ─────────────────────────────────────────────────────── #
    # 运行时任务触发 API
    # ─────────────────────────────────────────────────────── #

    def handle_run_task(self, data: dict) -> None:
        import threading as _threading
        try:
            from runtime.llm_client import LLMClient
            from runtime.model_router import ModelRouter
            from runtime.traffic_monitor import TrafficMonitor
            from runtime.message_bus import MessageBus
            from runtime.agent_manager import AgentManager
        except ImportError as exc:
            self.send_error_response(f"runtime 模块未安装: {exc}")
            return

        agent_id = data.get("agent_id", "ceo")
        task_id = data.get("task_id")
        task_desc = data.get("task", "")
        skill_ids = data.get("skill_ids")

        if task_id and not task_desc:
            bound = self.task_service.get_task(task_id)
            if not bound:
                self.send_error_response(f"任务不存在: {task_id}", 404)
                return
            agent_id = str(data.get("agent_id") or bound.get("execution_owner") or agent_id)
            task_desc = (
                f"【调试运行】{bound.get('title', '')}\n"
                f"{bound.get('description', '')}"
            )

        if not task_desc:
            self.send_error_response("task 字段不能为空")
            return

        orchestrator = getattr(self.workflow, "_runtime_orchestrator", None)

        def _bg() -> None:
            if task_id and orchestrator:
                bound = self.task_service.get_task(task_id)
                if bound:
                    payload = dict(bound)
                    payload["description"] = task_desc
                    payload["status"] = TaskStatus.EXECUTING.value
                    orchestrator._spawn_for_task(payload)
                    return
            router = ModelRouter()
            monitor = TrafficMonitor(router=router)
            client = LLMClient()
            bus = MessageBus()
            manager = AgentManager(bus, router, monitor, client, self.task_service)
            manager.spawn(
                agent_id=agent_id,
                initial_task=task_desc,
                task_id=task_id,
                skill_ids=skill_ids,
            )

        _threading.Thread(target=_bg, daemon=True).start()
        self.send_json_response({"ok": True, "agent_id": agent_id, "task_id": task_id})

    def handle_departments(self) -> None:
        self.send_json_response(self.agent_monitor.get_department_health())

    def serve_static_artifact(self, path: str) -> None:
        """提供 artifacts/ 下静态文件（visual_diff 截图预览等）。"""
        rel = path[len("/artifacts/") :].lstrip("/").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            self.send_error_response("Forbidden", 403)
            return
        root = (PROJECT_ROOT / "artifacts").resolve()
        file_path = (PROJECT_ROOT / "artifacts" / rel).resolve()
        try:
            file_path.relative_to(root)
        except ValueError:
            self.send_error_response("Forbidden", 403)
            return
        if not file_path.is_file():
            self.send_error_response("Not Found", 404)
            return
        suffix = file_path.suffix.lower()
        content_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".html": "text/html; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }
        payload = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_dashboard(self) -> None:
        dashboard_file = Path(__file__).parent / "dashboard.html"
        if not dashboard_file.exists():
            self.send_error_response("Dashboard not found", 404)
            return
        content = dashboard_file.read_text(encoding="utf-8")
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(host: str = "0.0.0.0", port: int = 7891) -> None:
    server = HTTPServer((host, port), CorpPilotAPI)
    print(f"CorpPilot Dashboard Server")
    print(f"Address: http://{host}:{port}")
    print(f"Dashboard: http://localhost:{port}/dashboard")
    print(f"Health: http://localhost:{port}/api/health")
    print("按 Ctrl+C 停止服务\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CorpPilot Dashboard Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host address")
    parser.add_argument("--port", type=int, default=7891, help="Port number")
    args = parser.parse_args()
    run_server(args.host, args.port)


