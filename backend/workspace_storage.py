"""Move a complete, stopped local workspace once, independently of application releases."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

try:
    from backend.knowledge.managed import DirectoryLease, ManagedPostgres
    from backend.knowledge.backup import restore_workspace, workspace_key
except ModuleNotFoundError:
    from knowledge.managed import DirectoryLease, ManagedPostgres
    from knowledge.backup import restore_workspace, workspace_key

SCHEMA = 'kb0012'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect(root):
    root = Path(root).expanduser().resolve()
    if (root / '.workspace-copy-incomplete').exists() or (root / '.candidate-incomplete').exists():
        raise ValueError('此目录的复制尚未完成，请选择已经校验的资料目录')
    if not (root / 'library.sqlite').is_file():
        raise ValueError('没有找到工作台资料，请选择包含 library.sqlite 的完整资料目录')
    with closing(sqlite3.connect((root / 'library.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('项目数据库完整性检查未通过')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'projects', 'app_settings', 'artifacts'} <= tables:
            raise ValueError('这不是可识别的工作台资料目录')
    config = json.loads((root / 'knowledge.json').read_text(encoding='utf-8')) if (root / 'knowledge.json').exists() else {}
    external = config.get('mode') == 'external' or config.get('enabled') and config.get('mode', 'auto') == 'auto'
    if external or not (root / '.managed-postgres/credentials.json').is_file() or not (root / 'knowledge-db/PG_VERSION').is_file():
        raise ValueError('此操作需要完整的内置数据库资料；外部数据库请使用成套备份恢复')
    if (root / 'knowledge-db/PG_VERSION').read_text().strip() != '17':
        raise ValueError('数据库版本不兼容，请使用对应版本的工作台')
    return {'path': str(root), 'local': True}


def migrate(source, target, progress=lambda value: None):
    source, target = Path(source).resolve(), Path(target).resolve()
    inspect(source)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('新资料目录必须独立于原资料目录')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('迁移目标必须是空目录，不能覆盖现有文件')
    if any(p.lower() == 'release' for p in target.parts):
        raise ValueError('请选择 release 发布目录之外的固定位置')
    state = source / '.managed-postgres/runtime-state.json'
    if (source / 'knowledge-db/postmaster.pid').exists() or not state.exists() or not json.loads(state.read_text(encoding='utf-8')).get('clean_shutdown'):
        raise ValueError('请先正常关闭使用原资料目录的工作台，再迁移')
    lease = DirectoryLease(source / '.managed-postgres/owner.lock')
    lease.acquire()
    manager = None
    try:
        if (source / 'knowledge-db/postmaster.pid').exists():
            raise ValueError('原数据库仍在运行')
        target.mkdir(parents=True, exist_ok=True)
        marker = target / '.workspace-copy-incomplete'
        marker.write_text(json.dumps({'source': str(source)}), encoding='utf-8')
        excluded = {'Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GPUPersistentCache',
                    'GrShaderCache', 'ShaderCache', 'Network', 'Session Storage', 'Shared Dictionary', 'Local Storage'}
        files = []
        for directory, names, filenames in os.walk(source, followlinks=False):
            folder = Path(directory)
            names[:] = [n for n in names if not (folder == source and n in excluded)]
            for name in names:
                if (folder / name).is_symlink() or (hasattr(Path, 'is_junction') and (folder / name).is_junction()):
                    raise ValueError('资料中包含目录链接，请改用成套备份恢复')
            (target / folder.relative_to(source)).mkdir(parents=True, exist_ok=True)
            for name in filenames:
                path = folder / name
                if name == 'owner.lock' or folder == source and name in {'library.sqlite', 'library.sqlite-wal', 'library.sqlite-shm'}:
                    continue
                if path.is_symlink():
                    raise ValueError('资料中包含文件链接，请改用成套备份恢复')
                files.append(path)
        total, copied, verified, last = sum(p.stat().st_size for p in files), 0, [], 0
        with closing(sqlite3.connect(source / 'library.sqlite', timeout=2)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            for path in files:
                relative = path.relative_to(source)
                destination = target / relative
                before = path.stat()
                sha = digest(path)
                shutil.copy2(path, destination)
                if digest(destination) != sha or path.stat().st_mtime_ns != before.st_mtime_ns:
                    raise ValueError('复制期间文件发生变化：' + str(relative))
                copied += before.st_size
                verified.append({'path': relative.as_posix(), 'sha256': sha, 'bytes': before.st_size})
                if time.monotonic() - last > 1:
                    progress({'stage': 'copy', 'copied': copied, 'total': total, 'files': len(verified)})
                    last = time.monotonic()
            with closing(sqlite3.connect((source / 'library.sqlite').as_uri() + '?mode=ro', uri=True)) as reader, closing(sqlite3.connect(target / 'library.sqlite')) as destination:
                reader.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('项目数据库复制校验失败')
                # Check every old SQLite row before rebinding file locations.
                for (name,) in reader.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    quoted = '"' + name.replace('"', '""') + '"'
                    if reader.execute('SELECT * FROM '+quoted).fetchall() != destination.execute('SELECT * FROM '+quoted).fetchall():
                        raise ValueError('项目记录核验不一致：' + name)
                for identity, asset in destination.execute('SELECT id,asset_path FROM materials').fetchall():
                    path = Path(asset)
                    if path.is_absolute() and source in path.resolve().parents:
                        destination.execute('UPDATE materials SET asset_path=? WHERE id=?', (str(target / path.resolve().relative_to(source)), identity))
                destination.commit()
        progress({'stage': 'verify', 'copied': copied, 'total': total})
        manager = ManagedPostgres(target)
        manager.start()
        with manager.connect() as connection:
            schema = connection.execute('SELECT version_num FROM kb_schema_version').fetchone()[0]
            if schema != SCHEMA:
                raise ValueError('请先使用匹配的数据版本完成升级，再迁移资料目录')
            restore_workspace(connection, target, {'workspace_key': workspace_key(source)}, native=True)
        manager.stop(); manager = None
        report = {'source': str(source), 'target': str(target), 'schema': schema, 'verified_files': len(verified),
                  'verified_bytes': copied, 'sqlite_rows_verified': True, 'source_preserved': True}
        (target / 'workspace-migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (target / 'workspace-copy-sha256.json').write_text(json.dumps(verified, ensure_ascii=False), encoding='utf-8')
        marker.unlink()
        inspect(target)
        progress({'stage': 'complete', **report})
        return report
    finally:
        if manager:
            manager.stop()
        lease.release()
