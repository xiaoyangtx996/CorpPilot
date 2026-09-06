"""Real Owner HTTP gates coordination without granting powers to task recipients."""
import json
import pytest

from test_workbench_api import request, running
from test_workbench_collaboration_api import setup_plan
from test_workbench_provider import endpoint
from test_workbench_controller import configure, until
from workbench.settings import Settings


@pytest.mark.parametrize('kind', ['plan', 'proposal'])
def test_coordinator_grant_required_before_http_effects_and_replay_survives_restart(tmp_path, monkeypatch, kind):
    store, source, _, approved = setup_plan(tmp_path)
    coordinator = approved['coordinator_id']
    store.save_agent({'tools': ['read']}, coordinator)
    proposal = {k: approved[k] for k in ('title', 'shared_brief', 'tasks')}
    output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}]}
    payload = approved if kind == 'plan' else {
        'agent_id': coordinator, 'source_message_id': approved['source_message_id'],
        'request_id': 'f85-proposal', 'candidate_ids': sorted({t['agent_id'] for t in approved['tasks']})}
    route = f'/api/workbench/conversations/{source["id"]}/collaboration-' + ('plans' if kind == 'plan' else 'proposals')
    with endpoint(output) as (config, calls):
        with running(tmp_path) as port:
            configure(Settings(store), config, monkeypatch)
            status, error = request(port, 'POST', route, payload)
            assert status == 403 and 'delegate' in error['error']
            assert request(port, 'GET', route) == (200, [])
            assert len(store.conversations()) == 1 and calls == []
            store.save_agent({'tools': ['read', 'delegate']}, coordinator)
            status, receipt = request(port, 'POST', route, payload)
            assert status == (201 if kind == 'plan' else 202)
            if kind == 'proposal':
                detail = '/api/workbench/collaboration-proposals/' + receipt['id']
                receipt = until(lambda: (r if (r := request(port, 'GET', detail)[1])['state'] == 'completed' else None))
                assert receipt['proposal'] == proposal and len(calls) == 1
            else:
                detail = '/api/workbench/collaboration-plans/' + receipt['id']
                assert calls == []
            store.save_agent({'tools': ['read']}, coordinator)
            assert request(port, 'POST', route, payload) == (status, receipt)
            assert request(port, 'GET', detail) == (200, receipt)
            assert request(port, 'POST', route, {**payload, 'request_id': 'new-after-revocation'})[0] == 403
            # Assignment does not require recipients to be able to coordinate other agents.
            assert all('delegate' not in store.agent(t['agent_id'])['tools'] for t in approved['tasks'])
        with running(tmp_path) as port:
            assert request(port, 'POST', route, payload) == (status, receipt)
            assert request(port, 'GET', detail) == (200, receipt)
            assert len(calls) == (1 if kind == 'proposal' else 0)
