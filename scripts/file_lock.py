#!/usr/bin/env python3
"""
CorpPilot File Lock
文件锁机制，防止多 Agent 并发写入冲突
"""

import os
import time
from pathlib import Path
from typing import Optional
from contextlib import contextmanager


class FileLockError(Exception):
    """文件锁异常"""
    pass


class FileLock:
    """
    文件锁实现
    支持 Unix (fcntl) 和 Windows (msvcrt) 平台
    """
    
    def __init__(self, lock_file: str, timeout: float = 30.0):
        """
        初始化文件锁
        
        Args:
            lock_file: 锁文件路径
            timeout: 获取锁的超时时间（秒）
        """
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self._fd = None
        self._locked = False
    
    def acquire(self, blocking: bool = True) -> bool:
        """
        获取文件锁
        
        Args:
            blocking: 是否阻塞等待
            
        Returns:
            是否成功获取锁
        """
        start_time = time.time()
        
        while True:
            try:
                # 创建锁文件目录
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                
                # 打开锁文件
                self._fd = open(self.lock_file, 'w')
                
                if os.name == 'nt':
                    # Windows 平台使用 msvcrt
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
                    # Unix 平台使用 fcntl
                    import fcntl
                    flag = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                    fcntl.flock(self._fd.fileno(), flag)
                    self._locked = True
                    return True
                    
            except (IOError, OSError):
                if not blocking:
                    return False
                    
                # 检查超时
                if time.time() - start_time >= self.timeout:
                    raise FileLockError(
                        f"获取文件锁超时: {self.lock_file}"
                    )
                
                time.sleep(0.1)
    
    def release(self):
        """释放文件锁"""
        if not self._locked or self._fd is None:
            return
        
        try:
            if os.name == 'nt':
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
    """
    文件锁上下文管理器
    
    Usage:
        with with_file_lock("/path/to/lock"):
            # 执行需要加锁的操作
            pass
    """
    lock = FileLock(lock_file, timeout)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()


def lock_task_file(task_id: str, operation: str = "write") -> FileLock:
    """
    获取任务文件锁
    
    Args:
        task_id: 任务ID
        operation: 操作类型
        
    Returns:
        FileLock 实例
    """
    from pathlib import Path
    project_root = Path(__file__).parent.parent
    lock_dir = project_root / "data" / "locks"
    lock_file = lock_dir / f"{task_id}.lock"
    return FileLock(str(lock_file))


if __name__ == "__main__":
    # 测试文件锁
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "test.lock")
        
        print("测试文件锁...")
        
        with with_file_lock(lock_file):
            print("✅ 获取锁成功")
            print(f"   锁文件: {lock_file}")
            time.sleep(1)
        
        print("✅ 释放锁成功")
