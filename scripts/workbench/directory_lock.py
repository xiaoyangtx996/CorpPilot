"""Shared lifetime lock for the service and offline maintenance commands."""
import os
from pathlib import Path


def acquire(data_dir):
    stream = open(Path(data_dir) / 'controller.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise ValueError('此数据目录已有运行中的控制服务或维护命令') from None
    return stream
