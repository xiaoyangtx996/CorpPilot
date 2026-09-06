"""Approved memory and provenance survive a real backup/restore round trip."""
from test_workbench_memories import setup, proposal, decision
from workbench.backup import backup, restore
from workbench.memories import Memories
from workbench.runs import Runs
from workbench.store import Store


def test_approved_memory_history_and_rollback_after_restore(tmp_path):
    source = tmp_path / 'source'
    store, _, _, task, run, memory = setup(source)
    Runs(store)
    identity = task['agent_id']
    candidate = memory.propose('agent', identity, proposal(run))
    memory.decide(candidate['id'], decision())
    expected = memory.get('agent', identity)
    history = memory.history('agent', identity)
    backup(source, tmp_path / 'backup')
    restore(tmp_path / 'backup', tmp_path / 'restored')
    restored = Memories(Store(tmp_path / 'restored'))
    assert restored.get('agent', identity) == expected
    assert restored.history('agent', identity) == history
    assert restored.candidate(candidate['id']) == memory.candidate(candidate['id'])
    result = restored.rollback('agent', identity, {'request_id': 'restored-rollback', 'expected_version': 1,
        'target_version': 0, 'note': 'Fixture verifies restored provenance and rollback'})
    assert result['version'] == 2 and result['content'] == ''
    assert memory.get('agent', identity) == expected
