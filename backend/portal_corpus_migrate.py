"""Copy only one processed core corpus into an empty portal system library.

Original files and the source database are retained. Personal research, other
literature, notes, accounts and model credentials are never published.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

import psycopg
from psycopg import sql

from .portal_database import native_url
from .knowledge.managed import ManagedPostgres

EDITION="SELECT edition_id FROM public.kb_core_books WHERE corpus_id=%(corpus)s"
REVISION=f"SELECT active_revision FROM public.kb_editions WHERE id IN ({EDITION})"
INDEX=f"SELECT active_index FROM public.kb_editions WHERE id IN ({EDITION})"
ASSET=f"SELECT DISTINCT asset_id FROM public.kb_core_files WHERE revision_id IN ({REVISION})"
SELECTION=[
    ('kb_works',f'id IN (SELECT work_id FROM public.kb_editions WHERE id IN ({EDITION}))'),
    ('kb_editions',f'id IN ({EDITION})'),('kb_assets',f'id IN ({ASSET})'),
    ('kb_pages',f'asset_id IN ({ASSET})'),('kb_page_labels',f'asset_id IN ({ASSET})'),
    ('kb_corpora','id=%(corpus)s'),('kb_revisions',f'id IN ({REVISION})'),
    ('kb_indexes',f'id IN ({INDEX})'),
    ('kb_edition_assets',f'edition_id IN ({EDITION}) AND asset_id IN ({ASSET})'),
    ('kb_core_books','corpus_id=%(corpus)s'),('kb_core_files',f'revision_id IN ({REVISION})'),
    ('kb_blocks',f'revision_id IN ({REVISION})'),('kb_chunks',f'index_id IN ({INDEX})'),
    ('kb_core_passages',f'corpus_id=%(corpus)s AND revision_id IN ({REVISION})')]


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def copy_checked(source,target,expected):
    if not source.is_file() or source.is_symlink():raise RuntimeError('源原件缺失或是链接，迁移未发布')
    if digest(source)!=expected:raise RuntimeError('源原件与已加工数据库不一致，迁移未发布')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if digest(target)!=expected:raise RuntimeError('后台已有同路径但不同内容的文件，迁移未发布')
        return
    temporary=target.with_name(target.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        shutil.copy2(source,temporary)
        if digest(temporary)!=expected:raise RuntimeError('后台原件复制校验失败，迁移未发布')
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)


def contained(root,relative):
    value=(root/relative).resolve()
    if value==root or not value.is_relative_to(root):raise RuntimeError('语料路径越界')
    return value


def copy_rows(old,new,table,where,values,overrides=None):
    columns=[row[0] for row in old.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,))]
    if 'owner_role' in columns:
        if old.execute(sql.SQL('SELECT 1 FROM public.{} WHERE '+where+' AND owner_role IS NOT NULL LIMIT 1').format(sql.Identifier(table)),values).fetchone():raise RuntimeError('所选语料包含私人文献，迁移已停止')
        columns.remove('owner_role')
    expressions=[sql.SQL((overrides or {})[name]) if name in (overrides or {}) else sql.Identifier(name) for name in columns]
    outgoing=sql.SQL('COPY (SELECT {} FROM public.{} WHERE '+where+') TO STDOUT (FORMAT BINARY)').format(sql.SQL(',').join(expressions),sql.Identifier(table))
    incoming=sql.SQL('COPY public.{} ({}) FROM STDIN (FORMAT BINARY)').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)))
    with old.cursor().copy(outgoing,values) as reader,new.cursor().copy(incoming) as writer:
        for block in reader:writer.write(block)


def copy_corpus(old,new,source_path,destination,slug='daizhige',progress=lambda *a:None):
    source_path=Path(source_path).resolve();destination=Path(destination).resolve()
    source=old.execute('SELECT id,source_revision,state,statistics FROM public.kb_corpora WHERE slug=%s',(slug,)).fetchone()
    if not source or source[2]!='ready':raise RuntimeError('原语料库未完整入库，不能复用其索引')
    corpus,revision,_state,statistics=source;values={'corpus':corpus}
    if old.execute('SELECT 1 FROM public.kb_core_books cb JOIN public.kb_editions e ON e.id=cb.edition_id WHERE cb.corpus_id=%s AND (e.deleted OR e.active_revision IS DISTINCT FROM cb.imported_revision) LIMIT 1',(corpus,)).fetchone():raise RuntimeError('核心语料有人工修订或删除，请先核验')
    files=old.execute(f'SELECT f.source_path,a.sha256,a.storage_key FROM public.kb_core_files f JOIN public.kb_assets a ON a.id=f.asset_id WHERE f.revision_id IN ({REVISION}) ORDER BY f.source_path',values).fetchall()
    paths={}
    for name,sha,key in files:
        if name in paths and paths[name]!=(sha,key):raise RuntimeError('原语料存在冲突路径')
        paths[name]=(sha,key)
    counts={table:old.execute(sql.SQL('SELECT count(*) FROM public.{} WHERE '+where).format(sql.Identifier(table)),values).fetchone()[0] for table,where in SELECTION}
    if counts['kb_core_books']!=statistics.get('source_books') or len(paths)!=statistics.get('source_files'):raise RuntimeError('原语料文件或书目数量与入库清单不一致')
    existing=new.execute('SELECT id FROM public.kb_corpora WHERE slug=%s',(slug,)).fetchone()
    if existing:
        if existing[0]!=corpus:raise RuntimeError('后台已有同名但不同来源的语料库，不覆盖')
        current=new.execute('SELECT count(*) FROM public.kb_core_books WHERE corpus_id=%s',(corpus,)).fetchone()[0]
        if current!=counts['kb_core_books']:raise RuntimeError('后台同名语料数量不一致，不覆盖')
        return {'already_present':True,'books':current,'files':len(paths)}
    if new.execute('SELECT 1 FROM public.kb_works LIMIT 1').fetchone():raise RuntimeError('此入口只迁入空的系统文献库，不覆盖既有文献')
    raw_root=contained(destination,'corpora/'+slug)
    copied_assets=set()
    for i,(name,(sha,key)) in enumerate(paths.items()):
        if not key.startswith('assets/'):raise RuntimeError('原语料原件存储路径无效')
        raw=contained(source_path,name);target_raw=contained(raw_root,name)
        copy_checked(raw,target_raw,sha)
        if key not in copied_assets:
            copy_checked(target_raw,contained(destination,key),sha);copied_assets.add(key)
        if i%250==0 or i==len(paths)-1:progress('copy_files',i+1,len(paths))
    manifest={'format':1,'slug':slug,'source_path':str(source_path),'source_revision':revision,'books':counts['kb_core_books'],
              'files':len(paths),'sha256':{name:sha for name,(sha,_key) in paths.items()}}
    (raw_root/'.wenjin-corpus.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    progress('database_copy',0,len(SELECTION))
    with new.transaction():
        new.execute('SELECT pg_advisory_xact_lock(824171,7721)')
        new.execute('SET LOCAL statement_timeout=0');new.execute("SET LOCAL lock_timeout='10s'")
        new.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier('public',table) for table,_ in SELECTION)))
        if new.execute('SELECT 1 FROM public.kb_works LIMIT 1').fetchone():raise RuntimeError('后台文献库在复制期间已有新增，迁移未发布')
        libraries=old.execute(f'SELECT DISTINCT l.id,l.name FROM public.kb_libraries l JOIN public.kb_works w ON w.library_id=l.id WHERE w.id IN (SELECT work_id FROM public.kb_editions WHERE id IN ({EDITION}))',values).fetchall()
        for identifier,name in libraries:new.execute('INSERT INTO public.kb_libraries(id,name) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING',(identifier,name))
        indexes=new.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='kb_core_passages' AND indexdef LIKE '% USING gin %'").fetchall()
        for name,_definition in indexes:new.execute(sql.SQL('DROP INDEX public.{}').format(sql.Identifier(name)))
        for i,(table,where) in enumerate(SELECTION):
            overrides={'active_revision':'NULL::uuid','active_index':'NULL::uuid'} if table=='kb_editions' else {'parent_id':'NULL::uuid'} if table=='kb_revisions' else None
            copy_rows(old,new,table,where,values,overrides)
            progress('database_copy',i+1,len(SELECTION))
        new.execute('CREATE TEMP TABLE corpus_heads(id uuid,active_revision uuid,active_index uuid) ON COMMIT DROP')
        with old.cursor().copy(sql.SQL(f'COPY (SELECT id,active_revision,active_index FROM public.kb_editions WHERE id IN ({EDITION})) TO STDOUT (FORMAT BINARY)'),values) as reader,new.cursor().copy('COPY corpus_heads FROM STDIN (FORMAT BINARY)') as writer:
            for block in reader:writer.write(block)
        new.execute('UPDATE public.kb_editions e SET active_revision=h.active_revision,active_index=h.active_index FROM corpus_heads h WHERE e.id=h.id')
        new.execute('UPDATE public.kb_corpora SET source_path=%s WHERE id=%s',(str(raw_root),corpus))
        for table,_ in SELECTION:
            actual=new.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table))).fetchone()[0]
            if actual!=counts[table]:raise RuntimeError('语料迁移数量核验失败：'+table)
        progress('build_search_index',0,len(indexes))
        new.execute("SET LOCAL maintenance_work_mem='512MB'")
        for i,(_name,definition) in enumerate(indexes):
            new.execute(definition);progress('build_search_index',i+1,len(indexes))
        # COPY into an empty catalog otherwise leaves the planner using empty
        # table estimates until autovacuum runs, making the first directory scan
        # select nested-loop plans over hundreds of thousands of source rows.
        progress('analyze',0,len(SELECTION))
        for i,(table,_where) in enumerate(SELECTION):
            new.execute(sql.SQL('ANALYZE public.{}').format(sql.Identifier(table)))
            progress('analyze',i+1,len(SELECTION))
    return {'already_present':False,'corpus_id':str(corpus),'books':counts['kb_core_books'],'files':len(paths),
            'source_revision':revision,'raw_directory':str(raw_root),'tables':counts,'originals_verified':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--source-data-dir',type=Path,required=True)
    parser.add_argument('--source-path',type=Path,required=True)
    parser.add_argument('--slug',default='daizhige')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not __import__('re').fullmatch(r'[a-z0-9-]{1,80}',args.slug):parser.error('语料标识无效')
    root=args.data_dir.resolve();identity=json.loads((root/'private/library.json').read_text(encoding='utf-8'))
    shared=json.loads((root/'private/runtimes'/identity['workspace_id']/'shared-database.json').read_text(encoding='utf-8'))
    destination=Path(shared['root']).resolve()
    if not destination.is_relative_to(root):raise RuntimeError('后台资料目录不在指定门户内')
    manager=ManagedPostgres(args.source_data_dir.resolve())
    args.report.parent.mkdir(parents=True,exist_ok=True)
    def progress(stage,done,total):
        args.report.with_suffix('.progress.json').write_text(json.dumps({'stage':stage,'done':done,'total':total}),encoding='utf-8')
        print(stage,done,total,flush=True)
    try:
        manager.start()
        with manager.connect() as old,psycopg.connect(native_url(shared['owner_url'])) as new:
            old.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            result=copy_corpus(old,new,args.source_path,destination,args.slug,progress)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:manager.stop()


if __name__=='__main__':main()
