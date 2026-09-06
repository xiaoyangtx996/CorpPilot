"""Explicit offline release of a restored copy after external-effect review."""
import argparse
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .directory_lock import acquire


def plain(path):
    path = Path(path).absolute()
    for node in (path, *path.parents):
        if node.is_symlink() or node.exists() and getattr(node.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('恢复路径不能经过链接或重解析点')
    return path


def activate(data_dir, *, original_stopped=False, effects_checked=False):
    if original_stopped is not True or effects_checked is not True:
        raise ValueError('必须确认原副本不再运行并已核对外部副作用；解除后原排队授权可能继续执行')
    directory = plain(data_dir)
    marker = plain(directory / 'restore-quarantine.json')
    complete = plain(directory / 'restore-complete.json')
    database = plain(directory / 'workbench.sqlite3')
    with ExitStack() as stack:
        plain(directory / 'controller.lock')
        stack.enter_context(acquire(directory))
        if not marker.is_file() or marker.stat().st_size > 32768:
            raise ValueError('恢复隔离记录不存在或无效')
        value = json.loads(marker.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'version', 'backup', 'source_data_dir'}
                or type(value['version']) is not int or value['version'] != 1
                or any(not isinstance(value[key], str) or not Path(value[key]).is_absolute() for key in ('backup', 'source_data_dir'))):
            raise ValueError('恢复隔离记录无效，未解除')
        if not complete.is_file() or complete.stat().st_size > 32768:
            raise ValueError('恢复尚未完整完成，不能解除隔离')
        completion = json.loads(complete.read_text(encoding='utf-8'))
        if not isinstance(completion, dict) or type(completion.get('version')) is not int or completion != {'version': 1, 'backup': value['backup']}:
            raise ValueError('恢复完成证据不一致，不能解除隔离')
        original = plain(value['source_data_dir'])
        if original == directory:
            raise ValueError('原副本与恢复副本不能相同')
        if original.exists():
            plain(original / 'controller.lock')
            stack.enter_context(acquire(original))
        if not database.is_file():
            raise ValueError('恢复数据库不存在')
        db = stack.enter_context(closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)))
        db.execute('BEGIN')
        for table, declarations, key in (('runs', 'model_run_reconciliations', 'run_id'),
                                          ('task_executions', 'execution_reconciliations', 'execution_id')):
            if db.execute(f"SELECT 1 FROM {table} WHERE state IN ('running','stopping') LIMIT 1").fetchone():
                raise ValueError('仍有运行记录；先启动隔离服务恢复状态并核查，再关闭服务解除隔离')
            if db.execute(f"SELECT 1 FROM {table} r WHERE state='unknown' AND NOT EXISTS(SELECT 1 FROM {declarations} c WHERE c.{key}=r.id) LIMIT 1").fetchone():
                raise ValueError('仍有未核查的未知执行或模型调用，不能解除恢复隔离')
        audit = directory / f'restore-release-{uuid.uuid4()}.json'
        receipt = {**value, 'original_stopped': True, 'effects_checked': True}
        with audit.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        marker.unlink()
        return {'released': True, 'receipt': str(audit), 'message': '下次启动可能继续此前已授权队列；不要同时启动旧副本'}


def main():
    parser = argparse.ArgumentParser(description='离线解除恢复副本的调度隔离；不会启动服务')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--confirm-original-stopped', action='store_true')
    parser.add_argument('--confirm-external-effects-checked', action='store_true')
    args = parser.parse_args()
    try:
        result = activate(args.data_dir, original_stopped=args.confirm_original_stopped,
                          effects_checked=args.confirm_external_effects_checked)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'未解除恢复隔离：{error}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
