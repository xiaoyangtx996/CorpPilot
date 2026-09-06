"""Bounded Owner observation; never a worker context or a dispatch operation."""
from .store import _text
from .tasks import Tasks
from .runs import Runs
from .executions import Executions


def get(store, agent_id):
    agent_id = _text(agent_id, 'Agent ID')
    with store.connect() as db:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM agents WHERE id=?', (agent_id,)).fetchone():
            raise KeyError('Agent 不存在')

        def group(source, select, order):
            total = db.execute('SELECT count(*) ' + source, (agent_id,)).fetchone()[0]
            items = [dict(row) for row in db.execute(select + ' ' + source + ' ORDER BY ' + order + ' LIMIT 50', (agent_id,))]
            return {'items': items, 'total': total, 'has_more': total > len(items)}

        tasks = group('FROM tasks t JOIN task_revisions r ON r.task_id=t.id AND r.requirement_version=t.requirement_version WHERE r.agent_id=?',
                      'SELECT t.id', 't.updated_at DESC,t.id DESC')
        tasks['items'] = [Tasks._task(db, row['id']) for row in tasks['items']]
        executions = group('FROM task_executions e JOIN tasks t ON t.id=e.task_id JOIN task_revisions r ON r.task_id=e.task_id AND r.requirement_version=e.requirement_version WHERE e.agent_id=?',
                           '''SELECT e.*,r.title task_title,t.conversation_id,
                           (SELECT count(*) FROM execution_artifacts a WHERE a.execution_id=e.id) artifact_count,
                           (SELECT decision FROM execution_reviews v WHERE v.execution_id=e.id) review_decision''',
                           'e.updated_at DESC,e.id DESC')
        executions['items'] = [{**row, 'usage': Executions._usage(db, row['id'])} for row in executions['items']]
        runs = group('FROM runs r WHERE r.agent_id=?', '''SELECT r.id,CASE
                     WHEN EXISTS(SELECT 1 FROM planning_requests p WHERE p.run_id=r.id) THEN 'planning'
                     WHEN EXISTS(SELECT 1 FROM retrospective_requests p WHERE p.run_id=r.id) THEN 'retrospective'
                     WHEN EXISTS(SELECT 1 FROM peer_review_requests p WHERE p.run_id=r.id) THEN 'peer_review'
                     ELSE 'reply' END kind''', 'r.updated_at DESC,r.id DESC')
        runs['items'] = [{**Runs._run(db, row['id']), 'kind': row['kind']} for row in runs['items']]
        return {'agent_id': agent_id, 'tasks': tasks, 'executions': executions, 'model_runs': runs}
