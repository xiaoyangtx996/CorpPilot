"""Concurrent real HTTP, restart and backup verify recoverable identity creation."""
from concurrent.futures import ThreadPoolExecutor

from test_workbench_api import running, request as http
from workbench.store import Store
from workbench.backup import backup, restore


def test_concurrent_http_creation_has_one_persistent_receipt(tmp_path):
    with running(tmp_path) as port:
        store = Store(tmp_path)
        count = len(store.agents())
        payload = {'request_id': 'http-create', 'name': 'Persistent identity', 'template_id': store.templates()[0]['id'], 'skills': ['coding']}
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: http(port, 'POST', '/api/workbench/agents', payload), range(8)))
        assert all(status == 201 for status, _ in responses)
        agent = responses[0][1]
        assert all(value == agent for _, value in responses)
        assert len(store.agents()) == count + 1
        path = '/api/workbench/agent-requests/http-create'
        status, receipt = http(port, 'GET', path)
        assert status == 200 and receipt['agent'] == agent
        assert receipt['payload'] == {key: agent[key] for key in ('name', 'template_id', 'model', 'skills', 'tools', 'enabled')}
        assert receipt['request_id'] == 'http-create'
        assert http(port, 'POST', '/api/workbench/agents', {**payload, 'name': 'Different'})[0] == 400
        assert http(port, 'PATCH', '/api/workbench/agents/' + agent['id'], {'request_id': 'wrong'})[0] == 400
        assert http(port, 'PATCH', '/api/workbench/agents/' + agent['id'], {'name': 'Later renamed', 'enabled': False})[0] == 200
        assert http(port, 'GET', path) == (200, receipt)
        assert http(port, 'POST', '/api/workbench/agents', payload) == (201, agent)
        assert store.agent(agent['id'])['name'] == 'Later renamed'
        with store.connect() as db: before = list(db.iterdump())
        assert http(port, 'GET', path, headers={'Authorization': ''})[0] == 401
        assert http(port, 'GET', '/api/workbench/agent-requests/unknown') == (200, None)
        assert http(port, 'GET', path) == (200, receipt)
        with store.connect() as db: assert list(db.iterdump()) == before
    with running(tmp_path) as port:
        assert http(port, 'GET', path) == (200, receipt)
        assert http(port, 'POST', '/api/workbench/agents', payload) == (201, agent)
    bundle = tmp_path.parent / (tmp_path.name + '-backup'); recovered = tmp_path.parent / (tmp_path.name + '-restored')
    backup(tmp_path, bundle); restore(bundle, recovered)
    with running(recovered) as port:
        assert http(port, 'GET', path) == (200, receipt)
        assert http(port, 'POST', '/api/workbench/agents', payload) == (201, agent)
        assert http(port, 'GET', '/api/workbench/agents/' + agent['id'])[1]['name'] == 'Later renamed'


def test_failed_creation_http_has_no_agent_or_receipt(tmp_path):
    with running(tmp_path) as port:
        store = Store(tmp_path); count = len(store.agents())
        value = {'request_id': 'fixable', 'name': 'Fixable', 'template_id': store.templates()[0]['id'], 'skills': ['missing']}
        assert http(port, 'POST', '/api/workbench/agents', value)[0] == 400
        assert http(port, 'GET', '/api/workbench/agent-requests/fixable') == (200, None)
        assert len(store.agents()) == count
        value['skills'] = []
        status, agent = http(port, 'POST', '/api/workbench/agents', value)
        assert status == 201 and len(store.agents()) == count + 1
        assert http(port, 'GET', '/api/workbench/agent-requests/fixable')[1]['agent']['id'] == agent['id']
