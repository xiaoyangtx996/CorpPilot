"""Real local HTTP/subprocess receipts survive failed output publication; no paid calls."""
import pytest

from test_workbench_provider import endpoint, SNAPSHOT
from test_workbench_controller import configure, until
from workbench.provider import run_reply, ProviderError
from workbench.controller import ReplyController
from workbench.settings import Settings
from workbench.store import Store
from workbench.runs import Runs
from workbench.activity import get as activity


def create(runs):
    store = runs.store
    actor = store.agents()[0]['id']
    room = store.save_conversation({'type': 'dm', 'title': 'usage', 'member_ids': [actor]})['id']
    source = store.send_message(room, {'content': 'question', 'request_id': 'source'})
    run = runs.create(room, {'agent_id': actor, 'source_message_id': source['id'], 'request_id': 'run'})
    return actor, room, run['id']


@pytest.mark.parametrize('choices', [[], [{'finish_reason': 'length', 'message': {'content': 'partial'}}],
    [{'finish_reason': 'stop', 'message': {'content': '', 'tool_calls': [{'private': 'never echoed'}]}}]])
def test_rejected_output_keeps_subprocess_receipt(choices):
    with endpoint({'model': 'reported-model', 'usage': {'prompt_tokens': 9, 'completion_tokens': 4},
                   'choices': choices}) as (config, requests):
        with pytest.raises(ProviderError) as failure:
            run_reply(config, SNAPSHOT)
        assert failure.value.receipt == {'model': 'reported-model', 'prompt_tokens': 9, 'completion_tokens': 4}
        assert 'never echoed' not in str(failure.value)
        assert len(requests) == 1


@pytest.mark.parametrize('publication_failure', [False, True])
def test_controller_persists_failed_usage_and_activity_after_restart(tmp_path, monkeypatch, publication_failure):
    output = {'model': 'reported-model', 'usage': {'prompt_tokens': 9, 'completion_tokens': 4},
              'choices': [{'finish_reason': 'stop' if publication_failure else 'length', 'message': {'content': 'result'}}]}
    with endpoint(output) as (config, requests):
        store = Store(tmp_path)
        settings = Settings(store)
        configure(settings, config, monkeypatch)
        ctl = ReplyController(store, settings)
        try:
            if publication_failure:
                def reject(*args, **kwargs):
                    raise ValueError('publication permission changed')
                monkeypatch.setattr(ctl.runs, 'finish', reject)
            actor, room, identity = create(ctl.runs)
            until(lambda: ctl.runs.get(identity)['state'] == 'failed')
            assert len(store.messages(room)) == 1
        finally:
            ctl.close()
        reopened = ReplyController(store, settings)
        try:
            result = activity(store, actor)['model_runs']['items'][0]
            assert result['id'] == identity and result['state'] == 'failed'
            assert result['model'] == 'reported-model'
            assert result['usage'] == {'prompt_tokens': 9, 'completion_tokens': 4}
            assert len(requests) == 1
        finally:
            reopened.close()


def test_receipt_is_idempotent_unknown_fields_are_not_zero_and_recovery_preserves_it(tmp_path):
    store = Store(tmp_path)
    runs = Runs(store)
    _, _, identity = create(runs)
    receipt = {'model': 'reported-model', 'prompt_tokens': 9, 'completion_tokens': None}
    with pytest.raises(ValueError, match='运行中'):
        runs.record_usage(identity, receipt)
    runs.claim(identity)
    runs.record_usage(identity, receipt)
    runs.record_usage(identity, receipt)
    with pytest.raises(ValueError, match='冲突'):
        runs.record_usage(identity, {**receipt, 'completion_tokens': 0})
    assert runs.recover() == 1
    runs.record_usage(identity, receipt)
    assert runs.get(identity)['state'] == 'unknown'
    assert runs.get(identity)['usage'] == {'prompt_tokens': 9, 'completion_tokens': None}
    with pytest.raises(ValueError, match='Token'):
        runs.record_usage(identity, {**receipt, 'prompt_tokens': True})


def test_unusable_receipt_is_unknown_not_zero():
    with endpoint({'usage': {'prompt_tokens': -1}, 'choices': []}) as (config, _):
        with pytest.raises(ProviderError) as failure:
            run_reply(config, SNAPSHOT)
        assert failure.value.receipt is None


def test_finish_cannot_replace_receipt_or_publish_conflicting_output(tmp_path):
    runs = Runs(Store(tmp_path))
    _, room, identity = create(runs)
    runs.claim(identity)
    receipt = {'model': 'reported-model', 'prompt_tokens': 9, 'completion_tokens': 4}
    runs.record_usage(identity, receipt)
    with pytest.raises(ValueError, match='冲突'):
        runs.finish(identity, 'result', **{**receipt, 'completion_tokens': 0})
    assert len(runs.store.messages(room)) == 1
    assert runs.get(identity)['usage']['completion_tokens'] == 4
    assert runs.finish(identity, 'result', **receipt)['state'] == 'completed'


@pytest.mark.parametrize('reason', ['stop', 'length'])
def test_reflected_synthetic_key_is_not_in_public_model_receipt(reason):
    output = {'model': 'reflected dummy-test-secret', 'usage': {'prompt_tokens': 9},
              'choices': [{'finish_reason': reason, 'message': {'content': 'ok'}}]}
    with endpoint(output) as (config, _):
        if reason == 'stop':
            receipt = run_reply(config, SNAPSHOT)
        else:
            with pytest.raises(ProviderError) as failure:
                run_reply(config, SNAPSHOT)
            receipt = failure.value.receipt
        assert receipt['model'] == '[模型标识已隐藏]'
        assert receipt['prompt_tokens'] == 9 and receipt['completion_tokens'] is None
