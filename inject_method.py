"""插入 get_department_health 方法到 AgentMonitorService 类"""
from pathlib import Path

core_path = Path("scripts/core.py")
content = core_path.read_text(encoding="utf-8")

METHOD = '''
    def get_department_health(self) -> Dict[str, Any]:
        """聚合各部门所有 Agent 的健康状态。供 /api/departments 端点调用。"""
        roster_path = self.task_service.data_dir / "department_roster.json"
        if not roster_path.exists():
            return {"departments": {}}
        try:
            with open(roster_path, "r", encoding="utf-8") as f:
                roster: Dict[str, Any] = json.load(f)
        except Exception:
            return {"departments": {}}

        tasks = self.task_service.all_tasks()
        depts: Dict[str, Any] = roster.get("departments", {})

        for dept_id, dept_data in depts.items():
            all_agents: List[Dict[str, Any]] = []
            if dept_data.get("head"):
                all_agents.append(dept_data["head"])
            all_agents.extend(dept_data.get("default_roles", []))
            all_agents.extend(dept_data.get("dynamic_agents", []))

            active_count = busy_count = blocked_count = 0
            for agent in all_agents:
                aid = agent.get("agent_id", "")
                owned = [t for t in tasks if t.get("current_owner") == aid or t.get("execution_owner") == aid]
                blocked = [t for t in owned if t.get("status") == TaskStatus.BLOCKED.value]
                executing = [t for t in owned if t.get("status") == TaskStatus.EXECUTING.value]
                if blocked:
                    health = "blocked"; blocked_count += 1
                elif executing:
                    health = "busy"; busy_count += 1
                elif owned:
                    health = "active"; active_count += 1
                else:
                    health = "idle"
                agent["health_status"] = health
                agent["owned_task_count"] = len(owned)
                agent["executing_task_count"] = len(executing)
                agent["blocked_task_count"] = len(blocked)
                agent["completed_task_count"] = len(
                    [t for t in owned if t.get("status") == TaskStatus.COMPLETED.value]
                )
            dept_data["stats"] = {
                "total_agents": len(all_agents),
                "active_agents": active_count,
                "busy_agents": busy_count,
                "blocked_agents": blocked_count,
            }
        return roster

'''

ANCHOR = "        return max(timestamps) if timestamps else None\r\n\r\n\r\nclass BoardRoom:"
REPLACEMENT = "        return max(timestamps) if timestamps else None\r\n" + METHOD.replace("\n", "\r\n") + "\r\nclass BoardRoom:"

if "get_department_health" in content:
    print("已存在 get_department_health，无需注入")
elif ANCHOR in content:
    content = content.replace(ANCHOR, REPLACEMENT)
    core_path.write_text(content, encoding="utf-8")
    print("注入成功")
else:
    # 尝试更宽泛的锚点
    ANCHOR2 = "        return max(timestamps) if timestamps else None"
    idx = content.find(ANCHOR2)
    if idx == -1:
        print("ERROR: 找不到锚点")
    else:
        insert_at = idx + len(ANCHOR2)
        final = content[:insert_at] + "\r\n" + METHOD.replace("\n", "\r\n") + content[insert_at:]
        core_path.write_text(final, encoding="utf-8")
        print("注入成功（方式B）")
