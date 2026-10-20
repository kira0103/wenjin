"""Verified offline copy on the same machine; never upgrades or starts the source."""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.knowledge.managed import ManagedPostgres, DirectoryLease
from backend.knowledge.backup import restore_workspace, workspace_key
from backend.knowledge import repository as r
from backend.knowledge.config import register_managed, unregister_managed

SOURCE = ROOT / 'release/v0.5.0-rc.6/史学工作台验证数据'
TARGET = ROOT / 'release/v0.5.0-rc.7/史学工作台验证数据'
OUTPUT = ROOT / '.local/submissions-20261018'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def counts(db):
    return {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}


def main():
    if '--verify-existing' in sys.argv:
        return verify_existing()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ.pop('SHIXUE_KB_DATABASE_URL', None)
    if TARGET.exists():
        raise RuntimeError('目标资料目录已存在，禁止覆盖')
    if SOURCE.resolve().parent != (ROOT / 'release/v0.5.0-rc.6').resolve():
        raise RuntimeError('来源目录异常')
    manifest = SOURCE / '.managed-postgres/runtime-state.json'
    if (SOURCE / 'knowledge-db/postmaster.pid').exists() or not json.loads(manifest.read_text(encoding='utf-8')).get('clean_shutdown'):
        raise RuntimeError('请先正常关闭源候选版；不会复制正在运行的数据库')
    lease = DirectoryLease(SOURCE / '.managed-postgres/owner.lock')
    lease.acquire()
    manager = None
    started = time.monotonic()
    try:
        # Hold the source's database lease and SQLite write reservation throughout the copy.
        with closing(sqlite3.connect(SOURCE / 'library.sqlite', timeout=2)) as source:
            source.execute('BEGIN IMMEDIATE')
            before_counts = counts(source)
            TARGET.mkdir(parents=True)
            marker = TARGET / '.candidate-incomplete'
            marker.write_text('Copy verification in progress', encoding='utf-8')
            folders = ('knowledge-db', '.managed-postgres', 'assets', 'blob_storage', 'artifacts',
                       'checkpoints', 'models', 'rerank-models')
            for folder in folders:
                for directory in [SOURCE / folder, *(SOURCE / folder).rglob('*')]:
                    if directory.is_symlink():
                        raise RuntimeError('来源资料包含链接，未继续复制')
                    if directory.is_dir():
                        (TARGET / directory.relative_to(SOURCE)).mkdir(parents=True, exist_ok=True)
            files = [p for folder in folders for p in (SOURCE / folder).rglob('*') if p.is_file()]
            files += [p for p in SOURCE.iterdir() if p.is_file() and p.name not in
                      {'library.sqlite', 'library.sqlite-wal', 'library.sqlite-shm'} and p.suffix not in {'.db', '.db-journal'}]
            files = [p for p in files if p.name != 'owner.lock']
            copied, total, hashes, last_log = 0, sum(p.stat().st_size for p in files), [], time.monotonic()
            for path in files:
                if path.is_symlink():
                    raise RuntimeError('来源资料包含链接，未继续复制')
                relative = path.relative_to(SOURCE)
                destination = TARGET / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                stat = path.stat()
                digest = sha(path)
                shutil.copy2(path, destination)
                if sha(destination) != digest or path.stat().st_mtime_ns != stat.st_mtime_ns or path.stat().st_size != stat.st_size:
                    raise RuntimeError('复制期间来源或目标内容变化：' + str(relative))
                hashes.append({'path': relative.as_posix(), 'size': stat.st_size, 'sha256': digest})
                copied += stat.st_size
                if time.monotonic() - last_log > 3:
                    print(json.dumps({'copy_gb': round(copied/1e9, 2), 'total_gb': round(total/1e9, 2)}), flush=True)
                    last_log = time.monotonic()
            # A separate reader can take a consistent backup while this connection excludes writers.
            with closing(sqlite3.connect(SOURCE / 'library.sqlite')) as reader, closing(sqlite3.connect(TARGET / 'library.sqlite')) as target:
                reader.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or counts(target) != before_counts:
                    raise RuntimeError('项目数据库复制校验失败')
                for identity, asset in target.execute('SELECT id,asset_path FROM materials').fetchall():
                    path = Path(asset)
                    if path.is_absolute() and SOURCE.resolve() in path.resolve().parents:
                        target.execute('UPDATE materials SET asset_path=? WHERE id=?', (str(TARGET / path.resolve().relative_to(SOURCE.resolve())), identity))
                target.commit()
            if counts(source) != before_counts or (SOURCE / 'knowledge-db/postmaster.pid').exists():
                raise RuntimeError('源资料发生变化，复制未发布')
            source.rollback()
        (OUTPUT / 'candidate-copy-sha256.json').write_text(json.dumps(hashes, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'copy_complete': True, 'files': len(hashes), 'bytes': copied}), flush=True)
        manager = ManagedPostgres(TARGET)
        manager.start()
        register_managed(TARGET, manager.url)
        with manager.connect() as connection:
            restore_workspace(connection, TARGET, {'workspace_key': workspace_key(SOURCE)}, native=True)
        from backend import app as service
        service.initialize(TARGET)
        with service.connection() as target:
            after_counts = counts(target)
            if any(after_counts[name] != count for name, count in before_counts.items()):
                raise RuntimeError('新版本旧工作台资料数量不一致')
            journals = target.execute('SELECT count(*) FROM submission_journals').fetchone()[0]
            if journals != 935:
                raise RuntimeError('期刊目录数量不一致')
        with r.database.transaction(TARGET) as connection:
            schema = r.execute(connection, 'SELECT version_num FROM kb_schema_version').scalar_one()
            books = r.execute(connection, 'SELECT count(*) FROM kb_core_books').scalar_one()
            assets = r.execute(connection, 'SELECT count(*) FROM kb_core_files f JOIN kb_editions e ON e.active_revision=f.revision_id').scalar_one()
            chunks = r.execute(connection, 'SELECT count(*) FROM kb_chunks c JOIN kb_editions e ON e.active_index=c.index_id').scalar_one()
        from backend.knowledge import assistant_registry
        skills = len(assistant_registry.list_definitions(TARGET, 'skill'))
        agents = len(assistant_registry.list_definitions(TARGET, 'agent'))
        report = {'source': str(SOURCE), 'target': str(TARGET), 'ready': True, 'schema': schema,
                  'copy_mode': 'exclusive-clean-shutdown-physical-copy', 'source_unmodified': True,
                  'verified_files': len(hashes), 'verified_bytes': copied, 'sqlite_counts_before': before_counts,
                  'sqlite_counts_after': after_counts, 'journals': journals, 'books': books, 'files': assets,
                  'active_chunks': chunks, 'skills': skills, 'agents': agents,
                  'elapsed_seconds': round(time.monotonic()-started, 1)}
        if books != 15295 or assets != 28950 or skills < 6 or agents < 5:
            raise RuntimeError('旧文献或助手配置核验未通过')
        (OUTPUT / 'candidate-data-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        marker.unlink()
        print(json.dumps({k: report[k] for k in ('ready', 'journals', 'books', 'files', 'skills', 'agents')}), flush=True)
    finally:
        r.database.dispose()
        if manager:
            manager.stop()
        unregister_managed(TARGET)
        lease.release()


def verify_existing():
    """Resume only verification of this script's completed, unpublished copy."""
    marker = TARGET / '.candidate-incomplete'
    hashes_path = OUTPUT / 'candidate-copy-sha256.json'
    if not marker.is_file() or not hashes_path.is_file() or (TARGET / 'knowledge-db/postmaster.pid').exists():
        raise RuntimeError('没有可继续校验的已完成副本，或副本仍在运行')
    hashes = json.loads(hashes_path.read_text(encoding='utf-8'))
    lease = DirectoryLease(SOURCE / '.managed-postgres/owner.lock')
    lease.acquire()
    manager = ManagedPostgres(TARGET)
    try:
        if (SOURCE / 'knowledge-db/postmaster.pid').exists():
            raise RuntimeError('源资料正在运行，无法完成离线核对')
        with closing(sqlite3.connect(SOURCE / 'library.sqlite')) as source, closing(sqlite3.connect(TARGET / 'library.sqlite')) as target:
            source.execute('BEGIN IMMEDIATE')
            before, after = counts(source), counts(target)
            for table, count in before.items():
                if after.get(table) != count:
                    raise RuntimeError('原资料数量不符：' + table)
                quoted = '"' + table.replace('"', '""') + '"'
                original = sorted(json.dumps(row, ensure_ascii=False, default=str) for row in source.execute('SELECT * FROM ' + quoted))
                copied = sorted(json.dumps(tuple(str(v).replace(str(TARGET), str(SOURCE)) if isinstance(v, str) else v for v in row),
                                           ensure_ascii=False, default=str) for row in target.execute('SELECT * FROM ' + quoted))
                if original != copied:
                    raise RuntimeError('原资料内容不符：' + table)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('本机项目数据库完整性校验失败')
            journals = target.execute('SELECT count(*) FROM submission_journals').fetchone()[0]
            manuscripts = target.execute('SELECT count(*) FROM submission_papers').fetchone()[0]
        manager.start(); register_managed(TARGET, manager.url)
        with manager.connect() as connection:
            restore_workspace(connection, TARGET, {'workspace_key': workspace_key(SOURCE)}, native=True)
        with r.database.transaction(TARGET) as connection:
            schema = r.execute(connection, 'SELECT version_num FROM kb_schema_version').scalar_one()
            books = r.execute(connection, 'SELECT count(*) FROM kb_core_books').scalar_one()
            files = r.execute(connection, 'SELECT count(*) FROM kb_core_files f JOIN kb_editions e ON e.active_revision=f.revision_id').scalar_one()
            historical_files = r.execute(connection, 'SELECT count(*) FROM kb_core_files').scalar_one()
            chunks = r.execute(connection, 'SELECT count(*) FROM kb_chunks c JOIN kb_editions e ON e.active_index=c.index_id').scalar_one()
        from backend.knowledge import assistant_registry
        skills = len(assistant_registry.list_definitions(TARGET, 'skill'))
        agents = len(assistant_registry.list_definitions(TARGET, 'agent'))
        if (journals, manuscripts, schema, books, files, skills, agents) != (935, 0, 'kb0010', 15295, 28950, 6, 5):
            raise RuntimeError('副本数量或版本核验不符')
        report = {'source': str(SOURCE), 'target': str(TARGET), 'ready': True, 'schema': schema,
                  'copy_mode': 'exclusive-clean-shutdown-physical-copy', 'source_unmodified': True,
                  'verified_files': len(hashes), 'verified_bytes': sum(v['size'] for v in hashes),
                  'sqlite_counts_before': before, 'sqlite_counts_after': after, 'old_sqlite_rows_identical': True,
                  'journals': journals, 'manuscripts': manuscripts, 'books': books, 'files': files,
                  'all_historical_core_file_records': historical_files, 'active_chunks': chunks, 'skills': skills, 'agents': agents}
        (OUTPUT / 'candidate-data-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        marker.unlink()
        print(json.dumps({k: report[k] for k in ('ready', 'journals', 'books', 'files', 'skills', 'agents')}))
    finally:
        r.database.dispose(); manager.stop(); unregister_managed(TARGET); lease.release()


if __name__ == '__main__':
    main()
