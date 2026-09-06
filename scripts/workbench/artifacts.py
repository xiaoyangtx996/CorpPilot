"""Bounded, immutable copies of explicitly exported CLI artifacts."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import uuid

MAX_FILES = 100
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_DEPTH = 12
ERROR = "成果读取或校验失败：请检查文件类型、大小和敏感内容"


def _identity(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError(ERROR)
    return value


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError(ERROR)
    parts = value.split("/")
    if len(parts) > MAX_DEPTH or any(
        not part or part in (".", "..") or part.endswith((".", " "))
        or any(unicodedata.category(c).startswith("C") or c in '\\:<>"|?*' for c in part)
        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError(ERROR)
    return value


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS execution_artifacts (
        id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES task_executions(id),
        path TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>=0), sha256 TEXT NOT NULL,
        content BLOB NOT NULL, UNIQUE(execution_id,path))""")
    for action in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS artifacts_no_{action.lower()}
            BEFORE {action} ON execution_artifacts BEGIN
            SELECT RAISE(ABORT, 'Artifacts are immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS artifacts_no_replace
        BEFORE INSERT ON execution_artifacts WHEN EXISTS
        (SELECT 1 FROM execution_artifacts WHERE id=NEW.id OR
            (execution_id=NEW.execution_id AND path=NEW.path))
        BEGIN SELECT RAISE(ABORT, 'Artifacts are immutable'); END""")


@contextmanager
def _locked(path, directory):
    # Windows directory handles deny rename/delete while descendants are read.
    import ctypes
    import msvcrt
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_void_p]
    kernel.GetFileType.argtypes = [w.HANDLE]
    kernel.GetFileType.restype = w.DWORD
    class Info(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("times", w.FILETIME * 3),
                    ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                    ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3):
            raise FileNotFoundError()
        raise ValueError(ERROR)
    try:
        info = Info()
        if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                or kernel.GetFileType(handle) != 1
                or info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
                or (not directory and info.links != 1)):
            raise ValueError(ERROR)
        if directory:
            yield None
        else:
            if (info.size_high << 32 | info.size_low) > MAX_FILE_BYTES:
                raise ValueError(ERROR)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None  # fd owns the handle now.
            with os.fdopen(fd, "rb") as stream:
                yield stream
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def capture(data_dir: Path, execution_id: str, api_key: str) -> list[dict]:
    """Call only after trusted runner confirms process-tree exit; never use runner paths."""
    try:
        _identity(execution_id)
        if os.name != "nt" or not isinstance(api_key, str) or not api_key.strip():
            raise ValueError(ERROR)
        secret = api_key.strip()
        secrets = [secret.encode("utf-8"), secret.encode("utf-16-le"), secret.encode("utf-16-be")]
        root = Path(data_dir).absolute() / "execution-workspaces" / execution_id / "work" / "artifacts"
        items, total, entries = [], 0, 0
        with ExitStack() as locks:
            for parent in reversed((root, *root.parents)):
                try:
                    locks.enter_context(_locked(parent, True))
                except FileNotFoundError:
                    return []

            def visit(folder, prefix="", depth=1):
                nonlocal total, entries
                with os.scandir(folder) as scan:
                    for entry in scan:
                        entries += 1
                        if entries > MAX_ENTRIES or depth > MAX_DEPTH:
                            raise ValueError(ERROR)
                        relative = _path(prefix + entry.name)
                        if secret in relative:
                            raise ValueError(ERROR)
                        # lstat chooses expected type only; the opened handle validates it again.
                        directory = entry.is_dir(follow_symlinks=False)
                        with _locked(Path(entry.path), directory) as stream:
                            if directory:
                                visit(Path(entry.path), relative + "/", depth + 1)
                            else:
                                data = stream.read(MAX_FILE_BYTES + 1)
                                total += len(data)
                                if (len(items) >= MAX_FILES or len(data) > MAX_FILE_BYTES
                                        or total > MAX_TOTAL_BYTES or any(key in data for key in secrets)):
                                    raise ValueError(ERROR)
                                items.append({"path": relative, "data": data})
            visit(root)
        return sorted(items, key=lambda item: item["path"])
    except (OSError, ValueError, TypeError, OverflowError):
        raise ValueError(ERROR) from None


def persist(db, execution_id, items):
    _identity(execution_id)
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError(ERROR)
    rows, paths, total = [], set(), 0
    for item in items:
        if not isinstance(item, dict) or set(item) != {"path", "data"} or not isinstance(item["data"], bytes):
            raise ValueError(ERROR)
        path, data = _path(item["path"]), item["data"]
        total += len(data)
        if path.casefold() in paths or len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError(ERROR)
        paths.add(path.casefold())
        rows.append((str(uuid.uuid4()), execution_id, path, len(data), hashlib.sha256(data).hexdigest(), data))
    if not db.execute("SELECT 1 FROM task_executions WHERE id=?", (execution_id,)).fetchone():
        raise KeyError("任务执行不存在")
    db.execute("SAVEPOINT artifact_insert")
    try:
        db.executemany("INSERT INTO execution_artifacts VALUES (?,?,?,?,?,?)", rows)
    except Exception:
        db.execute("ROLLBACK TO artifact_insert")
        raise
    finally:
        db.execute("RELEASE artifact_insert")


def list_for(store, execution_id):
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM task_executions WHERE id=?", (execution_id,)).fetchone():
            raise KeyError("任务执行不存在")
        return [dict(row) for row in db.execute(
            "SELECT id,execution_id,path,size,sha256 FROM execution_artifacts WHERE execution_id=? ORDER BY path", (execution_id,))]


def get(store, artifact_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM execution_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError("成果不存在")
        return verify_snapshot(row)


def verify_snapshot(row):
    """Validate the same stored bytes for downloads and Owner approval."""
    result = dict(row)
    content = result.pop("content")
    if (not isinstance(content, bytes) or len(content) != result["size"]
            or len(content) > MAX_FILE_BYTES or hashlib.sha256(content).hexdigest() != result["sha256"]):
        raise ValueError(ERROR)
    _path(result["path"])
    return {**result, "data": content}


def input_snapshots(db, bindings, *, downstream_execution_id=None):
    """Read only direct, frozen inputs; caller holds the authorization transaction."""
    from . import dependencies
    if downstream_execution_id:
        run = db.execute('SELECT * FROM task_executions WHERE id=?', (downstream_execution_id,)).fetchone()
        if run is None or run['state'] != 'running' or dependencies.bound_inputs(db, downstream_execution_id) != bindings:
            raise ValueError('前置成果不属于本次运行的固定输入')
        dependencies.check_bound(db, dict(run))
    selected, total = [], 0
    for binding in bindings:
        identity = binding["upstream_execution_id"]
        review = db.execute("SELECT decision,artifact_ids FROM execution_reviews WHERE execution_id=?",
                            (identity,)).fetchone()
        metadata = db.execute("""SELECT id,size,length(content) byte_count FROM execution_artifacts
            WHERE execution_id=? ORDER BY id LIMIT ?""", (identity, MAX_FILES + 1)).fetchall()
        authorized = bool(downstream_execution_id) and dependencies.handoff_source(db, downstream_execution_id, binding['dependency_task_id']) == identity
        if (not metadata or (review is not None and review['decision'] == 'rejected')
                or (not authorized and (review is None or review['decision'] != 'approved'))
                or (review is not None and review['decision'] == 'approved'
                    and [row['id'] for row in metadata] != json.loads(review['artifact_ids']))):
            raise ValueError("前置成果与已批准清单不一致，未启动执行")
        for row in metadata:
            total += row["byte_count"]
            if (len(selected) >= MAX_FILES or row["size"] != row["byte_count"]
                    or row["byte_count"] > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES):
                raise ValueError("前置成果超过输入限额或大小不一致，未启动执行")
            stored = db.execute("SELECT * FROM execution_artifacts WHERE id=?", (row["id"],)).fetchone()
            selected.append(verify_snapshot(stored))
    return selected
