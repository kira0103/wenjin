"""Verified bundle export and restore to an empty, independent destination."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from . import repository as r
from .audit import read_only


def workspace_key(root):
    return hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()


def restore_workspace(connection,destination,manifest,native=False):
    """Rebind only this bundle's workspace, preserving unrelated shared-DB rows."""
    def execute(statement,values=None):
        return connection.execute(statement,values or {}) if native else connection.execute(text(statement),values or {})
    tables=[name for name in ('kb_jobs','kb_retrieval_runs','kb_eval_suites','kb_retrieval_settings','kb_research_runs',
                             'kb_assistant_definitions','kb_skill_tests','kb_collaboration_runs',
                             'kb_literature_category_state','kb_literature_categories',
                             'kb_document_library_state','kb_document_groups','kb_document_entries','kb_document_imports')
            if execute("SELECT to_regclass('public."+name+"')").fetchone()[0]]
    source=manifest.get('workspace_key')
    if source is None:
        keys={row[0] for name in tables for row in execute('SELECT DISTINCT workspace_key FROM '+name).fetchall()}
        if len(keys)>1:raise ValueError('旧备份包含多个工作区，无法唯一识别恢复范围，请使用原目录生成新版备份')
        source=next(iter(keys),None)
    if source is None:return
    key_parameter='%(key)s' if native else ':key'
    old_parameter='%(old)s' if native else ':old'
    for name in tables:
        execute('UPDATE '+name+' SET workspace_key='+key_parameter+' WHERE workspace_key='+old_parameter,
                {'key':workspace_key(destination),'old':source})


def pg_command(root,operation,filename=None,url=None):
    engine=r.database.engine(root)
    url=url or engine.url
    environment=os.environ.copy()
    from .config import managed_url
    from .managed import runtime_directory
    bundled=runtime_directory()/'bin'/(operation+'.exe' if os.name=='nt' else operation)
    executable=str(bundled) if bundled.is_file() else shutil.which(operation)
    # The Docker fallback is only for a development connection; bundled tools
    # also support explicit external restore connections.
    if executable:
        command=[executable,'--host',url.host or '127.0.0.1','--port',str(url.port or 5432),'--username',url.username,'--dbname',url.database]
        environment['PGPASSWORD']=url.password or ''
        if operation=='pg_dump':command+=['--format=custom','--no-owner','--no-privileges']
        else:command+=['--no-owner','--no-privileges','--exit-on-error','--single-transaction']
    elif url.host in {'127.0.0.1','localhost'} and url.port==55432:
        # Development fallback uses local container authentication; no password argument.
        command=['docker','exec','-i','shixue-knowledge-postgres-1',operation,'--username','shixue_admin','--dbname',url.database]
        if operation=='pg_dump':command+=['--format=custom','--no-owner','--no-privileges']
        else:command+=['--no-owner','--no-privileges','--exit-on-error','--single-transaction']
    else:raise HTTPException(503,'没有找到 PostgreSQL 备份工具，请安装匹配版本的 pg_dump/pg_restore')
    # Full core corpora can exceed 20 GB; a four-minute cap aborts healthy dumps.
    options={'env':environment,'stderr':subprocess.PIPE,'timeout':1800}
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    if operation=='pg_dump':
        with Path(filename).open('wb') as file:subprocess.run(command,stdout=file,check=True,**options)
    else:
        with Path(filename).open('rb') as file:subprocess.run(command,stdin=file,stdout=subprocess.DEVNULL,check=True,**options)


def export_bundle(root,check=lambda:None,progress=lambda *args:None):
    identifier=r.uid();destination=root/'exports';destination.mkdir(exist_ok=True)
    temporary=destination/(identifier+'.partial');target=destination/(identifier+'.zip')
    engine=r.database.engine(root)
    check();progress('database',10)
    with tempfile.TemporaryDirectory(prefix='kb-export-') as folder:
        stage=Path(folder)
        with engine.begin() as lock,closing(sqlite3.connect(root/'library.sqlite',timeout=10)) as local:
            lock.execute(text('SELECT pg_advisory_xact_lock(824171,2)'))
            local.execute('BEGIN IMMEDIATE')
            if local.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise HTTPException(409,'请等待旧任务结束，再导出完整备份')
            for table in ('collation_batches','collation_debug_runs'):
                if local.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone() and local.execute('SELECT 1 FROM '+table+" WHERE status IN ('running','pausing','stopping') LIMIT 1").fetchone():
                    raise HTTPException(409,'请暂停点校批次后再备份')
            pg_command(root,'pg_dump',stage/'knowledge.dump')
            # A valid archive must not conceal missing or externally modified originals.
            for asset in r.rows(lock,"SELECT storage_key,sha256 FROM kb_assets WHERE availability='present'"):
                path=r.asset_path(root,asset['storage_key'])
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=asset['sha256']:
                        raise HTTPException(409,'原件哈希不一致，请核验原件后备份')
            with closing(read_only(root/'library.sqlite')) as source,closing(sqlite3.connect(stage/'library.sqlite')) as copy:source.backup(copy)
            paths=[stage/'knowledge.dump',stage/'library.sqlite']
            if (root/'knowledge.json').is_file():paths.append(root/'knowledge.json')
            assets=[]
            for directory in ('assets','artifacts','checkpoints'):
                base=root/directory
                if base.exists():assets.extend(file for file in base.rglob('*') if file.is_file() and not file.name.endswith(('.tmp','-wal','-shm')))
            material_paths={}
            has_kind=any(item[1]=='kind' for item in local.execute('PRAGMA table_info(materials)'))
            material_query="SELECT id,asset_path FROM materials WHERE kind<>'working_input'" if has_kind else 'SELECT id,asset_path FROM materials'
            for material_id,asset_path in local.execute(material_query):
                asset=Path(asset_path);asset=(asset if asset.is_absolute() else root/asset).resolve()
                if not asset.is_file():continue
                if root.resolve() not in asset.parents:
                    relative=Path('assets/legacy-backup')/str(material_id)/asset.name
                    copied=stage/relative;copied.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(asset,copied);assets.append(copied)
                    material_paths[material_id]=relative.as_posix();continue
                material_paths[material_id]=str(asset.relative_to(root.resolve())).replace('\\','/')
            actual_revision=r.execute(lock,'SELECT version_num FROM kb_schema_version').scalar_one()
            manifest={'format':1,'schema_revision':actual_revision,'workspace_key':workspace_key(root),'models_included':False,'credentials_included':False,'material_paths':material_paths,'files':{}}
            checkpoints=[]
            if (root/'checkpoints').exists():
                for source_path in (root/'checkpoints').rglob('*.sqlite'):
                    relative=source_path.relative_to(root);snapshot=stage/relative;snapshot.parent.mkdir(parents=True,exist_ok=True)
                    with closing(read_only(source_path)) as source,closing(sqlite3.connect(snapshot)) as copy:source.backup(copy)
                    checkpoints.append((snapshot,str(relative).replace('\\','/')))
            assets=[path for path in assets if not (path.suffix=='.sqlite' and 'checkpoints' in path.parts)]
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                all_files=[(path,path.name) for path in paths]+[(path,(path.relative_to(stage) if stage in path.parents else path.relative_to(root)).as_posix()) for path in assets]+checkpoints
                for index,(path,name) in enumerate(all_files):
                    # Cancellation heartbeat needs DB writes, which are deliberately paused here.
                    with path.open('rb') as stream:manifest['files'][name]=hashlib.file_digest(stream,'sha256').hexdigest()
                    archive.write(path,name)
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
            local.rollback()
    verify_bundle(temporary)
    temporary.replace(target)
    return {'storage_key':str(target.relative_to(root)).replace('\\','/'),'files':len(manifest['files'])}


def verify_bundle(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('备份文件校验失败')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!=1:raise ValueError('不支持的备份格式')
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('备份存在重复路径')
        for name in names:
            part=Path(name)
            if part.is_absolute() or '..' in part.parts or ':' in name or '\\' in name:raise ValueError('备份包含不安全路径')
        if set(names)!=(set(manifest['files'])|{'manifest.json'}):raise ValueError('备份清单与内容不一致')
        if any(path not in manifest['files'] for path in manifest.get('material_paths',{}).values()):raise ValueError('备份中的原件定位不完整')
        for name,expected in manifest['files'].items():
            with archive.open(name) as file:
                if hashlib.file_digest(file,'sha256').hexdigest()!=expected:raise ValueError('备份内容摘要不匹配')
        return manifest


def restore_bundle(bundle,destination,url):
    from .database import build_engine
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('恢复目录必须不存在，禁止覆盖现有资料')
    manifest=verify_bundle(bundle)
    engine=build_engine(url)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%'")).scalar_one():
                raise ValueError('恢复数据库必须为空')
        with tempfile.TemporaryDirectory(prefix='kb-restore-') as folder:
            stage=Path(folder)
            with zipfile.ZipFile(bundle) as archive:archive.extractall(stage)
            with closing(read_only(stage/'library.sqlite')) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite 校验失败')
            pg_command(stage,'pg_restore',stage/'knowledge.dump',engine.url)
            with engine.connect() as connection:
                exists=connection.execute(text("SELECT to_regclass('public.kb_schema_version')")).scalar()
                actual=connection.execute(text('SELECT version_num FROM kb_schema_version')).scalar_one() if exists else None
                if actual!=manifest['schema_revision']:raise ValueError('恢复 schema 版本不匹配')
            with engine.begin() as connection:
                if actual:
                    connection.execute(text("UPDATE kb_jobs SET state='failed',error='恢复后请人工重试中断任务' WHERE state IN ('queued','running')"))
                    restore_workspace(connection,destination,manifest)
                if actual and connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname='shixue_app'")).scalar():
                    connection.execute(text('GRANT USAGE,CREATE ON SCHEMA public TO shixue_owner'))
                    for name in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'kb_%'")).scalars().all():
                        quoted=connection.dialect.identifier_preparer.quote(name)
                        connection.execute(text('ALTER TABLE public.'+quoted+' OWNER TO shixue_owner'))
                    connection.execute(text('ALTER FUNCTION public.kb_immutable_source() OWNER TO shixue_owner'))
                    connection.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO shixue_app'))
                    connection.execute(text('REVOKE INSERT,UPDATE,DELETE ON kb_schema_version FROM shixue_app'))
                    connection.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
            with closing(sqlite3.connect(stage/'library.sqlite')) as local:
                for material_id,relative in manifest.get('material_paths',{}).items():
                    local.execute('UPDATE materials SET asset_path=? WHERE id=?',(str(destination/relative),material_id))
                local.commit()
            # Restoring a copy must never reconnect it to the source database.
            previous=stage/'knowledge.json'
            if previous.exists():previous.rename(stage/'connection-before-restore.json')
            previous.write_text(json.dumps({'mode':'external','enabled':False}),encoding='utf-8')
            shutil.copytree(stage,destination)
            (destination/'knowledge.dump').unlink()
    finally:engine.dispose()
    return {'restored':True,'files':len(manifest['files']),'configure_connection':True}


def restore_managed_bundle(bundle,destination):
    """Restore a new owned cluster, then publish the entire directory at once."""
    from .managed import ManagedPostgres
    from .config import register_managed,unregister_managed
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('恢复目录必须不存在，禁止覆盖现有资料')
    manifest=verify_bundle(bundle)
    if any(Path(name).parts[0] in {'knowledge-db','.managed-postgres'} for name in manifest['files']):raise ValueError('备份不能包含数据库物理目录或凭据')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=destination.parent/('.shixue-restore-'+r.uid())
    stage.mkdir();marker=stage/'.restore-staging';marker.write_text('restore-staging-v1',encoding='ascii')
    manager=ManagedPostgres(stage);stopped=False
    try:
        manager.start();register_managed(stage,manager.url)
        payload=stage/'restored-documents'
        result=restore_bundle(bundle,payload,manager.url)
        with closing(sqlite3.connect(payload/'library.sqlite')) as local:
            for material,relative in manifest.get('material_paths',{}).items():
                local.execute('UPDATE materials SET asset_path=? WHERE id=?',(str(destination/relative),material))
            local.commit()
        if manifest['schema_revision']:
            with manager.connect() as connection:
                restore_workspace(connection,destination,{'workspace_key':workspace_key(payload)},native=True)
        for source in list(payload.iterdir()):
            if (stage/source.name).exists():raise ValueError('备份内容与恢复目录冲突')
            source.rename(stage/source.name)
        payload.rmdir()
        (stage/'knowledge.json').write_text(json.dumps({'mode':'internal','enabled':False}),encoding='utf-8')
        r.database.dispose();manager.stop();stopped=True;unregister_managed(stage)
        marker.unlink();stage.rename(destination)
        return {**result,'configure_connection':False,'mode':'internal','destination':str(destination)}
    finally:
        r.database.dispose()
        if not stopped:manager.stop()
        unregister_managed(stage)
        if stage.exists() and marker.is_file() and stage.resolve().parent==destination.parent and stage.name.startswith('.shixue-restore-'):
            shutil.rmtree(stage)
