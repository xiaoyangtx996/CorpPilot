"""Bounded Windows process execution; the entire descendant tree belongs to one job."""
import os
import math
import subprocess
import threading
import time
from pathlib import Path


def run_process(argv: list[str], cwd: Path, env: dict[str, str], stdin: bytes,
                timeout_seconds: float, cancel: threading.Event | None = None,
                output_limit_bytes: int = 4 * 1024 * 1024) -> dict:
    result = {"exit_code": None, "reason": "start_failed", "stdout": b"", "stderr": b""}
    if cancel is not None and cancel.is_set():
        result["reason"] = "cancelled"
        return result
    if (os.name != "nt" or not argv
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
            or isinstance(output_limit_bytes, bool)
            or not isinstance(output_limit_bytes, int) or output_limit_bytes < 0):
        return result
    # _winapi is the stdlib primitive used by subprocess. Popen discards the
    # primary thread handle, so cannot safely assign a suspended process to a job.
    import _winapi
    import ctypes
    import msvcrt
    from ctypes import wintypes as w

    class BasicLimit(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_limit", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                    ("scheduling", w.DWORD)]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("basic", BasicLimit), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [("times", ctypes.c_int64 * 4), ("faults", w.DWORD),
                    ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, restype in [
        ("CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
        ("QueryInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
        ("ResumeThread", [w.HANDLE], w.DWORD),
        ("CloseHandle", [w.HANDLE], w.BOOL),
    ]:
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = args, restype

    def active_processes():
        info = Accounting()
        if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError("job accounting unavailable")
        return info.active

    job = process = primary_thread = None
    fds = []
    workers = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    lock = threading.Lock()
    overflow = threading.Event()
    io_error = threading.Event()
    assigned = False
    deadline = time.monotonic() + timeout_seconds

    def drain(fd, stream):
        try:
            with os.fdopen(fd, "rb", buffering=0) as pipe:
                while chunk := pipe.read(65536):
                    with lock:
                        remaining = output_limit_bytes - sum(map(len, buffers.values()))
                        buffers[stream].extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            overflow.set()
        except OSError:
            io_error.set()

    def feed(fd):
        try:
            with os.fdopen(fd, "wb", buffering=0) as pipe:
                data = memoryview(stdin)
                while data:
                    written = pipe.write(data[:65536])
                    if not written:
                        break
                    data = data[written:]
        except OSError:
            pass  # A program may intentionally close stdin without consuming it.

    try:
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError("job creation failed")
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError("job limits failed")
        for _ in range(3):
            fds.extend(os.pipe())
        input_read, input_write, output_read, output_write, error_read, error_write = fds
        child_fds = [input_read, output_write, error_write]
        handles = [msvcrt.get_osfhandle(fd) for fd in child_fds]
        for fd in child_fds:
            os.set_inheritable(fd, True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
        startup.lpAttributeList = {"handle_list": handles}
        if cancel is not None and cancel.is_set():
            result["reason"] = "cancelled"
            return result
        process, primary_thread, _, _ = _winapi.CreateProcess(
            None, subprocess.list2cmdline(argv), None, None, True,
            0x00000004 | subprocess.CREATE_NO_WINDOW, env, str(cwd), startup)
        for fd in child_fds:
            os.close(fd)
            fds.remove(fd)
        if not kernel.AssignProcessToJobObject(job, process):
            raise OSError("job assignment failed")
        assigned = True
        for target, args in [(drain, (output_read, "stdout")),
                             (drain, (error_read, "stderr")), (feed, (input_write,))]:
            worker = threading.Thread(target=target, args=args, daemon=True)
            worker.start()
            workers.append(worker)
            fds.remove(args[0])  # The worker now owns this descriptor.
        if kernel.ResumeThread(primary_thread) == 0xFFFFFFFF:
            raise OSError("process resume failed")
        result["reason"] = "unknown"
        while True:
            if cancel is not None and cancel.is_set():
                result["reason"] = "cancelled"
                break
            if overflow.is_set():
                result["reason"] = "output_limit"
                break
            if time.monotonic() >= deadline:
                result["reason"] = "timeout"
                break
            if _winapi.WaitForSingleObject(process, 0) == _winapi.WAIT_OBJECT_0:
                result["reason"] = "exited"
                break
            time.sleep(0.01)
    except Exception:
        # Errors must not expose arguments, environment values or platform messages.
        result["reason"] = "unknown" if assigned else "start_failed"
    finally:
        confirmed_empty = False
        try:
            if assigned:
                if active_processes():
                    if not kernel.TerminateJobObject(job, 1):
                        raise OSError("job termination failed")
                cleanup_deadline = time.monotonic() + 5
                while active_processes() and time.monotonic() < cleanup_deadline:
                    time.sleep(0.01)
                confirmed_empty = active_processes() == 0
                if not confirmed_empty:
                    result["reason"] = "unknown"
            elif process:
                _winapi.TerminateProcess(process, 1)  # Still suspended; has no descendants.
                _winapi.WaitForSingleObject(process, 5000)
        except Exception:
            result["reason"] = "unknown"
        finally:
            if job:
                kernel.CloseHandle(job)
            for fd in fds:
                os.close(fd)
            for worker in workers:
                worker.join(timeout=2)
            if any(worker.is_alive() for worker in workers) or io_error.is_set():
                result["reason"] = "unknown"
            if overflow.is_set() and result["reason"] == "exited":
                result["reason"] = "output_limit"
            if confirmed_empty and result["reason"] != "unknown":
                try:
                    if _winapi.WaitForSingleObject(process, 1000) != _winapi.WAIT_OBJECT_0:
                        result["reason"] = "unknown"
                    else:
                        result["exit_code"] = _winapi.GetExitCodeProcess(process)
                except Exception:
                    result["reason"] = "unknown"
            if primary_thread:
                _winapi.CloseHandle(primary_thread)
            if process:
                _winapi.CloseHandle(process)
            with lock:
                result.update({name: bytes(value) for name, value in buffers.items()})
    return result
