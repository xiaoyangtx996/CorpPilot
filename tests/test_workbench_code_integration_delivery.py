"""Saved F71 artifacts reach native integration without a model or source writes.

This checks the internal primitive, not the future Owner integration API.
"""
import json
import threading

import pytest

from test_workbench_code_review_delivery import approved
from test_workbench_git_checkout import git
from workbench import artifacts
from workbench.backup import backup, restore
from workbench.store import Store
from workbench.runs import Runs
from workbench.code_integration import integrate_code
from workbench import code_integration
from workbench.git_checkout import PreparationUnknownError
from test_workbench_code_integration import change
from test_workbench_git_checkout import repo


@pytest.mark.parametrize('restored', [False, True])
def test_saved_approved_patch_integrates_pinned_base(tmp_path, monkeypatch, restored):
    source, sha, store, tasks, room, agents, task, controller, run, saved, calls = approved(tmp_path, monkeypatch)
    controller.close()
    # Advance and dirty the source after delivery; integration must use the old pin.
    (source/'code.txt').write_bytes(b'later committed source\n')
    git(source, 'commit', '-qam', 'later')
    (source/'code.txt').write_bytes(b'owner uncommitted work\n')
    before = (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'),
              (source/'.git/index').read_bytes(), (source/'.git/config').read_bytes())
    if restored:
        Runs(store)  # Same model-ledger initialization used by the full server.
        backup(store.data_dir, tmp_path/'backup')
        restore(tmp_path/'backup', tmp_path/'restored')
        store = Store(tmp_path/'restored')
    rows = {r['path']: artifacts.get(store, r['id']) for r in artifacts.list_for(store, run['id'])}
    manifest = json.loads(rows['corppilot-code/manifest.json']['data'])
    change = dict(patch=rows['corppilot-code/change.patch']['data'], manifest=manifest)
    target = tmp_path/'integration'
    result = integrate_code(str(source), sha, target, 'corppilot/integration-delivery', [change])
    assert result['tree'] == manifest['target_tree']
    assert git(target, 'rev-parse', 'HEAD') == result['commit']
    assert git(target, 'cat-file', '-p', result['commit']).splitlines()[1] == 'parent '+sha
    assert (target/'code.txt').read_bytes() == b'changed code\n'
    assert (target/'new.txt').read_bytes() == b'new source\n'
    assert git(target, 'status', '--porcelain') == ''
    assert result['source_execution_ids'] == [run['id']]
    assert before == (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'),
                      (source/'.git/index').read_bytes(), (source/'.git/config').read_bytes())
    assert (source/'code.txt').read_bytes() == b'owner uncommitted work\n'
    # Restore preserves saved artifacts, not Git objects; source stays necessary.
    assert calls == [run['id']]


def test_saved_artifact_corruption_never_reaches_integration(tmp_path, monkeypatch):
    source, sha, store, tasks, room, agents, task, controller, run, saved, calls = approved(tmp_path, monkeypatch)
    controller.close()
    rows = {r['path']: artifacts.get(store, r['id']) for r in artifacts.list_for(store, run['id'])}
    manifest = json.loads(rows['corppilot-code/manifest.json']['data'])
    with pytest.raises(ValueError):
        integrate_code(str(source), sha, tmp_path/'bad-integration', 'corppilot/integration-bad',
                       [dict(patch=rows['corppilot-code/change.patch']['data']+b'corrupt', manifest=manifest)])
    assert git(source, 'rev-parse', 'HEAD') == sha


@pytest.mark.parametrize('failure', ['unknown', 'cancel'])
def test_failure_after_ref_write_keeps_directory_and_never_retries(tmp_path, monkeypatch, failure):
    source, base = repo(tmp_path)
    item = change(tmp_path, source, base, {'new.txt': b'accepted code\n'})
    target = tmp_path/'partial'
    cancel = threading.Event()
    original = code_integration.run_process
    observed = []
    def controlled(argv, *args, **kwargs):
        observed.append(argv)
        if failure == 'unknown' and 'reset' in argv:
            return dict(reason='unknown', exit_code=None, stdout=b'')
        result = original(argv, *args, **kwargs)
        if failure == 'cancel' and 'update-ref' in argv:
            cancel.set()
        return result
    monkeypatch.setattr(code_integration, 'run_process', controlled)
    with pytest.raises(PreparationUnknownError if failure == 'unknown' else ValueError):
        integrate_code(source, base, target, 'integration/partial', [item], cancel=cancel)
    assert any('update-ref' in argv for argv in observed)
    assert git(target, 'rev-parse', 'refs/heads/integration/partial') != base
    count = len(observed)
    with pytest.raises(ValueError):
        integrate_code(source, base, target, 'integration/partial', [item])
    assert len(observed) == count
    assert git(source, 'rev-parse', 'HEAD') == base
    assert not (source/'new.txt').exists()
