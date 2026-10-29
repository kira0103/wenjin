from pathlib import Path
from uuid import UUID

from fastapi import HTTPException,Request,Query
from fastapi.responses import FileResponse

from . import repository as r
from .schemas import EditionCreate,Note,Topic,Search,Anchor,PageLabel,Revision,ProjectLink,Migration,NoteRelation,Reindex
from .worker import worker
from .ingestion import publish
from .retrieval import search,occurrence_count,answer


def register_features(router,root,legacy_connection,completion,model_settings):
    def validate_project(identifier):
        if identifier:
            with legacy_connection() as connection:
                if not connection.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0',(identifier,)).fetchone():
                    raise HTTPException(404,'找不到项目')

    def resolve_scope(payload):
        validate_project(payload.scope.project_id)
        validate_project(getattr(payload,'context_project_id',None))
        data=payload.model_dump(mode='json')
        if payload.scope.project_id:
            with legacy_connection() as legacy:
                from .execution import activated
                unified=activated(legacy)
                material_ids=[row[0] for row in legacy.execute('SELECT material_id FROM project_materials WHERE project_id=?',(payload.scope.project_id,))]
            if unified:
                from .project_sources import ProjectSourceService
                data['scope'].update(ProjectSourceService(root(),legacy_connection).resolved_scope(payload.scope.project_id))
                data['scope']['project_id']=None
                return data
            with r.database.transaction(root()) as connection:
                migrated=r.rows(connection,'SELECT DISTINCT edition_id FROM kb_legacy_links WHERE material_id=ANY(CAST(:ids AS text[]))',ids=material_ids)
                linked=r.rows(connection,'SELECT edition_id FROM kb_project_editions WHERE project_id=:id AND edition_id NOT IN (SELECT edition_id FROM kb_legacy_links)',id=payload.scope.project_id)
            allowed={str(item['edition_id']) for item in migrated+linked}
            if payload.scope.edition_ids:allowed&={str(value) for value in payload.scope.edition_ids}
            data['scope']['project_id']=None;data['scope']['edition_ids']=sorted(allowed)
            data['scope']['_empty']=not bool(allowed)
        return data

    @router.get('/editions')
    def editions(query:str='',project_id:str|None=None,before:str|None=None,limit:int=Query(100,ge=1,le=200)):
        validate_project(project_id)
        with r.database.transaction(root()) as connection:
            items=r.list_editions(connection,query,project_id,before,limit)
            return {'items':items,'next_cursor':str(items[-1]['id']) if len(items)==limit else None}

    @router.post('/editions')
    def create(payload:EditionCreate):
        if not payload.title.strip():raise HTTPException(400,'题名不能为空')
        from .catalog import CatalogService
        return CatalogService(root()).create(payload.model_dump(mode='json'))

    @router.patch('/editions/{identifier}')
    def edit(identifier:UUID,payload:EditionCreate):
        with legacy_connection() as local:
            from .execution import activated
            if activated(local):raise HTTPException(409,'请分别编辑作品信息和版本信息，并提供修改版本号')
        with r.database.transaction(root()) as connection:
            current=r.edition(connection,identifier)
            data=payload.model_dump(mode='json')
            r.execute(connection,'UPDATE kb_works SET title=:title,item_type=:kind,authors_json=CAST(:a AS jsonb),date_label=:date WHERE id=:id',
                      title=data['title'],kind=data['item_type'],a=r.j(data['authors']),date=data['date_label'],id=current['work_id'])
            r.execute(connection,'UPDATE kb_editions SET label=:label,publisher=:p,publication_year=:year,doi=:doi,source_note=:note,tags=CAST(:tags AS jsonb) WHERE id=:id',
                      label=data['label'],p=data['publisher'],year=data['publication_year'],doi=data['doi'],note=data['source_note'],tags=r.j(data['tags']),id=identifier)
            return r.edition(connection,identifier)

    @router.get('/editions/{identifier}')
    def detail(identifier:UUID):
        with r.database.transaction(root()) as connection:
            item=r.edition(connection,identifier)
            if r.execute(connection,'SELECT edition_id FROM kb_core_books WHERE edition_id=:id',id=identifier).first():
                return {**item,'core':True,'files':[],'blocks':[],'revisions':[]}
            item['files']=r.rows(connection,'SELECT a.id,a.mime_type,a.size_bytes,a.availability,ea.filename,ea.position_kind,ea.ordinal,(SELECT COALESCE(max(page_index),0) FROM kb_pages p WHERE p.asset_id=a.id) page_count FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=:id ORDER BY ea.ordinal',id=identifier)
            item['blocks']=r.rows(connection,'SELECT b.*,l.printed_label,l.volume_label,l.leaf_label,l.side FROM kb_blocks b '
                         'LEFT JOIN kb_page_labels l ON l.edition_id=:id AND l.asset_id=b.asset_id AND l.page_index=b.page_index '
                         'WHERE b.revision_id=:revision ORDER BY b.ordinal',id=identifier,revision=item['active_revision'])
            item['revisions']=r.rows(connection,'SELECT id,source_kind,created_at FROM kb_revisions WHERE edition_id=:id ORDER BY created_at DESC',id=identifier)
            return item

    @router.get('/editions/{identifier}/revisions/{revision}')
    def revision_detail(identifier:UUID,revision:UUID):
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            r.one(connection,'SELECT id FROM kb_revisions WHERE id=:r AND edition_id=:e',r=revision,e=identifier)
            return r.rows(connection,'SELECT * FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=revision)

    @router.get('/editions/{identifier}/files/{asset}')
    def file(identifier:UUID,asset:UUID):
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            item=r.one(connection,'SELECT a.*,ea.filename FROM kb_assets a JOIN kb_edition_assets ea ON ea.asset_id=a.id WHERE ea.edition_id=:e AND a.id=:a',e=identifier,a=asset)
        return FileResponse(r.asset_path(root(),item['storage_key']),media_type='application/pdf' if Path(item['filename']).suffix.lower()=='.pdf' else 'application/octet-stream',filename=item['filename'])

    @router.get('/editions/{identifier}/files/{asset}/pages/{page}/image')
    def page_image(identifier:UUID,asset:UUID,page:int):
        import io
        from PIL import Image
        from fastapi.responses import Response
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            item=r.one(connection,'SELECT a.* FROM kb_assets a JOIN kb_edition_assets ea ON ea.asset_id=a.id WHERE ea.edition_id=:e AND a.id=:a',e=identifier,a=asset)
        with Image.open(r.asset_path(root(),item['storage_key'])) as image:
            if not 1<=page<=getattr(image,'n_frames',1):raise HTTPException(404,'找不到扫描页')
            image.seek(page-1);frame=image.convert('RGB');frame.thumbnail((4000,4000));buffer=io.BytesIO();frame.save(buffer,format='PNG')
        return Response(buffer.getvalue(),media_type='application/octet-stream')

    @router.post('/editions/{identifier}/bridge')
    def bridge(identifier:UUID,payload:ProjectLink):
        validate_project(payload.project_id)
        with legacy_connection() as local:
            from .execution import activated,WorkingInputService
            unified=activated(local)
        if unified:
            return WorkingInputService(root(),legacy_connection).catalog(payload.project_id,identifier)
        from .legacy import bridge_snapshot
        return bridge_snapshot(root(),identifier,payload,legacy_connection)

    @router.post('/editions/{identifier}/imports')
    async def import_file(identifier:UUID,request:Request,filename:str=Query(min_length=1,max_length=220)):
        filename=Path(filename.replace('\\','/')).name
        with r.database.transaction(root()) as connection:r.edition(connection,identifier)
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>250*1024*1024:raise HTTPException(413,'文件超过 250 MB')
        from .catalog import CatalogService
        from starlette.concurrency import run_in_threadpool
        result=await run_in_threadpool(CatalogService(root()).import_file,identifier,filename,bytes(data))
        if not result['duplicate']:worker.start(root())
        return result

    @router.put('/editions/{identifier}/pages/{asset}/{page}/label')
    def label(identifier:UUID,asset:UUID,page:int,payload:PageLabel):
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            r.execute(connection,'INSERT INTO kb_page_labels(edition_id,asset_id,page_index,printed_label,volume_label,leaf_label,side) '
                      'VALUES(:e,:a,:p,:printed,:volume,:leaf,:side) ON CONFLICT(edition_id,asset_id,page_index) '
                      'DO UPDATE SET printed_label=excluded.printed_label,volume_label=excluded.volume_label,leaf_label=excluded.leaf_label,side=excluded.side',
                      e=identifier,a=asset,p=page,printed=payload.printed_label,volume=payload.volume_label,leaf=payload.leaf_label,side=payload.side)
        return {'saved':True}

    @router.post('/editions/{identifier}/revisions')
    def revise(identifier:UUID,payload:Revision):
        with r.database.transaction(root()) as connection:
            r.one(connection,'SELECT id FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=identifier)
            if r.execute(connection,'SELECT edition_id FROM kb_core_books WHERE edition_id=:id',id=identifier).first():
                raise HTTPException(409,'核心古籍原始版本保持只读，请在项目中制作校对稿，或另建文献版本')
            for page in payload.pages:
                r.one(connection,'SELECT asset_id FROM kb_edition_assets WHERE edition_id=:e AND asset_id=:a',e=identifier,a=page.asset_id)
                r.one(connection,'SELECT page_index FROM kb_pages WHERE asset_id=:a AND page_index=:p',a=page.asset_id,p=page.page_index)
            pages=[]
            for page in payload.pages:
                quality={'source':'manual','review_required':True}
                if page.source_block_id:
                    previous=r.one(connection,'SELECT body,quality FROM kb_blocks WHERE id=:id AND revision_id=:r AND asset_id=:a AND page_index=:p',
                                   id=page.source_block_id,r=payload.expected_revision,a=page.asset_id,p=page.page_index)
                    if previous['body']==page.body:quality=dict(previous['quality'])
                if page.reviewed is not None:quality['review_required']=not page.reviewed
                pages.append({**page.model_dump(mode='json'),'quality':quality})
            result=publish(connection,identifier,pages,'manual',payload.expected_revision)
            r.queue(connection,'index',{'edition_id':str(identifier),'revision_id':result['revision_id']})
        worker.start(root());return result

    @router.get('/editions/{identifier}/delete-preview')
    def delete_preview(identifier:UUID):
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            return {'anchors':r.execute(connection,'SELECT count(*) FROM kb_anchors WHERE edition_id=:id',id=identifier).scalar_one(),
                    'projects':r.rows(connection,"SELECT DISTINCT project_id FROM kb_project_sources WHERE edition_id=:id AND state IN ('active','archived') UNION SELECT project_id FROM kb_project_editions WHERE edition_id=:id",id=identifier)}

    @router.delete('/editions/{identifier}')
    def delete(identifier:UUID,confirmation:str=Query()):
        with r.database.transaction(root()) as connection:
            current=r.edition(connection,identifier)
            if confirmation!=current['title']:raise HTTPException(400,'请填写准确题名确认删除')
            r.execute(connection,'UPDATE kb_editions SET deleted=true WHERE id=:id',id=identifier)
            r.execute(connection,"UPDATE kb_jobs SET state='cancelled' WHERE payload->>'edition_id'=:id AND state IN ('queued','running')",id=str(identifier))
        return {'deleted':True,'sources_retained':True}

    @router.put('/editions/{identifier}/project')
    def project_link(identifier:UUID,payload:ProjectLink):
        validate_project(payload.project_id)
        with legacy_connection() as local:
            from .execution import activated
            unified=activated(local)
        if unified:
            from .project_sources import ProjectSourceService
            return ProjectSourceService(root(),legacy_connection).put(payload.project_id,identifier,{'roles':payload.roles})
        with r.database.transaction(root()) as connection:
            r.edition(connection,identifier)
            r.execute(connection,'INSERT INTO kb_project_editions(project_id,edition_id,roles) VALUES(:p,:e,CAST(:r AS jsonb)) '
                      'ON CONFLICT(project_id,edition_id) DO UPDATE SET roles=excluded.roles',p=payload.project_id,e=identifier,r=r.j(payload.roles))
            links=r.rows(connection,'SELECT material_id FROM kb_legacy_links WHERE edition_id=:id',id=identifier)
        with legacy_connection() as local:
            for link in links:local.execute('INSERT OR REPLACE INTO project_materials(project_id,material_id,roles_json) VALUES(?,?,?)',(payload.project_id,link['material_id'],r.j(payload.roles)))
        return {'linked':True}

    @router.delete('/editions/{identifier}/project/{project}')
    def unlink(identifier:UUID,project:str):
        validate_project(project)
        with legacy_connection() as local:
            from .execution import activated
            unified=activated(local)
        if unified:
            from .project_sources import ProjectSourceService
            service=ProjectSourceService(root(),legacy_connection)
            for link in service.list(project):
                if str(link['edition_id'])==str(identifier):service.remove(project,link['id'])
            return {'unlinked':True}
        with r.database.transaction(root()) as connection:
            r.execute(connection,'DELETE FROM kb_project_editions WHERE project_id=:p AND edition_id=:e',p=project,e=identifier)
            links=r.rows(connection,'SELECT material_id,entry_id FROM kb_legacy_links WHERE edition_id=:id',id=identifier)
        with legacy_connection() as local:
            for link in links:
                local.execute('DELETE FROM project_materials WHERE project_id=? AND material_id=?',(project,link['material_id']))
                local.execute('DELETE FROM project_bibliography WHERE project_id=? AND entry_id=?',(project,link['entry_id']))
        return {'unlinked':True}

    @router.get('/jobs')
    def jobs():
        with r.database.transaction(root()) as connection:return r.rows(connection,"SELECT * FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC LIMIT 100")

    @router.get('/jobs/{identifier}')
    def job(identifier:UUID):
        with r.database.transaction(root()) as connection:return r.one(connection,"SELECT * FROM kb_jobs WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)

    @router.post('/jobs/{identifier}/retry')
    def retry(identifier:UUID):
        with r.database.transaction(root()) as connection:
            current=r.one(connection,"SELECT kind FROM kb_jobs WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)
            from .shared import read_only_library
            if read_only_library() and current['kind'] in {'import','index','document_batch','document_import','core_import','migration','export'}:
                raise HTTPException(403,'公共文献处理由管理员维护，旧任务记录继续保留')
            if current['kind'] in ('research','collaboration'):raise HTTPException(409,'请在研究助手中使用“重新研究”，以核验最新范围和模型配置')
            result=r.one(connection,"UPDATE kb_jobs SET state='queued',owner=NULL,error='',progress=0,attempts=0 WHERE id=:id AND workspace_key=current_setting('shixue.workspace') AND state IN ('failed','cancelled') RETURNING *",id=identifier)
        worker.start(root());return result

    @router.post('/jobs/{identifier}/cancel')
    def cancel(identifier:UUID):
        with r.database.transaction(root()) as connection:
            return r.one(connection,"UPDATE kb_jobs SET state='cancelled',updated_at=now() WHERE id=:id AND workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running') RETURNING *",id=identifier)

    @router.post('/editions/{identifier}/reindex')
    def reindex(identifier:UUID,payload:Reindex|None=None):
        with r.database.transaction(root()) as connection:
            current=r.edition(connection,identifier)
            if not current['active_revision']:raise HTTPException(409,'请先导入原文')
            if payload and payload.expected_revision and str(current['active_revision'])!=str(payload.expected_revision):
                raise HTTPException(409,'原文已更新，请重新预览后建立索引')
            from .chunking import stored_config
            previous=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
            config=payload.config.model_dump() if payload and payload.config else stored_config(previous)
            result=r.queue(connection,'index',{'edition_id':str(identifier),'revision_id':str(current['active_revision']),'config':config})
        worker.start(root());return result

    @router.post('/anchors')
    def create_anchor(payload:Anchor):
        with r.database.transaction(root()) as connection:return r.anchor(connection,payload.block_id,payload.start,payload.end)

    @router.get('/anchors/{identifier}')
    def get_anchor(identifier:UUID):
        with r.database.transaction(root()) as connection:
            return r.one(connection,'SELECT a.*,e.deleted FROM kb_anchors a JOIN kb_editions e ON e.id=a.edition_id WHERE a.id=:id',id=identifier)

    @router.get('/notes')
    def notes(query:str='',before:str|None=None):
        with r.database.transaction(root()) as connection:
            return r.rows(connection,"SELECT * FROM kb_notes WHERE NOT deleted AND (strpos(title,:q)>0 OR strpos(body,:q)>0) "
                          "AND (:before='' OR id::text<:before) ORDER BY id::text DESC LIMIT 100",q=query,before=before or '')

    @router.get('/notes/{identifier}')
    def note_detail(identifier:UUID):
        with r.database.transaction(root()) as connection:
            item=r.one(connection,'SELECT * FROM kb_notes WHERE id=:id AND NOT deleted',id=identifier)
            item['sources']=r.rows(connection,'SELECT s.*,a.quote,a.locator FROM kb_note_sources s JOIN kb_anchors a ON a.id=s.anchor_id WHERE s.note_id=:id',id=identifier)
            item['revisions']=r.rows(connection,'SELECT * FROM kb_note_revisions WHERE note_id=:id ORDER BY version DESC',id=identifier)
            item['relations']=r.rows(connection,'SELECT r.*,n.title FROM kb_note_relations r JOIN kb_notes n ON n.id=r.target_id WHERE r.source_id=:id',id=identifier)
            return item

    @router.post('/notes')
    def new_note(payload:Note):
        with r.database.transaction(root()) as connection:return r.save_note(connection,None,payload.model_dump(mode='json'))

    @router.patch('/notes/{identifier}')
    def edit_note(identifier:UUID,payload:Note):
        with r.database.transaction(root()) as connection:return r.save_note(connection,identifier,payload.model_dump(mode='json'))

    @router.post('/notes/{identifier}/relations')
    def relate(identifier:UUID,payload:NoteRelation):
        if identifier==payload.target_id:raise HTTPException(400,'不能关联笔记自身')
        with r.database.transaction(root()) as connection:
            for value in (identifier,payload.target_id):r.one(connection,'SELECT id FROM kb_notes WHERE id=:id AND NOT deleted',id=value)
            r.execute(connection,'INSERT INTO kb_note_relations(source_id,target_id,relation) VALUES(:s,:t,:r) ON CONFLICT DO NOTHING',s=identifier,t=payload.target_id,r=payload.relation)
        return {'linked':True}

    @router.delete('/notes/{identifier}')
    def remove_note(identifier:UUID):
        with r.database.transaction(root()) as connection:r.execute(connection,'UPDATE kb_notes SET deleted=true WHERE id=:id',id=identifier)
        return {'deleted':True}

    @router.get('/topics')
    def topics():
        with r.database.transaction(root()) as connection:return r.rows(connection,'SELECT * FROM kb_topics ORDER BY created_at DESC LIMIT 100')

    @router.get('/topics/{identifier}')
    def topic_detail(identifier:UUID):
        with r.database.transaction(root()) as connection:
            item=r.one(connection,'SELECT * FROM kb_topics WHERE id=:id',id=identifier)
            item['notes']=r.rows(connection,'SELECT n.* FROM kb_notes n JOIN kb_topic_notes t ON t.note_id=n.id WHERE t.topic_id=:id AND NOT n.deleted',id=identifier)
            item['editions']=r.rows(connection,'SELECT e.*,w.title FROM kb_editions e JOIN kb_works w ON w.id=e.work_id JOIN kb_topic_editions t ON t.edition_id=e.id WHERE t.topic_id=:id AND NOT e.deleted',id=identifier)
            return item

    def topic_save(identifier,payload):
        with r.database.transaction(root()) as connection:
            if identifier:
                r.one(connection,'SELECT id FROM kb_topics WHERE id=:id',id=identifier)
                r.execute(connection,'UPDATE kb_topics SET title=:t,question=:q,description=:d WHERE id=:id',id=identifier,t=payload.title,q=payload.question,d=payload.description)
                for table in ('kb_topic_notes','kb_topic_editions'):r.execute(connection,'DELETE FROM '+table+' WHERE topic_id=:id',id=identifier)
            else:
                identifier=r.uid();r.execute(connection,'INSERT INTO kb_topics(id,title,question,description) VALUES(:id,:t,:q,:d)',id=identifier,t=payload.title,q=payload.question,d=payload.description)
            for value in set(payload.note_ids):
                r.one(connection,'SELECT id FROM kb_notes WHERE id=:id AND NOT deleted',id=value)
                r.execute(connection,'INSERT INTO kb_topic_notes(topic_id,note_id) VALUES(:t,:n)',t=identifier,n=value)
            for value in set(payload.edition_ids):
                r.edition(connection,value)
                r.execute(connection,'INSERT INTO kb_topic_editions(topic_id,edition_id) VALUES(:t,:e)',t=identifier,e=value)
        return topic_detail(UUID(str(identifier)))

    @router.post('/topics')
    def create_topic(payload:Topic):return topic_save(None,payload)

    @router.patch('/topics/{identifier}')
    def update_topic(identifier:UUID,payload:Topic):return topic_save(identifier,payload)

    @router.post('/search')
    def retrieve(payload:Search):
        return search(root(),resolve_scope(payload))

    @router.post('/occurrences')
    def occurrences(payload:Search):
        return occurrence_count(root(),payload.query,resolve_scope(payload)['scope'])

    @router.post('/ask')
    def ask(payload:Search):
        return answer(root(),resolve_scope(payload),completion,model_settings())

    @router.post('/ask-stream')
    def ask_stream(payload:Search):
        from fastapi.responses import StreamingResponse
        from .streaming import events
        return StreamingResponse(events(root(),resolve_scope(payload),model_settings()),media_type='text/event-stream')

    @router.get('/answers')
    def answers():
        with r.database.transaction(root()) as connection:return r.rows(connection,'SELECT * FROM kb_qa_runs ORDER BY created_at DESC LIMIT 100')

    from .quality_api import register as register_quality
    register_quality(router,root,resolve_scope)

    from .research_api import register as register_research
    register_research(router,root,resolve_scope,model_settings,completion)
    from .assistant_api import register as register_assistants
    register_assistants(router,root,resolve_scope,model_settings)
    from .collaboration import register as register_collaboration
    register_collaboration(router,root,resolve_scope,model_settings)

    @router.post('/migrations/preview')
    def migration_preview(payload:Migration):
        from .legacy import preview
        return preview(root(),payload.material_ids)

    @router.post('/migrations')
    def migration_start(payload:Migration):
        with legacy_connection() as local:
            from .execution import activated
            if activated(local):raise HTTPException(409,'文献库已统一，旧的逐附件迁移接口已停用')
        from .legacy import preview
        plan=preview(root(),payload.material_ids)
        if plan['blocking']:raise HTTPException(409,'部分资料缺失或正在使用，请先解决预览中的问题')
        with r.database.transaction(root()) as connection:result=r.queue(connection,'migration',payload.model_dump(mode='json'))
        worker.start(root());return result

    @router.post('/exports')
    def export():
        with r.database.transaction(root()) as connection:result=r.queue(connection,'export',{})
        worker.start(root());return result

    @router.get('/exports/{identifier}/file')
    def export_download(identifier:UUID):
        with r.database.transaction(root()) as connection:item=r.one(connection,"SELECT result FROM kb_jobs WHERE id=:id AND kind='export' AND state='succeeded'",id=identifier)
        return FileResponse(r.asset_path(root(),item['result']['storage_key']),filename='文献库备份.zip',media_type='application/octet-stream')
