"""Maintenance-window migration with one durable global activation decision."""
import hashlib
import json
import sqlite3
import shutil
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from . import repository as r
from .audit import read_only
from .backup import pg_command
from .catalog import CatalogService
from .ingestion import publish
from .project_sources import ProjectSourceService
from .execution import activated


def upgrade_backup(root):
    from sqlalchemy import text
    # The same exclusive lock is respected by every catalog transaction.
    # SQLite's write reservation also freezes projects and execution records.
    with r.database.engine(root).begin() as lock,closing(sqlite3.connect(root/'library.sqlite',timeout=15)) as local:
        lock.execute(text('SELECT pg_advisory_xact_lock(824171,2)'))
        local.execute('BEGIN IMMEDIATE')
        return _upgrade_backup_locked(root,lock)


def _upgrade_backup_locked(root,lock):
    """Preserve pre-upgrade SQLite even when legacy assets are missing."""
    target=root/'backups'/('before-0.5.0-'+r.uid()+'.zip');target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shixue-upgrade-') as directory:
        stage=Path(directory);paths=[]
        if (root/'library.sqlite').exists():
            with closing(read_only(root/'library.sqlite')) as old,closing(sqlite3.connect(stage/'library.sqlite')) as copy:
                old.backup(copy)
            paths.append(stage/'library.sqlite')
        exists=r.execute(lock,"SELECT to_regclass('public.kb_schema_version')").scalar()
        revision=r.execute(lock,'SELECT version_num FROM kb_schema_version').scalar() if exists else None
        pg_command(root,'pg_dump',stage/'knowledge.dump');paths.append(stage/'knowledge.dump')
        for folder in ('assets','artifacts','checkpoints'):
            if (root/folder).exists():
                for source in (root/folder).rglob('*'):
                    if not source.is_file() or source.name.endswith(('-wal','-shm','.tmp')):continue
                    if source.suffix=='.sqlite':
                        output=stage/source.relative_to(root);output.parent.mkdir(parents=True,exist_ok=True)
                        with closing(read_only(source)) as old,closing(sqlite3.connect(output)) as copy:old.backup(copy)
                        paths.append(output)
                    else:paths.append(source)
        if (root/'knowledge.json').exists():paths.append(root/'knowledge.json')
        manifest={'format':1,'schema_revision':revision,'workspace_key':hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest(),'credentials_included':False,'models_included':False,
                  'material_paths':{},'files':{},'purpose':'upgrade-0.5.0'}
        if (stage/'library.sqlite').exists():
            with closing(read_only(stage/'library.sqlite')) as db:
                for identifier,path in db.execute('SELECT id,asset_path FROM materials'):
                    candidate=Path(path);candidate=candidate if candidate.is_absolute() else root/candidate
                    if not candidate.is_file():continue
                    if root.resolve() in candidate.resolve().parents:
                        relative=candidate.resolve().relative_to(root.resolve())
                        if candidate.resolve() not in [p.resolve() for p in paths]:paths.append(candidate)
                    else:
                        # Preserve external originals inside the portable backup.
                        relative=Path('assets/legacy-backup')/str(identifier)/candidate.name
                        copied=stage/relative;copied.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(candidate,copied);paths.append(copied)
                    manifest['material_paths'][identifier]=relative.as_posix()
        temporary=target.with_suffix('.partial')
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for source in paths:
                relative=source.relative_to(stage) if stage in source.parents else source.relative_to(root)
                name=relative.as_posix()
                with source.open('rb') as stream:manifest['files'][name]=hashlib.file_digest(stream,'sha256').hexdigest()
                archive.write(source,name)
            archive.writestr('manifest.json',r.j(manifest))
        temporary.replace(target)
    return target.relative_to(root).as_posix()


class CatalogActivation:
    def __init__(self,root,local_connection):
        self.root=root;self.local=local_connection

    def status(self):
        with self.local() as db:
            old=db.execute('SELECT setting_value FROM app_settings WHERE setting_key=\'catalog-activation\'').fetchone()
            local=json.loads(old[0]) if old else {'state':'prepared'}
            count=db.execute('SELECT count(*) FROM bibliography').fetchone()[0]
        try:
            with r.database.transaction(self.root) as connection:
                state=r.execute(connection,'SELECT * FROM kb_catalog_activation WHERE id=1').mappings().first()
                items=r.rows(connection,'SELECT entry_id,edition_id,state,error FROM kb_migration_items ORDER BY entry_id')
            same=state and local.get('epoch')==str(state['epoch']) and local.get('state')=='activated' and state['state']=='activated'
            return {'state':'activated' if same else state['state'] if state else 'prepared',
                    'active':bool(same),'legacy_count':count,'items':items,'backup_id':state['backup_id'] if state else '',
                    'message':'文献库已统一' if same else '升级尚未完成，请先备份并迁移旧资料'}
        except (SQLAlchemyError,ValueError):
            return {'state':'unavailable','active':False,'legacy_count':count,'items':[],
                    'message':'数据库尚未就绪，请检查连接与结构升级'}

    def reconcile(self):
        with self.local() as db:
            if not activated(db):return
        status=self.status()
        if status['active'] or status['state']=='unavailable':return
        # A changed external connection or mismatched restored pair must not
        # authorize writes solely from the old SQLite activation marker.
        with self.local() as db:
            row=db.execute("SELECT setting_value FROM app_settings WHERE setting_key='catalog-activation'").fetchone()
            marker=json.loads(row[0]);marker['state']='prepared'
            db.execute("UPDATE app_settings SET setting_value=? WHERE setting_key='catalog-activation'",(r.j(marker),))

    def migrate(self):
        with self.local() as db:
            for table in ('tasks','collation_batches','preprocess_jobs','collation_debug_runs'):
                if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                    if db.execute("SELECT 1 FROM "+table+" WHERE status IN ('queued','running','pausing','stopping') LIMIT 1").fetchone():
                        raise HTTPException(409,'请先结束或暂停正在执行的工作，再迁移')
        state=self.status()
        if state['active']:return state
        backup=state.get('backup_id') or ''
        with r.database.transaction(self.root) as connection:
            existing=r.execute(connection,'SELECT * FROM kb_catalog_activation WHERE id=1 FOR UPDATE').mappings().first()
            epoch=str(existing['epoch']) if existing else r.uid()
            r.execute(connection,'INSERT INTO kb_catalog_activation(id,epoch,state,backup_id) VALUES(1,:epoch,\'migrating\',:backup) '
                      "ON CONFLICT(id) DO UPDATE SET state='migrating',backup_id=excluded.backup_id",epoch=epoch,backup=backup)
        with self.local() as db:
            db.execute('INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value',
                       ('catalog-activation',r.j({'epoch':epoch,'state':'migrating'})))
        with closing(read_only(self.root/'library.sqlite')) as old:
            old.row_factory=sqlite3.Row
            books=[dict(row) for row in old.execute('SELECT * FROM bibliography ORDER BY created_at,id')]
            for book in books:
                try:
                    self._entry(old,book)
                except Exception as error:
                    message=error.detail if isinstance(error,HTTPException) else '迁移失败，请检查原件或数据库后重试'
                    with r.database.transaction(self.root) as connection:
                        r.execute(connection,'INSERT INTO kb_migration_items(entry_id,state,error,archive) VALUES(:id,\'conflict\',:error,CAST(:archive AS jsonb)) '
                                  "ON CONFLICT(entry_id) DO UPDATE SET state='conflict',error=excluded.error",id=book['id'],error=str(message),archive=r.j(book))
            # Candidates may point forward to a record processed later.
            with r.database.transaction(self.root) as connection:
                for row in old.execute('SELECT id,match_id FROM references_found WHERE match_id IS NOT NULL'):
                    r.execute(connection,'UPDATE kb_reference_candidates SET matched_edition_id=(SELECT edition_id FROM kb_legacy_entries WHERE entry_id=:old) '
                              'WHERE id=:id AND matched_edition_id IS NULL',old=row['match_id'],id=row['id'])
                previous=r.rows(connection,'SELECT * FROM kb_project_editions')
            projects=ProjectSourceService(self.root,self.local)
            for link in previous:
                if old.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0',(link['project_id'],)).fetchone():
                    projects.put(link['project_id'],link['edition_id'],{'roles':link['roles']},historical=True)
            projects.sync_events()
        with r.database.transaction(self.root) as connection:
            blocking=r.execute(connection,"SELECT count(*) FROM kb_migration_items WHERE state IN ('failed','conflict','migrated')").scalar()
            verified=r.execute(connection,"SELECT count(*) FROM kb_migration_items WHERE state IN ('verified','missing_original','excluded')").scalar()
            if blocking or verified!=len(books):return self.status()
            r.execute(connection,"UPDATE kb_catalog_activation SET state='verified',updated_at=now() WHERE id=1")
        # PostgreSQL is published first; SQLite is the final local cutover marker.
        # A crash between these commits leaves the whole application in maintenance.
        with r.database.transaction(self.root) as connection:
            r.execute(connection,"UPDATE kb_catalog_activation SET state='activated',updated_at=now() WHERE id=1")
        with self.local() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE materials SET kind=\'legacy_handle\' WHERE kind=\'catalog\'')
            db.execute('UPDATE app_settings SET setting_value=? WHERE setting_key=\'catalog-activation\'',(r.j({'epoch':epoch,'state':'activated'}),))
            freeze_legacy_tables(db)
        return self.status()

    def _entry(self,old,book):
        missing=False
        with r.database.transaction(self.root) as connection:
            existing=r.execute(connection,'SELECT edition_id FROM kb_legacy_entries WHERE entry_id=:id',id=book['id']).scalar()
            edition=str(existing) if existing else str(r.create_edition(connection,{
                'title':book['title'],'item_type':book.get('item_type','other'),
                'authors':[book['authors']] if book['authors'] else [],'date_label':book['year'],
                'label':book.get('version_label',''),'source_note':book['raw_reference'],
                'tags':[book['genre']] if book.get('genre') else []})['id'])
            if not existing:
                r.execute(connection,'INSERT INTO kb_legacy_entries(entry_id,edition_id) VALUES(:old,:new)',old=book['id'],new=edition)
                r.execute(connection,'UPDATE kb_works SET metadata_json=CAST(:metadata AS jsonb) WHERE id=(SELECT work_id FROM kb_editions WHERE id=:id)',
                          id=edition,metadata=r.j({'authors_raw':book['authors'],'year_raw':book['year'],'source_material_id':book.get('source_material_id'),'needs_review':True,
                          'legacy_input_snapshot':book['title'].endswith(' · 固定输入快照')}))
                if book['title'].endswith(' · 固定输入快照'):
                    r.execute(connection,"UPDATE kb_editions SET tags=tags||'[\"旧输入快照\",\"待分类\"]'::jsonb WHERE id=:id",id=edition)
            pages=[];links={}
            for ordinal,row in enumerate(old.execute('SELECT m.*,lm.file_format,lm.position_kind FROM materials m JOIN library_materials lm ON lm.material_id=m.id WHERE lm.entry_id=? ORDER BY m.created_at,m.id',(book['id'],))):
                item=dict(row);path=Path(item['asset_path']);path=path if path.is_absolute() else self.root/path
                linked=r.execute(connection,'SELECT * FROM kb_legacy_links WHERE material_id=:id',id=item['id']).mappings().first()
                if linked:
                    asset=str(linked['asset_id'])
                    availability=r.execute(connection,'SELECT availability FROM kb_assets WHERE id=:id',id=asset).scalar()
                    missing=missing or availability!='present'
                else:
                    if path.is_file():
                        data=path.read_bytes();h=hashlib.sha256(data).hexdigest()
                        if h!=item['sha256']:raise HTTPException(409,'原件摘要与旧记录不一致，未激活')
                        _,key=r.store_file(self.root,item['filename'],data);size=len(data);availability='present'
                    else:
                        missing=True;h=item['sha256']
                        if len(h)!=64 or any(c not in '0123456789abcdef' for c in h.lower()): h=hashlib.sha256(('missing:'+item['id']).encode()).hexdigest()
                        key='assets/missing/'+h;size=0;availability='missing'
                    asset=str(r.execute(connection,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes,availability) VALUES(:id,:h,:k,:s,:v) '
                                        'ON CONFLICT(sha256) DO UPDATE SET sha256=excluded.sha256 RETURNING id',id=r.uid(),h=h,k=key,s=size,v=availability).scalar_one())
                    r.execute(connection,'INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind,ordinal) VALUES(:e,:a,:f,:p,:o) ON CONFLICT DO NOTHING',
                              e=edition,a=asset,f=item['filename'],p=item['position_kind'],o=ordinal)
                    r.execute(connection,'INSERT INTO kb_legacy_links(material_id,entry_id,edition_id,asset_id,source_digest) VALUES(:m,:entry,:e,:a,:h)',
                              m=item['id'],entry=book['id'],e=edition,a=asset,h=item['sha256'])
                links[item['id']]=asset
                r.execute(connection,'INSERT INTO kb_pages(asset_id,page_index) SELECT :asset,generate_series(1,:maximum) ON CONFLICT DO NOTHING',asset=asset,maximum=int(item['page_count']))
                for page in old.execute('SELECT page_number,text FROM pages WHERE material_id=? ORDER BY page_number',(item['id'],)):
                    pages.append({'asset_id':asset,'page_index':page['page_number'],'body':page['text'],'quality':{'source':'legacy','review_required':True}})
                    r.execute(connection,'INSERT INTO kb_pages(asset_id,page_index) VALUES(:a,:p) ON CONFLICT DO NOTHING',a=asset,p=page['page_number'])
            current=r.edition(connection,edition)
            if pages and not current['active_revision']:
                published=publish(connection,edition,pages,'legacy')
                r.queue(connection,'index',{'edition_id':edition,'revision_id':published['revision_id']})
            r.execute(connection,'INSERT INTO kb_migration_items(entry_id,edition_id,state,archive,error) VALUES(:id,:e,:state,CAST(:archive AS jsonb),\'\') '
                      'ON CONFLICT(entry_id) DO UPDATE SET edition_id=excluded.edition_id,state=excluded.state,error=\'\'',
                      id=book['id'],e=edition,state='missing_original' if missing else 'verified',archive=r.j(book))
        projects=ProjectSourceService(self.root,self.local)
        for link in old.execute('SELECT * FROM project_bibliography WHERE entry_id=?',(book['id'],)):
            if old.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0',(link['project_id'],)).fetchone():
                projects.put(link['project_id'],edition,{'roles':json.loads(link['roles_json'])},historical=True)
        for material,asset in links.items():
            maximum=old.execute('SELECT max(page_number) FROM pages WHERE material_id=?',(material,)).fetchone()[0]
            if maximum:
                for link in old.execute('SELECT * FROM project_materials WHERE material_id=?',(material,)):
                    if not old.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0',(link['project_id'],)).fetchone():continue
                    projects.put(link['project_id'],edition,{'mode':'ranges','ranges':[{'asset_id':asset,'page_from':1,'page_to':maximum}],
                                 'roles':json.loads(link['roles_json']),'witness_label':link['witness_label'],'scope_note':link['scope_note']},historical=True)
        with r.database.transaction(self.root) as connection:
            for material,asset in links.items():
                for row in old.execute('SELECT * FROM references_found WHERE material_id=?',(material,)):
                    match=r.execute(connection,'SELECT edition_id FROM kb_legacy_entries WHERE entry_id=:id',id=row['match_id']).scalar() if row['match_id'] else None
                    r.execute(connection,'INSERT INTO kb_reference_candidates(id,source_edition_id,source_asset_id,page_index,ordinal,raw_text,title,authors_raw,year_label,matched_edition_id,match_status) '
                              'VALUES(:id,:e,:a,:p,:o,:raw,:title,:authors,:year,:match,:status) ON CONFLICT(id) DO NOTHING',
                              id=row['id'],e=edition,a=asset,p=row['page_number'],o=row['ordinal'],raw=row['raw_text'],title=row['title'],
                              authors=row['authors'],year=row['year'],match=match,status=row['match_status'])


def freeze_legacy_tables(db):
    for table in ('bibliography','library_materials','project_bibliography','project_materials','references_found'):
        for action in ('INSERT','UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS frozen_'+table+'_'+action.lower()+' BEFORE '+action+' ON '+table+
                       " BEGIN SELECT RAISE(ABORT,'legacy catalog is archived; use the unified catalog'); END")
