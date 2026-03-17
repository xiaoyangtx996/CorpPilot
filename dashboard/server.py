#!/usr/bin/env python3
"""
CorpPilot Dashboard Server
企业看板 API 服务器 - Python 标准库实现，零依赖
"""

import json
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

# 董事会会议室
from meeting_room import BoardRoom, DecisionType, VoteResult

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
AGENT_CONFIG_FILE = DATA_DIR / "agent_config.json"
SKILLS_CONFIG_FILE = DATA_DIR / "skills.json"

# 董事会会议室实例
board_room = BoardRoom(str(DATA_DIR))


class CorpPilotAPI(BaseHTTPRequestHandler):
    """CorpPilot API 请求处理器"""
    
    # 允许的状态转换
    VALID_TRANSITIONS = {
        "pending": ["classified"],
        "classified": ["planned"],
        "planned": ["reviewing"],
        "reviewing": ["approved", "rejected"],
        "rejected": ["planned"],
        "approved": ["dispatched"],
        "dispatched": ["executing"],
        "executing": ["review", "blocked"],
        "blocked": ["executing"],
        "review": ["completed", "executing"],
        "completed": [],
    }
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{datetime.now().isoformat()}] {args[0]}")
    
    def send_json_response(self, data: Any, status: int = 200):
        """发送 JSON 响应"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def send_error_response(self, message: str, status: int = 400):
        """发送错误响应"""
        self.send_json_response({"error": message}, status)
    
    def read_json_file(self, file_path: Path) -> Any:
        """读取 JSON 文件"""
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def write_json_file(self, file_path: Path, data: Any):
        """写入 JSON 文件"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        
        # API 路由
        if path == "/api/tasks":
            self.handle_get_tasks(query)
        elif path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            self.handle_get_task(task_id)
        elif path == "/api/agents":
            self.handle_get_agents(query)
        elif path.startswith("/api/agents/"):
            agent_id = path.split("/")[-1]
            self.handle_get_agent(agent_id)
        elif path == "/api/skills":
            self.handle_get_skills(query)
        elif path == "/api/stats":
            self.handle_get_stats()
        elif path == "/api/health":
            self.send_json_response({"status": "healthy", "timestamp": datetime.now().isoformat()})
        # 董事会会议室 API
        elif path == "/api/board/proposals":
            self.handle_list_proposals(query)
        elif path.startswith("/api/board/proposals/"):
            proposal_id = path.split("/")[-1]
            self.handle_get_proposal(proposal_id)
        elif path == "/api/board/members":
            self.handle_get_board_members()
        elif path == "/" or path == "/dashboard":
            self.serve_dashboard()
        else:
            self.send_error_response("Not Found", 404)
    
    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error_response("Invalid JSON")
            return
        
        # API 路由
        if path == "/api/tasks":
            self.handle_create_task(data)
        elif path.startswith("/api/tasks/") and path.endswith("/status"):
            task_id = path.split("/")[3]
            self.handle_update_task_status(task_id, data)
        # 董事会会议室 API
        elif path == "/api/board/proposals":
            self.handle_create_proposal(data)
        elif path.startswith("/api/board/proposals/") and path.endswith("/discuss"):
            proposal_id = path.split("/")[4]
            self.handle_add_discussion(proposal_id, data)
        elif path.startswith("/api/board/proposals/") and path.endswith("/vote"):
            proposal_id = path.split("/")[4]
            self.handle_cast_vote(proposal_id, data)
        elif path.startswith("/api/board/proposals/") and path.endswith("/tally"):
            proposal_id = path.split("/")[4]
            self.handle_tally_votes(proposal_id)
        elif path.startswith("/api/board/proposals/") and path.endswith("/order"):
            proposal_id = path.split("/")[4]
            self.handle_direct_order(proposal_id, data)
        else:
            self.send_error_response("Not Found", 404)
    
    def do_PUT(self):
        """处理 PUT 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error_response("Invalid JSON")
            return
        
        if path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            self.handle_update_task(task_id, data)
        else:
            self.send_error_response("Not Found", 404)
    
    def do_DELETE(self):
        """处理 DELETE 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            self.handle_delete_task(task_id)
        else:
            self.send_error_response("Not Found", 404)
    
    # === 任务相关处理 ===
    
    def handle_get_tasks(self, query: Dict):
        """获取任务列表"""
        tasks = self.read_json_file(TASKS_FILE) or []
        
        # 筛选
        status = query.get("status", [None])[0]
        task_type = query.get("type", [None])[0]
        priority = query.get("priority", [None])[0]
        
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        if task_type:
            tasks = [t for t in tasks if t.get("type") == task_type]
        if priority:
            tasks = [t for t in tasks if t.get("priority") == priority]
        
        # 排序（按创建时间倒序）
        tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        # 分页
        limit = int(query.get("limit", [20])[0])
        offset = int(query.get("offset", [0])[0])
        total = len(tasks)
        tasks = tasks[offset:offset + limit]
        
        self.send_json_response({
            "total": total,
            "tasks": tasks
        })
    
    def handle_get_task(self, task_id: str):
        """获取单个任务"""
        tasks = self.read_json_file(TASKS_FILE) or []
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        
        if task:
            self.send_json_response(task)
        else:
            self.send_error_response(f"Task not found: {task_id}", 404)
    
    def handle_create_task(self, data: Dict):
        """创建任务"""
        # 验证必填字段
        required = ["title", "type", "priority", "requester"]
        for field in required:
            if not data.get(field):
                self.send_error_response(f"Missing required field: {field}")
                return
        
        # 生成任务 ID
        tasks = self.read_json_file(TASKS_FILE) or []
        year = datetime.now().year
        count = len([t for t in tasks if t.get("task_id", "").startswith(f"TASK-{year}")]) + 1
        task_id = f"TASK-{year}-{count:04d}"
        
        # 创建任务
        task = {
            "task_id": task_id,
            "title": data["title"],
            "type": data["type"],
            "priority": data["priority"],
            "requester": data["requester"],
            "description": data.get("description", ""),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "history": [
                {
                    "action": "created",
                    "timestamp": datetime.now().isoformat(),
                    "actor": data["requester"]
                }
            ]
        }
        
        tasks.append(task)
        self.write_json_file(TASKS_FILE, tasks)
        
        self.send_json_response(task, 201)
    
    def handle_update_task_status(self, task_id: str, data: Dict):
        """更新任务状态"""
        new_status = data.get("status")
        actor = data.get("actor", "system")
        
        if not new_status:
            self.send_error_response("Missing required field: status")
            return
        
        tasks = self.read_json_file(TASKS_FILE) or []
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        
        if not task:
            self.send_error_response(f"Task not found: {task_id}", 404)
            return
        
        current_status = task["status"]
        
        # 状态机校验
        if new_status not in self.VALID_TRANSITIONS.get(current_status, []):
            self.send_error_response(
                f"Invalid status transition: {current_status} -> {new_status}"
            )
            return
        
        # 更新状态
        task["status"] = new_status
        task["updated_at"] = datetime.now().isoformat()
        task["history"].append({
            "action": f"status_change:{current_status}->{new_status}",
            "timestamp": datetime.now().isoformat(),
            "actor": actor
        })
        
        self.write_json_file(TASKS_FILE, tasks)
        self.send_json_response(task)
    
    def handle_update_task(self, task_id: str, data: Dict):
        """更新任务"""
        tasks = self.read_json_file(TASKS_FILE) or []
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        
        if not task:
            self.send_error_response(f"Task not found: {task_id}", 404)
            return
        
        # 更新字段（排除不可修改的字段）
        protected = ["task_id", "created_at"]
        for key, value in data.items():
            if key not in protected:
                task[key] = value
        
        task["updated_at"] = datetime.now().isoformat()
        
        self.write_json_file(TASKS_FILE, tasks)
        self.send_json_response(task)
    
    def handle_delete_task(self, task_id: str):
        """删除任务"""
        tasks = self.read_json_file(TASKS_FILE) or []
        task = next((t for t in tasks if t["task_id"] == task_id), None)
        
        if not task:
            self.send_error_response(f"Task not found: {task_id}", 404)
            return
        
        tasks = [t for t in tasks if t["task_id"] != task_id]
        self.write_json_file(TASKS_FILE, tasks)
        
        self.send_json_response({"message": f"Task deleted: {task_id}"})
    
    # === Agent 相关处理 ===
    
    def handle_get_agents(self, query: Dict):
        """获取 Agent 列表"""
        config = self.read_json_file(AGENT_CONFIG_FILE) or {"agents": {}}
        agents = list(config.get("agents", {}).values())
        
        # 筛选
        layer = query.get("layer", [None])[0]
        if layer:
            agents = [a for a in agents if a.get("layer") == layer]
        
        self.send_json_response({"agents": agents})
    
    def handle_get_agent(self, agent_id: str):
        """获取单个 Agent"""
        config = self.read_json_file(AGENT_CONFIG_FILE) or {"agents": {}}
        agent = config.get("agents", {}).get(agent_id)
        
        if agent:
            self.send_json_response(agent)
        else:
            self.send_error_response(f"Agent not found: {agent_id}", 404)
    
    # === Skill 相关处理 ===
    
    def handle_get_skills(self, query: Dict):
        """获取 Skill 列表"""
        config = self.read_json_file(SKILLS_CONFIG_FILE) or {"skills": {}}
        skills = list(config.get("skills", {}).values())
        
        # 筛选
        agent = query.get("agent", [None])[0]
        if agent:
            skills = [s for s in skills if agent in s.get("agents", [])]
        
        self.send_json_response({"skills": skills})
    
    # === 统计相关处理 ===
    
    def handle_get_stats(self):
        """获取统计数据"""
        tasks = self.read_json_file(TASKS_FILE) or []
        
        # 按状态统计
        status_counts = {}
        for task in tasks:
            status = task.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # 按类型统计
        type_counts = {}
        for task in tasks:
            task_type = task.get("type", "unknown")
            type_counts[task_type] = type_counts.get(task_type, 0) + 1
        
        # 按优先级统计
        priority_counts = {}
        for task in tasks:
            priority = task.get("priority", "unknown")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        
        self.send_json_response({
            "total_tasks": len(tasks),
            "by_status": status_counts,
            "by_type": type_counts,
            "by_priority": priority_counts
        })
    
    # === 董事会会议室 API ===
    
    def handle_list_proposals(self, query: Dict):
        """列出提案"""
        status = query.get("status", [None])[0]
        proposals = board_room.list_proposals(status)
        self.send_json_response({"proposals": proposals})
    
    def handle_get_proposal(self, proposal_id: str):
        """获取提案详情"""
        proposal = board_room.get_proposal(proposal_id)
        if proposal:
            self.send_json_response(proposal)
        else:
            self.send_error_response("Proposal not found", 404)
    
    def handle_get_board_members(self):
        """获取董事会成员"""
        members = {k: {"id": v.id, "name": v.name, "role": v.role, "vote_weight": v.vote_weight} 
                   for k, v in BoardRoom.MEMBERS.items()}
        self.send_json_response({"members": members})
    
    def handle_create_proposal(self, data: Dict):
        """创建提案"""
        title = data.get("title")
        content = data.get("content")
        proposer = data.get("proposer")
        decision_type = data.get("decision_type", "strategic")
        
        if not all([title, content, proposer]):
            self.send_error_response("Missing required fields: title, content, proposer")
            return
        
        try:
            dtype = DecisionType(decision_type)
        except ValueError:
            self.send_error_response(f"Invalid decision_type. Valid: {[d.value for d in DecisionType]}")
            return
        
        proposal = board_room.create_proposal(title, content, proposer, dtype)
        self.send_json_response(proposal, 201)
    
    def handle_add_discussion(self, proposal_id: str, data: Dict):
        """添加讨论意见"""
        member_id = data.get("member_id")
        opinion = data.get("opinion")
        
        if not all([member_id, opinion]):
            self.send_error_response("Missing required fields: member_id, opinion")
            return
        
        result = board_room.add_discussion(proposal_id, member_id, opinion)
        if "error" in result:
            self.send_error_response(result["error"])
        else:
            self.send_json_response(result)
    
    def handle_cast_vote(self, proposal_id: str, data: Dict):
        """投票"""
        member_id = data.get("member_id")
        vote = data.get("vote")
        reason = data.get("reason", "")
        
        if not all([member_id, vote]):
            self.send_error_response("Missing required fields: member_id, vote")
            return
        
        try:
            vote_result = VoteResult(vote)
        except ValueError:
            self.send_error_response(f"Invalid vote. Valid: {[v.value for v in VoteResult]}")
            return
        
        result = board_room.cast_vote(proposal_id, member_id, vote_result, reason)
        if "error" in result:
            self.send_error_response(result["error"])
        else:
            self.send_json_response(result)
    
    def handle_tally_votes(self, proposal_id: str):
        """计票"""
        result = board_room.tally_votes(proposal_id)
        if "error" in result:
            self.send_error_response(result["error"])
        else:
            self.send_json_response(result)
    
    def handle_direct_order(self, proposal_id: str, data: Dict):
        """董事长直接下令"""
        order = data.get("order")
        if not order:
            self.send_error_response("Missing required field: order")
            return
        
        result = board_room.direct_order(proposal_id, order)
        if "error" in result:
            self.send_error_response(result["error"])
        else:
            self.send_json_response(result)
    
    # === 静态文件服务 ===
    
    def serve_dashboard(self):
        """服务 Dashboard 页面"""
        dashboard_file = Path(__file__).parent / "dashboard.html"
        
        if not dashboard_file.exists():
            self.send_error_response("Dashboard not found", 404)
            return
        
        with open(dashboard_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))


def run_server(host: str = "0.0.0.0", port: int = 7891):
    """启动服务器"""
    server = HTTPServer((host, port), CorpPilotAPI)
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  🏢 CorpPilot Dashboard Server                                ║
║  ─────────────────────────────────────────────────────────── ║
║  Address: http://{host}:{port}
║  Dashboard: http://localhost:{port}/dashboard
║  API Docs: http://localhost:{port}/api/health
╚══════════════════════════════════════════════════════════════╝
""")
    print("Press Ctrl+C to stop the server\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        server.shutdown()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="CorpPilot Dashboard Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host address")
    parser.add_argument("--port", type=int, default=7891, help="Port number")
    
    args = parser.parse_args()
    run_server(args.host, args.port)
