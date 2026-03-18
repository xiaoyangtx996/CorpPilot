#!/usr/bin/env python3
"""
CorpPilot File Lock
文件锁机制，避免并发写入时发生冲突。
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path


class FileLockError(Exception):
    """文件锁异常。"""


class FileLock:
    """跨平台文件锁实现，支持 Windows 和 Unix。"""

    def __init__(self, lock_file: str, timeout: float = 30.0):
        """初始化文件锁。"""
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self._fd = None
        self._locked = False

    def acquire(self, blocking: bool = True) -> bool:
        """获取文件锁。"""
        start_time = time.time()

        while True:
            try:
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                self._fd = open(self.lock_file, "w")

                if os.name == "nt":
                    import msvcrt

                    try:
                        msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
                        self._locked = True
                        return True
                    except OSError:
                        self._fd.close()
                        self._fd = None
                        if not blocking:
                            return False
                else:
                    import fcntl

                    flag = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                    fcntl.flock(self._fd.fileno(), flag)
                    self._locked = True
                    return True

            except (IOError, OSError):
                if not blocking:
                    return False

                if time.time() - start_time >= self.timeout:
                    raise FileLockError(f"获取文件锁超时: {self.lock_file}")

                time.sleep(0.1)

    def release(self):
        """释放文件锁。"""
        if not self._locked or self._fd is None:
            return

        try:
            if os.name == "nt":
                import msvcrt

                self._fd.seek(0)
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()
            self._fd = None
            self._locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    def __del__(self):
        self.release()


@contextmanager
def with_file_lock(lock_file: str, timeout: float = 30.0):
    """文件锁上下文管理器。"""
    lock = FileLock(lock_file, timeout)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()


def lock_task_file(task_id: str, operation: str = "write") -> FileLock:
    """为任务文件返回锁实例。"""
    project_root = Path(__file__).parent.parent
    lock_dir = project_root / "data" / "locks"
    lock_file = lock_dir / f"{task_id}.{operation}.lock"
    return FileLock(str(lock_file))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "test.lock")
        print("测试文件锁...")
        with with_file_lock(lock_file):
            print("已获取锁")
            print(f"锁文件: {lock_file}")
            time.sleep(1)
        print("已释放锁")
