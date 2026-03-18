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

        if path == "/api/tasks":
            self.handle_get_tasks(query)
            return
        if path.startswith("/api/tasks/"):
            segments = path.split("/")
            if path.endswith("/timeline"):
                self.handle_get_task_timeline(segments[-2])
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
        limit = int(query.get("limit", [20])[0])
        offset = int(query.get("offset", [0])[0])
        self.send_json_response({"total": len(tasks), "tasks": tasks[offset : offset + limit]})

    def handle_get_task(self, task_id: str) -> None:
        task = self.task_service.get_task(task_id)
        if not task:
            self.send_error_response("Task not found", 404)
            return
        task = dict(task)
        task["routing"] = self.workflow.routing_snapshot(task_id)
        self.send_json_response(task)

    def handle_get_task_timeline(self, task_id: str) -> None:
        try:
            timeline = self.workflow.timeline(task_id)
        except ValueError as exc:
            self.send_error_response(str(exc), 404)
            return
        self.send_json_response({"task_id": task_id, "timeline": timeline})

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
            )
        except ValueError as exc:
            self.send_error_response(str(exc))
            return
        self.send_json_response(task, 201)

    def handle_get_events(self, query: Dict[str, Any]) -> None:
        category = query.get("category", [None])[0]
        subject_id = query.get("subject_id", [None])[0]
        limit = int(query.get("limit", [30])[0])
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
            self.send_error_response(str(exc), 404)
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
                data["title"], data["content"], data["proposer"], DecisionType(data.get("decision_type", DecisionType.STRATEGIC.value))
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
        result = self.board_room.direct_order(proposal_id, data["order"])
        if "error" in result:
            self.send_error_response(result["error"])
            return
        self.send_json_response(result)

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


