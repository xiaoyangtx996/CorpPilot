"""The text transport is independent of CLI tools, with persistent secret-free settings."""
import json
import os

import pytest

from test_workbench_settings import CONFIG
from test_workbench_api import running, request
from workbench.settings import Settings
from workbench.store import Store
from workbench import provider


def test_text_transport_validation_persistence_and_revalidation(tmp_path, monkeypatch):
    store = Store(tmp_path / 'data')
    settings = Settings(store)
    assert settings.get()['transport'] == 'http'
    executable = tmp_path / 'opencode.exe'
    executable.touch()
    monkeypatch.setenv(CONFIG['api_key_env'], 'private-text-key')
    patch = {**{key: value for key, value in CONFIG.items() if key != 'base_url'},
             'transport': 'opencode', 'executable': str(executable), 'model': 'opencode/big-pickle'}
    status = settings.save(patch)
    assert status['configured'] and status['base_url'] == ''
    assert Settings(store).get() == status
    if os.name == 'nt':
        settings.save({'enabled': True})
        resolved = settings.resolve()
        assert resolved['transport'] == 'opencode' and resolved['api_key'] == 'private-text-key'
        with pytest.raises(ValueError, match='Zen'):
            settings.save({'model': 'other/model'})
        assert settings.get()['model'] == patch['model']
        executable.unlink()
        with pytest.raises(ValueError, match='存在'):
            settings.resolve()
    for invalid in ({'transport': 'unknown'}, {'executable': 'opencode.exe'}, {'executable': 5}, {'executable': 'C:/opencode.cmd'}):
        before = settings.get()
        with pytest.raises(ValueError):
            settings.save(invalid)
        assert settings.get() == before
    with store.connect() as db:
        assert 'private-text-key' not in '\n'.join(db.iterdump())


def test_explicit_transport_routes_to_text_provider_only(monkeypatch):
    from workbench import opencode_provider
    calls = []
    monkeypatch.setattr(opencode_provider, 'run_reply', lambda config, snapshot: calls.append((config, snapshot)) or {'content':'text'})
    snapshot = {'test':'snapshot'}
    assert provider.run_reply({'transport':'opencode'}, snapshot) == {'content':'text'}
    assert calls == [({'transport':'opencode'}, snapshot)]


def test_http_configuration_roundtrip_does_not_start_model(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, 'run_reply', lambda *a, **kw: pytest.fail('configuration started a model'))
    executable = tmp_path / 'opencode.exe'
    executable.touch()
    values = {**{key:value for key,value in CONFIG.items() if key != 'base_url'},
              'transport':'opencode', 'executable':str(executable), 'model':'opencode/big-pickle'}
    with running(tmp_path / 'data') as port:
        code, saved = request(port, 'PATCH', '/api/workbench/model-settings', values)
        assert code == 200 and saved['transport'] == 'opencode' and not saved['enabled']
    with running(tmp_path / 'data') as port:
        assert request(port, 'GET', '/api/workbench/model-settings') == (200, saved)
