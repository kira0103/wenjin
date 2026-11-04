"""Administrator ingestion previews and fenced, versioned document processing jobs."""
from typing import Literal
from uuid import UUID
from fastapi import HTTPException,Query,Depends
from pydantic import Field,model_validator
from . import repository as r,retrieval_settings
from .schemas import Strict,IngestionOptions
from .shared import private_schema
from .processing_inventory import InventoryFilters


class ProcessingPreview(Strict):
    text:str=Field(min_length=1,max_length=100000)
    processing:IngestionOptions


class TextImport(Strict):
    title:str=Field(min_length=1,max_length=200)
    text:str=Field(min_length=1,max_length=200000)
    item_type:Literal['ancient','article','thesis']='article'
    processing:IngestionOptions|None=None


class SelectedDocument(Strict):
    id:UUID
    revision_id:UUID|None


class ProcessDocuments(Strict):
    operation:Literal['process','index','reparse']='index'
    documents:list[SelectedDocument]=Field(min_length=1,max_length=100)
    processing:IngestionOptions|None=None
    use_document_policies:bool=True
    expected_policy_version:int|None=Field(default=None,ge=0)

    @model_validator(mode='after')
    def unique(self):
        if len({d.id for d in self.documents})!=len(self.documents):raise ValueError('处理文献不能重复')
        return self


class DocumentPolicy(Strict):
    version:int=Field(ge=0)
    processing:IngestionOptions|None=None


class DocumentPreview(Strict):
    expected_revision:UUID|None=None
    processing:IngestionOptions|None=None


class BulkProcess(Strict):
    filters:InventoryFilters=Field(default_factory=InventoryFilters)
    scope:Literal['filtered','library']='filtered'
    exclude_ids:list[UUID]=Field(default_factory=list,max_length=50000)
    query:str=Field(default='',max_length=200)
    state:Literal['all','pending','ready','mismatch','processing','failed']='all'
    skip_ready:bool=True
    processing:IngestionOptions|None=None
    use_document_policies:bool=True
    expected_policy_version:int|None=Field(default=None,ge=0)


class CleanupRequest(Strict):
    preview_id:UUID
    confirm:Literal[True]

def register(router,root):
    from .failure_api import register as register_failures
    register_failures(router,root)
    def administrator():
        if private_schema():raise HTTPException(403,'入库和加工策略由管理员管理')

    @router.on_event('startup')
    def resume_dispatch():
        if not private_schema():
            from .bulk_processing import dispatcher
            if r.database.health(root())['ready']:
                dispatcher.start(root())
                from .index_cleanup import runner
                runner.start(root())

    @router.on_event('shutdown')
    def stop_dispatch():
        from .bulk_processing import dispatcher
        dispatcher.stop()
        from .index_cleanup import runner
        runner.stop()

    @router.post('/rag-process-all')
    def process_all(payload:BulkProcess):
        administrator()
        from .bulk_processing import submit
        return submit(root(),payload)

    @router.post('/rag-process-all/{identifier}/{action}')
    def control_all(identifier:UUID,action:Literal['cancel','retry']):
        administrator()
        from .bulk_processing import control
        return control(root(),identifier,action)

    @router.post('/rag-cancel-all')
    def cancel_all_ingestion():
        administrator()
        from .bulk_processing import cancel_all
        return cancel_all(root())

    @router.get('/rag-index-history')
    def index_history(scope:Literal['other_models','all_inactive']='other_models'):
        administrator()
        from .index_cleanup import preview
        return preview(root(),scope)

    @router.get('/rag-index-cleanup')
    def latest_cleanup():
        administrator()
        with r.database.transaction(root()) as db:
            items=r.rows(db,"SELECT id,state,stage,progress,result,error,updated_at FROM kb_jobs WHERE kind='index_cleanup' AND workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC,id DESC LIMIT 1")
        return items[0] if items else None

    @router.post('/rag-index-cleanup')
    def cleanup_indexes(payload:CleanupRequest):
        administrator()
        from .index_cleanup import submit
        return submit(root(),payload.preview_id)

    @router.get('/rag-index-cleanup/{identifier}')
    def cleanup_status(identifier:UUID):
        administrator()
        from .index_cleanup import status
        return status(root(),identifier)

    @router.post('/rag-preview')
    def preview_processing(payload:ProcessingPreview):
        administrator()
        from .chunking import preview
        result=preview(payload.text,payload.processing.chunking.model_dump(),root())
        count=len(result['chunks'])
        return {**result,'chunks':result['chunks'][:100],'total':count,'truncated':count>100}

    @router.post('/rag-text')
    def import_text(payload:TextImport):
        administrator()
        if not payload.title.strip() or not payload.text.strip():raise HTTPException(400,'请填写题名和原文')
        from .catalog import CatalogService
        from .worker import worker
        catalog=CatalogService(root())
        edition=catalog.create({'title':payload.title.strip(),'item_type':payload.item_type,'source_note':'管理员直接录入原文'})
        job=catalog.import_file(edition['id'],'录入原文.txt',payload.text.encode('utf-8'),payload.processing.model_dump() if payload.processing else None)
        worker.start(root())
        return {'edition_id':str(edition['id']),'job_id':str(job['id']),'state':job['state']}

    @router.get('/rag-documents')
    def documents(query:str=Query('',max_length=200),offset:int=Query(0,ge=0),state:Literal['all','pending','ready','mismatch','processing','failed']='all',batch_id:str=Query('',max_length=200),limit:int=Query(50,ge=20,le=100),sort:Literal['title','title_desc','updated_desc','updated_asc']='title',filters:InventoryFilters=Depends()):
        administrator()
        if limit not in {20,50,100}:raise HTTPException(400,'每页可选择 20、50 或 100 份')
        from .processing_inventory import listing
        return listing(root(),query,state,offset,limit,batch_id,filters,sort)

    @router.get('/rag-documents/{identifier}/policy')
    def document_policy(identifier:UUID):
        administrator()
        from .processing_policy import effective
        with r.database.transaction(root()) as db:
            document=r.one(db,'SELECT id,processing_config,processing_version FROM kb_editions WHERE id=:id AND NOT deleted AND owner_role IS NULL',id=identifier)
            return {**effective(db,identifier),'version':document['processing_version'],'override':document['processing_config'],
                    'default_processing':retrieval_settings.read(root(),db)['options']['ingestion']}

    @router.put('/rag-documents/{identifier}/policy')
    def save_document_policy(identifier:UUID,payload:DocumentPolicy):
        administrator()
        from .processing_policy import save
        with r.database.transaction(root()) as db:
            r.one(db,'SELECT id FROM kb_editions WHERE id=:id AND NOT deleted AND owner_role IS NULL',id=identifier)
            result=save(db,identifier,payload.processing.model_dump() if payload.processing else None,payload.version)
            return {**result,'version':result['document_version'],'override':payload.processing.model_dump() if payload.processing else None}

    @router.post('/rag-documents/{identifier}/preview')
    def preview_document(identifier:UUID,payload:DocumentPreview):
        administrator()
        from .processing_policy import effective
        from .adaptive_chunking import plan_blocks
        from .models import embed_cached
        with r.database.transaction(root(),isolation_level='REPEATABLE READ') as db:
            document=r.one(db,'SELECT e.*,w.item_type FROM kb_editions e JOIN kb_works w ON w.id=e.work_id WHERE e.id=:id AND NOT e.deleted AND e.owner_role IS NULL',id=identifier)
            if not document['active_revision']:raise HTTPException(409,'文档尚无录文，请先解析原件')
            if payload.expected_revision and str(document['active_revision'])!=str(payload.expected_revision):raise HTTPException(409,'录文已更新，请刷新后预览')
            chosen=payload.processing.model_dump() if payload.processing else effective(db,identifier)['processing']
            blocks=r.rows(db,'SELECT id,asset_id,page_index,ordinal,kind,section_path,quality,left(body,200000) body FROM kb_blocks WHERE revision_id=:revision ORDER BY ordinal LIMIT 200',revision=document['active_revision'])
        bounded=[];remaining=200000
        for b in blocks:
            if remaining<=0:break
            bounded.append({**b,'body':b['body'][:remaining]});remaining-=len(b['body'])
        plan=plan_blocks(bounded,chosen['chunking'],embedder=lambda texts:embed_cached(root(),texts),metadata=document)
        return {'analysis':plan['analysis'],'resolved':plan['resolved'],'revision_id':str(document['active_revision']),
                'chunks':plan['chunks'][:100],'total':len(plan['chunks']),'truncated':len(plan['chunks'])>100,
                'sampled':True,'notice':'预览使用前 200 个原文块、最多 20 万字符；全量加工会按完整文档再次分析。'}

    @router.post('/rag-process')
    def process_documents(payload:ProcessDocuments):
        administrator()
        from .worker import worker
        jobs=[];batch_id=r.uid();used_batches=set()
        with r.database.transaction(root()) as db:
            policy=retrieval_settings.read(root(),db)
            if payload.expected_policy_version is not None and payload.expected_policy_version!=policy['version']:
                raise HTTPException(409,'向量模型或全局配置已更新，请刷新文献列表后重新提交')
            processing=payload.processing.model_dump() if payload.processing else policy['options']['ingestion']
            for selected in sorted(payload.documents,key=lambda d:str(d.id)):
                current=r.one(db,'SELECT id,active_revision FROM kb_editions WHERE id=:id AND NOT deleted AND owner_role IS NULL FOR UPDATE',id=selected.id)
                if str(current['active_revision'])!=str(selected.revision_id):raise HTTPException(409,'文献录文已经变化，请刷新文献列表后重试')
                kind='index' if payload.operation=='index' or payload.operation=='process' and current['active_revision'] else 'import'
                if kind=='index' and not current['active_revision']:raise HTTPException(409,'文献还没有录文，请先重新解析原件')
                if kind=='import':
                    if r.execute(db,'SELECT 1 FROM kb_core_books WHERE edition_id=:id',id=selected.id).scalar():raise HTTPException(409,'核心古籍请使用原有更新流程；此处可重建其索引')
                    if not r.execute(db,"SELECT 1 FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=:id AND a.availability='present' LIMIT 1",id=selected.id).scalar():raise HTTPException(409,'文献没有可用原件，请先补充原件')
                existing=r.rows(db,"SELECT * FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('index','import') "
                    "AND payload->>'edition_id'=:id AND state IN ('queued','running') ORDER BY created_at LIMIT 1",id=str(selected.id))
                from .processing_policy import effective
                chosen=effective(db,selected.id,processing) if payload.use_document_policies else {'processing':processing,'source':'draft','document_version':None}
                selected_processing=chosen['processing']
                values={'edition_id':str(selected.id),'processing_batch':batch_id,'policy_version':policy['version'],'policy_source':chosen['source'],'document_policy_version':chosen['document_version']}
                if kind=='index':values.update(revision_id=str(current['active_revision']),config=selected_processing['chunking'])
                else:values.update(expected_revision=str(current['active_revision']) if current['active_revision'] else None,processing=selected_processing)
                if existing:
                    prior=existing[0]
                    from .embedding_config import EmbeddingOptions
                    same=prior['kind']==kind and (prior['payload'].get('embedding') or EmbeddingOptions().model_dump(mode='json'))==policy['options']['embedding'] and (prior['payload'].get('config')==values.get('config') and prior['payload'].get('revision_id')==values['revision_id'] if kind=='index' else prior['payload'].get('processing')==selected_processing and prior['payload'].get('expected_revision')==values['expected_revision'] and 'asset_id' not in prior['payload'])
                    if not same:raise HTTPException(409,'所选文献已有不同参数的处理任务，请等待完成或取消后重试')
                    job=prior
                else:job=r.queue(db,kind,values)
                used_batches.add(job['payload'].get('processing_batch') or r.execute(db,"SELECT 'legacy:'||created_at::text FROM kb_jobs WHERE id=:id",id=job['id']).scalar())
                jobs.append({'edition_id':str(selected.id),'id':str(job['id']),'kind':kind,'state':job['state']})
        worker.start(root())
        return {'jobs':jobs,'batch_id':next(iter(used_batches)) if len(used_batches)==1 else None,'processing':processing,'policy_version':policy['version'],'embedding':{'model':policy['options']['embedding']['model'],'dimensions':policy['options']['embedding']['dimensions']}}
