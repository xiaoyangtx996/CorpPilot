"""Offline, allowlisted workbench records; never a complete tool workspace backup."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import os
import sqlite3
import stat
import sys
import uuid

from .directory_lock import acquire

MAX_TOTAL = 2 * 1024 ** 3
MAX_FILES = 10002


def safe(path):
    path = Path(os.path.abspath(path))
    for node in (path, *path.parents):
        if node.is_symlink() or node.exists() and getattr(node.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('备份路径不能经过链接或重解析点')
    return path


def limit(name):
    if name == 'workbench.sqlite3': return 1024 ** 3
    if name == 'reply-usage.jsonl': return 100 * 1024 ** 2
    parts = name.split('/')
    if len(parts) == 3 and parts[0] == 'execution-workspaces' and parts[2] == 'docker-worker.json':
        try:
            if str(uuid.UUID(parts[1])) == parts[1]: return 16384
        except ValueError: pass
    raise ValueError('备份包含非白名单路径')


def digest(path, maximum):
    path = safe(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('备份文件类型或大小无效')
    h, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum: raise ValueError('备份文件超过大小上限')
            h.update(chunk)
    safe(path)
    return size, h.hexdigest()


def database(path, *, immutable=True):
    with closing(sqlite3.connect(safe(path).as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('备份数据库完整性校验失败')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = {'schema_version': 2, 'tasks_schema_version': 1,
                    'runs_schema_version': 1, 'executions_schema_version': 1}
        required = {'agents', 'tasks', 'task_revisions', 'runs', 'task_executions',
                    'execution_reconciliations', 'model_run_reconciliations', 'execution_artifacts'}
        if not required | versions.keys() <= tables:
            raise ValueError('备份需要已初始化的完整工作台数据库')
        for table, expected in versions.items():
            if db.execute(f'SELECT version FROM {table}').fetchall() != [(expected,)]:
                raise ValueError('备份数据库版本不受支持')


def copy(source, target, maximum):
    safe(source)
    size = 0
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        while chunk := incoming.read(1024 * 1024):
            size += len(chunk)
            if size > maximum: raise ValueError('复制文件超过大小上限')
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def worker(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    required = {'version','execution_id','token','name','image','phase','container_id','executable'}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - {'image_id','exit_code'}:
        raise ValueError('Docker 证据字段无效')
    token = value['token']
    if (type(value['version']) is not int or value['version'] != 1 or value['execution_id'] != path.parent.name
        or not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token)
        or value['name'] != f"corppilot-{token[:12]}-{path.parent.name}"
        or value['phase'] not in ('create_intent','created','start_intent','stopped','removed','remove_failed')
        or not isinstance(value['image'],str) or not re.fullmatch(r'(?:sha256:|[A-Za-z0-9._:/-]+@sha256:)[0-9a-f]{64}',value['image'])
        or value['container_id'] is not None and (not isinstance(value['container_id'],str) or not re.fullmatch('[0-9a-f]{64}',value['container_id']))
        or not isinstance(value['executable'],str) or not value['executable']
        or 'image_id' in value and (not isinstance(value['image_id'],str) or not re.fullmatch('sha256:[0-9a-f]{64}',value['image_id']))
        or 'exit_code' in value and (type(value['exit_code']) is not int or not 0 <= value['exit_code'] <= 255)):
        raise ValueError('Docker 证据内容无效')


def verify(bundle):
    bundle = safe(bundle)
    manifest_path = bundle / 'manifest.json'
    digest(manifest_path, 4 * 1024 ** 2)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest,dict) or set(manifest) != {'version','source_data_dir','files'}
        or type(manifest['version']) is not int or manifest['version'] != 1
        or not isinstance(manifest['source_data_dir'],str) or not Path(manifest['source_data_dir']).is_absolute()
        or not isinstance(manifest['files'],list) or not 1 <= len(manifest['files']) <= MAX_FILES):
        raise ValueError('备份清单无效')
    names, total = set(), 0
    for item in manifest['files']:
        if not isinstance(item,dict) or set(item) != {'path','size','sha256'} or not isinstance(item['path'],str):
            raise ValueError('备份文件清单无效')
        name = item['path']
        maximum = limit(name)
        if name in names or type(item['size']) is not int or not 0 <= item['size'] <= maximum or not isinstance(item['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',item['sha256']):
            raise ValueError('备份文件清单无效')
        names.add(name)
        total += item['size']
        if total > MAX_TOTAL: raise ValueError('备份总量超过上限')
        if digest(bundle / name, maximum) != (item['size'],item['sha256']): raise ValueError('备份文件哈希或大小不符')
        if name.endswith('docker-worker.json'): worker(bundle / name)
    if 'workbench.sqlite3' not in names: raise ValueError('备份缺少数据库')
    # Walk only after checking each directory: never follow an untrusted reparse point.
    pending, actual, entries = [bundle], set(), 0
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            entries += 1
            if entries > MAX_FILES * 2 + 2: raise ValueError('备份目录条目超过上限')
            safe(path)
            if path.is_dir(): pending.append(path)
            elif path.is_file(): actual.add(path.relative_to(bundle).as_posix())
            else: raise ValueError('备份包含特殊文件')
            if len(actual) + len(pending) > MAX_FILES + 2: raise ValueError('备份文件数量超过上限')
    if actual != names | {'manifest.json'}: raise ValueError('备份含未登记文件')
    database(bundle / 'workbench.sqlite3')
    return manifest


def backup(source, destination):
    source, destination = safe(source), safe(destination)
    if not source.is_dir() or destination.exists() or destination.is_relative_to(source):
        raise ValueError('源须存在，备份目标须不存在且位于源目录外')
    safe(source / 'controller.lock')
    with acquire(source):
        database(source / 'workbench.sqlite3', immutable=False)
        destination.mkdir(parents=True, exist_ok=False)
        with closing(sqlite3.connect((source / 'workbench.sqlite3').as_uri() + '?mode=ro',uri=True)) as db:
            with closing(sqlite3.connect(destination / 'workbench.sqlite3')) as output:
                db.backup(output)
                output.execute('PRAGMA journal_mode=DELETE')
        names = ['workbench.sqlite3']
        if (source / 'reply-usage.jsonl').exists(): names.append('reply-usage.jsonl')
        root = safe(source / 'execution-workspaces')
        if root.exists():
            for directory in root.iterdir():
                safe(directory)
                record = directory / 'docker-worker.json'
                if record.exists():
                    name = record.relative_to(source).as_posix()
                    limit(name)
                    names.append(name)
                if len(names) > MAX_FILES: raise ValueError('备份文件数量超过上限')
        if sum(safe((destination if name == 'workbench.sqlite3' else source) / name).stat().st_size for name in names) > MAX_TOTAL:
            raise ValueError('备份总量超过上限')
        for name in names[1:]:
            digest(source / name, limit(name))
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            copy(source / name,target,limit(name))
        files = []
        for name in sorted(names):
            size, checksum = digest(destination / name,limit(name))
            files.append({'path':name,'size':size,'sha256':checksum})
        (destination / 'manifest.json').write_text(json.dumps({'version':1,'source_data_dir':str(source),'files':files},ensure_ascii=False,indent=2),encoding='utf-8')
        return verify(destination)


def restore(bundle, destination):
    bundle, destination = safe(bundle), safe(destination)
    manifest = verify(bundle)
    if destination.exists() or destination.is_relative_to(bundle): raise ValueError('恢复目标须是备份目录外的不存在目录')
    destination.mkdir(parents=True,exist_ok=False)
    with acquire(destination):
        with (destination / 'restore-quarantine.json').open('x',encoding='utf-8') as marker:
            json.dump({'version':1,'backup':str(bundle),'source_data_dir':manifest['source_data_dir']},marker,ensure_ascii=False)
            marker.flush()
            os.fsync(marker.fileno())
        for item in manifest['files']:
            name = item['path']
            target = destination / name
            target.parent.mkdir(parents=True,exist_ok=True)
            # Verify the copied bytes too; changed source cannot silently pass restore.
            copy(safe(bundle / name),target,limit(name))
            if digest(target,limit(name)) != (item['size'],item['sha256']): raise ValueError('恢复期间备份内容已变化；目标保留隔离状态')
        database(destination / 'workbench.sqlite3')
        with (destination / 'restore-complete.json').open('x',encoding='utf-8') as complete:
            json.dump({'version':1,'backup':str(bundle)},complete,ensure_ascii=False)
            complete.flush()
            os.fsync(complete.fileno())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['backup','verify','restore'])
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path,nargs='?')
    args = parser.parse_args()
    if args.command != 'verify' and args.destination is None: parser.error('需要目标目录')
    try:
        result = verify(args.source) if args.command == 'verify' else globals()[args.command](args.source,args.destination)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'备份操作失败：{exc}',file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
