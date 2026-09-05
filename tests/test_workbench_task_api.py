"""Real HTTP task contracts, optimistic edits, and restart recovery."""
from test_workbench_api import running, request


def test_task_http_contract_and_restart(tmp_path):
    prefix = "/api/workbench"
    with running(tmp_path) as port:
        _, agents = request(port, "GET", prefix + "/agents")
        agent_id = agents[0]["id"]
        _, conversation = request(port, "POST", prefix + "/conversations", {
            "type": "project", "title": "任务验收", "member_ids": [agent_id],
        })
        conversation_path = prefix + "/conversations/" + conversation["id"]
        _, source = request(port, "POST", conversation_path + "/messages", {
            "content": "建立可验收需求", "request_id": "source",
        })
        payload = {"source_message_id": source["id"], "request_id": "task",
                   "agent_id": agent_id, "title": "整理报告", "scope": "本项目",
                   "acceptance": "报告包含来源和结论"}
        status, task = request(port, "POST", conversation_path + "/tasks", payload)
        assert status == 201 and task["requirement_version"] == 1
        path = prefix + "/tasks/" + task["id"]
        assert request(port, "GET", path) == (200, task)
        assert request(port, "GET", conversation_path + "/tasks") == (200, [task])
        revised = {key: payload[key] for key in ("title", "scope", "acceptance", "agent_id")}
        revised.update(expected_version=1, scope="只分析已批准资料")
        status, updated = request(port, "PATCH", path, revised)
        assert status == 200 and updated["requirement_version"] == 2
        assert request(port, "PATCH", path, {**revised, "scope": "过期编辑"})[0] == 409
        assert request(port, "POST", conversation_path + "/tasks", payload) == (201, updated)
        assert request(port, "PATCH", path, {**revised, "expected_version": 2, "state": "completed"})[0] == 400
        assert request(port, "GET", path + "/revisions")[0] == 200
        assert len(request(port, "GET", path + "/revisions")[1]) == 2
        assert request(port, "GET", prefix + "/tasks/missing")[0] == 404
        assert request(port, "GET", path + "?actor_id=" + agent_id)[0] == 400
        # Creating/editing task cards must not schedule model calls or agent messages.
        assert request(port, "GET", conversation_path + "/runs") == (200, [])
        assert len(request(port, "GET", conversation_path + "/messages")[1]) == 1
        request(port, "PATCH", conversation_path, {"archived": True})
        assert request(port, "PATCH", path, {**revised, "expected_version": 2})[0] == 400
    with running(tmp_path) as port:
        assert request(port, "GET", path) == (200, updated)
        assert len(request(port, "GET", path + "/revisions")[1]) == 2
