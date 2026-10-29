"""Offline, explicit publication of a legacy account into an empty shared library.

The source database and original files are retained. No migration runs during a
login. Source IDs and SQLite snapshots remain valid after the move.
"""
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
import shutil
import sqlite3

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from .identity import workspace_key
from .portal_accounts import Accounts
from .portal_database import native_url, provision, save_private_json
from .portal_fleet import WorkspaceFleet
from .portal_shared_database import provision_private
from .knowledge.database import SCHEMA_REVISION
from .knowledge.managed import DirectoryLease
from .knowledge.shared import SHARED_TABLES


def publish_legacy(source_url, library, private, source_root):
    """Copy one stopped kb0012 library, then validate every restored FK."""
    source_root = Path(source_root).resolve()
    destination = Path(library['root']).resolve()
    schema = private['schema']
    with psycopg.connect(native_url(source_url)) as old, psycopg.connect(native_url(library['owner_url'])) as new:
        old.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if old.execute('SELECT version_num FROM public.kb_schema_version').fetchone() != (SCHEMA_REVISION,):
            raise RuntimeError('旧库需要先由对应版本升级到当前文献结构')
        if old.execute("SELECT 1 FROM kb_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise RuntimeError('旧库仍有未完成的文献任务，请先完成或取消')
        new.execute('SELECT pg_advisory_xact_lock(824171,1)')
        receipt=new.execute('SELECT to_regclass(%s)',(schema+'.wj_legacy_publication',)).fetchone()[0]
        if receipt:
            identity=new.execute(sql.SQL('SELECT source_identity FROM {}.wj_legacy_publication').format(sql.Identifier(schema))).fetchone()[0]
            if identity != workspace_key(source_root):raise RuntimeError('此迁移分区已用于其他资料目录')
            return
        if new.execute('SELECT 1 FROM public.kb_works LIMIT 1').fetchone() or new.execute('SELECT 1 FROM public.kb_corpora LIMIT 1').fetchone():
            raise RuntimeError('迁移入口只接受空公共库；已有资料请使用管理员导入，不能覆盖公共库')
        if new.execute(sql.SQL('SELECT 1 FROM {}.kb_catalog_activation LIMIT 1').format(sql.Identifier(schema))).fetchone():
            raise RuntimeError('目标个人分区已有数据，不能覆盖')
        tables = [row[0] for row in old.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND starts_with(tablename,'kb_') AND tablename<>'kb_schema_version' ORDER BY tablename")]
        # Copy originals before publishing DB rows. Any failed copy leaves the old
        # account intact and only unreferenced content-addressed files in the new root.
        for key,digest in old.execute("SELECT storage_key,sha256 FROM kb_assets WHERE availability='present'"):
            source, target = (source_root/key).resolve(), (destination/key).resolve()
            if not source.is_relative_to(source_root) or not target.is_relative_to(destination) or not key.startswith('assets/'):
                raise RuntimeError('旧库存在不受支持的原件路径，请先核对')
            if not source.is_file():
                raise RuntimeError('旧库原件缺失或校验失败，迁移未发布')
            with source.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest() != digest:
                    raise RuntimeError('旧库原件缺失或校验失败，迁移未发布')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                with target.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest() != digest:
                        raise RuntimeError('公共库原件校验失败')
            else:
                temporary=target.with_name(target.name+'.'+secrets.token_hex(8)+'.tmp')
                try:
                    shutil.copy2(source,temporary)
                    with temporary.open('rb') as stream:
                        if hashlib.file_digest(stream,'sha256').hexdigest() != digest:
                            raise RuntimeError('原件复制校验失败，迁移未发布')
                    os.replace(temporary,target)
                finally:
                    temporary.unlink(missing_ok=True)
        constraints=[]
        for table in tables:
            namespace = 'public' if table in SHARED_TABLES else schema
            for name,definition in new.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype='f'", (namespace+'.'+table,)).fetchall():
                constraints.append((namespace,table,name,definition))
                new.execute(sql.SQL('ALTER TABLE {}.{} DROP CONSTRAINT {}').format(sql.Identifier(namespace),sql.Identifier(table),sql.Identifier(name)))
        for table in tables:
            namespace = 'public' if table in SHARED_TABLES else schema
            # Empty-library hosts may already have initialized these two counters.
            if table in {'kb_document_library_state','kb_literature_category_state','kb_libraries'}:
                new.execute(sql.SQL('DELETE FROM public.{}').format(sql.Identifier(table)))
            with old.cursor().copy(sql.SQL('COPY public.{} TO STDOUT').format(sql.Identifier(table))) as outgoing:
                with new.cursor().copy(sql.SQL('COPY {}.{} FROM STDIN').format(sql.Identifier(namespace),sql.Identifier(table))) as incoming:
                    for data in outgoing:incoming.write(data)
            has_workspace=old.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name='workspace_key'",(table,)).fetchone()
            if has_workspace:
                new.execute(sql.SQL('UPDATE {}.{} SET workspace_key=%s').format(sql.Identifier(namespace),sql.Identifier(table)),(library['key'],))
        # ADD validates all existing rows, including cyclic revision/assistant FKs.
        for namespace,table,name,definition in constraints:
            new.execute(sql.SQL('ALTER TABLE {}.{} ADD CONSTRAINT {} ').format(sql.Identifier(namespace),sql.Identifier(table),sql.Identifier(name)) + sql.SQL(definition))
        new.execute(sql.SQL('CREATE TABLE {}.wj_legacy_publication (source_identity text NOT NULL, published_at timestamptz NOT NULL DEFAULT now())').format(sql.Identifier(schema)))
        new.execute(sql.SQL('INSERT INTO {}.wj_legacy_publication(source_identity) VALUES (%s)').format(sql.Identifier(schema)),(workspace_key(source_root),))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--username',required=True)
    parser.add_argument('--publish-library',action='store_true',help='明确将此旧账号的全部文献及原件发布到公共库')
    args=parser.parse_args()
    if not args.publish_library:
        parser.error('需要 --publish-library 明确授权发布旧文献；个人任务、笔记与成果仍保密')
    root=args.data_dir.resolve()
    accounts=Accounts(root/'private')
    user=next((accounts.get(item['id']) for item in accounts.list() if item['username']==args.username),None)
    if not user:parser.error('账号不存在')
    portal_lock=DirectoryLease(root/'private'/'portal.lock')
    portal_lock.acquire()
    fleet=WorkspaceFleet(root)
    products=[]
    locks=[]
    try:
        fleet.request_stop_all([accounts.get(item['id']) for item in accounts.list()])
        workspace=root/'workspaces'/user['workspace_id']
        if not (workspace/'library.sqlite').is_file():
            raise RuntimeError('此账号没有旧工作区资料')
        with sqlite3.connect(workspace/'library.sqlite') as local:
            for table in ('tasks','preprocess_jobs','collation_batches','collation_debug_runs'):
                if local.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone() and local.execute("SELECT 1 FROM "+table+" WHERE status IN ('queued','running','pausing','stopping') LIMIT 1").fetchone():
                    raise RuntimeError('请先完成或取消旧账号任务，再做离线迁移')
        for identity in (user,fleet.library):
            lease=DirectoryLease(fleet.runtime(identity)/'host.lock');lease.acquire();locks.append(lease)
        runtime=fleet.runtime(user)
        config=runtime/'database.json'
        legacy=json.loads(config.read_text(encoding='utf-8')) if config.exists() else None
        if legacy and legacy.get('mode')=='shared':raise RuntimeError('此账号已使用公共库')
        from .knowledge.product_runtime import ProductDatabase
        if legacy:
            source_url=legacy['runtime_url']
        else:
            product=ProductDatabase(workspace,'internal');product.start();products.append(product)
            if product.startup_error:raise RuntimeError('旧数据库无法启动')
            source_url=product.manager.url.render_as_string(hide_password=False)
        shared_root=root/'shared-library'
        library_runtime=fleet.runtime(fleet.library)
        admin_url=os.environ.get('SHIXUE_PORTAL_PG_ADMIN_URL')
        if admin_url:
            url=provision(admin_url,fleet.library['workspace_id'],library_runtime/'database.json')
            owner=json.loads((library_runtime/'database.json').read_text(encoding='utf-8'))['owner_url']
        else:
            product=ProductDatabase(shared_root,'internal');product.start();products.append(product)
            if product.startup_error:raise RuntimeError('公共数据库无法启动')
            url=owner=product.manager.url.render_as_string(hide_password=False)
        library={'root':str(shared_root),'key':workspace_key(shared_root),'runtime_url':url,'owner_url':owner,'admin_url':admin_url or owner}
        pending=runtime/'shared-database.pending.json'
        private=provision_private(library,user['workspace_id'],pending)
        publish_legacy(source_url,library,private,workspace)
        if legacy:save_private_json(runtime/'legacy-database.json',legacy)
        save_private_json(config,private)
        with accounts.connection() as db:
            accounts.audit(db,None,'library.legacy.publish',user['id'])
        print('旧文献已发布到公共库，个人研究记录与原 ID 已保留；旧数据库仍保留在原位置')
    finally:
        for product in reversed(products):product.stop()
        for lease in reversed(locks):lease.release()
        portal_lock.release()


if __name__=='__main__':main()
