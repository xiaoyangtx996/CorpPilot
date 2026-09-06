"""Versioned task prerequisites and exact approved inputs, without another scheduler."""


class DependencyBlocked(ValueError):
    pass


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id TEXT NOT NULL, requirement_version INTEGER NOT NULL,
        dependency_task_id TEXT NOT NULL REFERENCES tasks(id),
        PRIMARY KEY(task_id,requirement_version,dependency_task_id),
        FOREIGN KEY(task_id,requirement_version) REFERENCES task_revisions(task_id,requirement_version))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS dependencies_no_{operation.lower()}
            BEFORE {operation} ON task_dependencies BEGIN
            SELECT RAISE(ABORT, 'Task dependencies are immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS dependencies_no_replace BEFORE INSERT ON task_dependencies
        WHEN EXISTS(SELECT 1 FROM task_dependencies WHERE task_id=NEW.task_id
        AND requirement_version=NEW.requirement_version AND dependency_task_id=NEW.dependency_task_id)
        BEGIN SELECT RAISE(ABORT, 'Task dependencies are immutable'); END""")


def initialize_inputs(db):
    db.execute("""CREATE TABLE IF NOT EXISTS execution_inputs (
        execution_id TEXT NOT NULL REFERENCES task_executions(id),
        dependency_task_id TEXT NOT NULL REFERENCES tasks(id),
        upstream_execution_id TEXT NOT NULL REFERENCES task_executions(id),
        PRIMARY KEY(execution_id,dependency_task_id))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS inputs_no_{operation.lower()}
            BEFORE {operation} ON execution_inputs BEGIN
            SELECT RAISE(ABORT, 'Execution inputs are immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS inputs_no_replace BEFORE INSERT ON execution_inputs
        WHEN EXISTS(SELECT 1 FROM execution_inputs WHERE execution_id=NEW.execution_id
        AND dependency_task_id=NEW.dependency_task_id)
        BEGIN SELECT RAISE(ABORT, 'Execution inputs are immutable'); END""")
    db.execute("""CREATE TABLE IF NOT EXISTS execution_handoff_permissions (
        execution_id TEXT NOT NULL REFERENCES task_executions(id),
        dependency_task_id TEXT NOT NULL REFERENCES tasks(id),
        upstream_execution_id TEXT NOT NULL REFERENCES task_executions(id),
        launch_id TEXT NOT NULL REFERENCES project_launches(id),
        PRIMARY KEY(execution_id,dependency_task_id))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS handoff_no_{operation.lower()}
            BEFORE {operation} ON execution_handoff_permissions BEGIN
            SELECT RAISE(ABORT, 'Handoff permissions are immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS handoff_no_replace BEFORE INSERT ON execution_handoff_permissions
        WHEN EXISTS(SELECT 1 FROM execution_handoff_permissions WHERE execution_id=NEW.execution_id
        AND dependency_task_id=NEW.dependency_task_id)
        BEGIN SELECT RAISE(ABORT, 'Handoff permissions are immutable'); END""")


def ids(db, identity, version):
    return [r[0] for r in db.execute("""SELECT dependency_task_id FROM task_dependencies
        WHERE task_id=? AND requirement_version=? ORDER BY dependency_task_id""", (identity, version))]


def copy(db, identity, old, new):
    db.execute("""INSERT INTO task_dependencies SELECT task_id,?,dependency_task_id
        FROM task_dependencies WHERE task_id=? AND requirement_version=?""", (new, identity, old))


def validate_graph(db, identity, conversation_id, proposed):
    # Iterative DFS bounds work and avoids Python recursion limits on user-defined graphs.
    done, visiting, stack = set(), set(), [(identity, False)]
    while stack:
        node, exiting = stack.pop()
        if exiting:
            visiting.remove(node); done.add(node)
            continue
        if node in visiting:
            raise ValueError("任务依赖不能形成循环")
        if node in done:
            continue
        if len(done) + len(visiting) >= 1000:
            raise ValueError("任务依赖祖先数量超过1000上限")
        row = db.execute("SELECT conversation_id,requirement_version FROM tasks WHERE id=?", (node,)).fetchone()
        if row is None or row["conversation_id"] != conversation_id:
            raise ValueError("前置任务必须存在且属于同一会话")
        visiting.add(node); stack.append((node, True))
        children = proposed if node == identity else ids(db, node, row["requirement_version"])
        stack.extend((child, False) for child in reversed(children))


def bound_inputs(db, execution_id):
    return [dict(row) for row in db.execute("""SELECT dependency_task_id,upstream_execution_id
        FROM execution_inputs WHERE execution_id=? ORDER BY dependency_task_id""", (execution_id,))]


def handoff_source(db, execution_id, dependency):
    if not execution_id or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_handoff_permissions'").fetchone():
        return None
    row = db.execute("""SELECT upstream_execution_id FROM execution_handoff_permissions
        WHERE execution_id=? AND dependency_task_id=?""", (execution_id, dependency)).fetchone()
    return row[0] if row else None


def ready_inputs(db, identity, version, execution_id=None):
    """Resolve current approvals or an explicit, immutable same-launch handoff."""
    if execution_id:
        current = db.execute("SELECT task_id,requirement_version FROM task_executions WHERE id=?", (execution_id,)).fetchone()
        if current is None or (current['task_id'], current['requirement_version']) != (identity, version):
            raise DependencyBlocked("执行与当前任务需求不一致")
    root_execution = execution_id
    pending = [(identity, version, execution_id)]
    visited, result = set(), []
    while pending:
        task_id, task_version, execution_id = pending.pop()
        if task_id in visited:
            continue
        visited.add(task_id)
        if len(visited) > 1000:
            raise DependencyBlocked("任务依赖祖先数量超过1000上限")
        expected = []
        for dependency in ids(db, task_id, task_version):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_reviews'").fetchone():
                raise DependencyBlocked("前置任务尚未获得 Owner 批准")
            row = db.execute("""SELECT e.id,e.requirement_version,e.state,r.decision,t.requirement_version current_version
                FROM tasks t LEFT JOIN task_executions e ON e.id=(SELECT id FROM task_executions
                    WHERE task_id=t.id ORDER BY attempt DESC LIMIT 1)
                LEFT JOIN execution_reviews r ON r.execution_id=e.id WHERE t.id=?""", (dependency,)).fetchone()
            authorized = handoff_source(db, execution_id, dependency)
            if (row is None or row["id"] is None or row["requirement_version"] != row["current_version"]
                    or row["state"] != "awaiting_review"):
                raise DependencyBlocked("前置任务的当前版本和最新执行尚未获得 Owner 批准")
            if authorized:
                if row['id'] != authorized or row['decision'] == 'rejected':
                    raise DependencyBlocked("本批授权的固定前置执行已变化或被 Owner 拒绝")
                if not db.execute('SELECT 1 FROM execution_artifacts WHERE execution_id=?', (authorized,)).fetchone():
                    raise DependencyBlocked("本批前置执行尚无可交接的捕获成果")
            elif row['decision'] != 'approved':
                raise DependencyBlocked("前置任务的当前版本和最新执行尚未获得 Owner 批准")
            expected.append({"dependency_task_id": dependency, "upstream_execution_id": row["id"]})
            pending.append((dependency, row["current_version"], row["id"]))
        if task_id == identity and execution_id == root_execution:
            result = expected
        elif bound_inputs(db, execution_id) != expected:
            raise DependencyBlocked("前置成果依赖已变化，需要重新执行并评审")
    return result


def check_bound(db, run):
    if bound_inputs(db, run["id"]) != ready_inputs(db, run["task_id"], run["requirement_version"], run['id']):
        raise DependencyBlocked("本次执行使用的前置成果已变化，需要重新核查")
