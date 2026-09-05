"""Real Windows children, confined to the job created by run_process."""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workbench.process_tree import run_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")


def execute(tmp_path, code, **kwargs):
    return run_process([sys.executable, "-u", "-c", code], tmp_path,
                       dict(os.environ), kwargs.pop("stdin", b""),
                       kwargs.pop("timeout_seconds", 5), **kwargs)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_real_exit_and_both_streams(tmp_path, exit_code):
    result = execute(tmp_path, "import sys; print(sys.stdin.read()); "
                     f"print('err',file=sys.stderr); sys.exit({exit_code})", stdin=b"hello")
    assert result == {"exit_code": exit_code, "reason": "exited",
                      "stdout": b"hello\r\n", "stderr": b"err\r\n"}


def test_blocked_stdin_obeys_total_timeout(tmp_path):
    started = time.monotonic()
    result = execute(tmp_path, "import time; time.sleep(60)",
                     stdin=b"x" * (2 * 1024 * 1024), timeout_seconds=0.3)
    assert result["reason"] == "timeout"
    assert result["exit_code"] is not None
    assert time.monotonic() - started < 4


def test_cancel_and_pre_cancel(tmp_path):
    cancel = threading.Event()
    cancel.set()
    result = execute(tmp_path, "open('should-not-exist','w').close()", cancel=cancel)
    assert result["reason"] == "cancelled" and result["exit_code"] is None
    assert not (tmp_path / "should-not-exist").exists()
    cancel.clear()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        result = execute(tmp_path, "import time; time.sleep(60)", cancel=cancel)
        assert result["reason"] == "cancelled"
        assert result["exit_code"] is not None
    finally:
        timer.join()


def test_bounded_dual_stream_output(tmp_path):
    result = execute(tmp_path, "import os\nwhile True:\n os.write(1,b'a'*65536)\n os.write(2,b'b'*65536)",
                     output_limit_bytes=100000)
    assert result["reason"] == "output_limit"
    assert len(result["stdout"]) + len(result["stderr"]) == 100000
    assert result["exit_code"] is not None


@pytest.mark.parametrize("parent_waits", [False, True])
def test_descendants_are_stopped_before_return(tmp_path, parent_waits):
    # A grandchild repeatedly writes a heartbeat; the child waits for startup so
    # the test proves a live descendant existed before its parent exits.
    grandchild = "import time; from pathlib import Path\nwhile True:\n Path('heartbeat').write_text(str(time.time_ns())); time.sleep(.02)"
    child = ("import subprocess,sys,time; from pathlib import Path; "
             f"subprocess.Popen([sys.executable,'-u','-c',{grandchild!r}]); "
             "\nwhile not Path('heartbeat').exists(): time.sleep(.01)\ntime.sleep(60)")
    parent = ("import subprocess,sys,time; from pathlib import Path; "
              f"subprocess.Popen([sys.executable,'-u','-c',{child!r}]); "
              "\nwhile not Path('heartbeat').exists(): time.sleep(.01)\n"
              + ("time.sleep(60)" if parent_waits else "sys.exit(0)"))
    result = execute(tmp_path, parent, timeout_seconds=1 if parent_waits else 5)
    assert result["reason"] == ("timeout" if parent_waits else "exited")
    assert result["exit_code"] is not None
    heartbeat = tmp_path / "heartbeat"
    assert heartbeat.exists()
    stopped = heartbeat.read_bytes()
    time.sleep(.15)
    assert heartbeat.read_bytes() == stopped


def test_start_failure_has_no_sensitive_error(tmp_path):
    result = run_process([str(tmp_path / "missing-secret-executable")], tmp_path,
                         {"SECRET": "private-value"}, b"", 1)
    assert result == {"exit_code": None, "reason": "start_failed", "stdout": b"", "stderr": b""}


@pytest.mark.parametrize("invalid", [
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": True}, {"timeout_seconds": "1"},
    {"output_limit_bytes": True}, {"output_limit_bytes": 1.5},
    {"output_limit_bytes": -1},
])
def test_invalid_limits_do_not_start_process(tmp_path, invalid):
    result = execute(tmp_path, "open('should-not-exist','w').close()", **invalid)
    assert result == {"exit_code": None, "reason": "start_failed", "stdout": b"", "stderr": b""}
    assert not (tmp_path / "should-not-exist").exists()


def test_controller_crash_closes_its_job(tmp_path):
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    child = "import time; from pathlib import Path\nwhile True:\n Path('crash-heartbeat').write_text(str(time.time_ns())); time.sleep(.02)"
    controller = (
        "import os,sys,time,threading; from pathlib import Path\n"
        f"sys.path.insert(0,{scripts!r})\nfrom workbench.process_tree import run_process\n"
        "def crash():\n while not Path('crash-heartbeat').exists(): time.sleep(.01)\n os._exit(23)\n"
        "threading.Thread(target=crash,daemon=True).start()\n"
        f"run_process([sys.executable,'-u','-c',{child!r}],Path.cwd(),dict(os.environ),b'',10)\n")
    (tmp_path / "controller.py").write_text(controller, encoding="utf-8")
    # Keep the enclosing safety job alive while verifying the crashed controller's
    # own job killed its child. Thus outer-job cleanup cannot make this check pass.
    witness = (
        "import subprocess,sys,time; from pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'controller.py']); assert p.wait()==23\n"
        "time.sleep(.2); f=Path('crash-heartbeat'); before=f.read_bytes()\n"
        "time.sleep(.2); assert f.read_bytes()==before; print('verified')\n")
    result = execute(tmp_path, witness)
    assert result["reason"] == "exited" and result["exit_code"] == 0
    assert result["stdout"].strip() == b"verified"
