"""Quality tools stay inside the unified library and authenticated API."""
from uuid import UUID

from fastapi import HTTPException,Query

from . import repository as r
from .schemas import EvalSuite,EvalCase,ChunkPreview,Reindex,RetrievalSettings,Search
from .chunking import preview,configured_spans,stored_config,IDENTITY
from .evaluation import suite,add_case,queue_run
from .worker import worker


def register(router,root,resolve_scope):
    from .pipeline_api import register as register_pipeline
    register_pipeline(router,root)
    from .embedding_api import register as register_embedding
    register_embedding(router,root)
    def require_configuration_host():
        from .shared import private_schema
        if private_schema():raise HTTPException(403,'知识库配置与检索测试由管理员管理')

    @router.get('/retrieval-status')
    def retrieval_status():
        require_configuration_host()
        from . import models,reranking
        with r.database.transaction(root()) as connection:
            counts=r.one(connection,"SELECT count(*) documents,count(*) FILTER(WHERE e.active_index IS NOT NULL AND i.state='ready' AND i.model_key IS NOT NULL) vector_ready, "
                         "count(*) FILTER(WHERE i.state='failed') index_failed FROM kb_editions e LEFT JOIN kb_indexes i ON i.id=e.active_index WHERE NOT e.deleted")
            # An exact count over the chunks table takes seconds on a multi-million
            # chunk library and exceeds the 5-second statement budget while a bulk
            # ingestion is running. The status panel only needs a scale indicator,
            # so use the table estimate once the table is large.
            estimate=r.execute(connection,"SELECT reltuples::bigint FROM pg_class WHERE oid='public.kb_chunks'::regclass").scalar_one()
            chunks=(estimate if estimate and estimate>500000 else
                    r.execute(connection,'SELECT count(*) FROM kb_chunks c JOIN kb_editions e ON e.active_index=c.index_id WHERE NOT e.deleted').scalar_one())
            jobs=r.rows(connection,"SELECT id,state,stage,progress,error FROM kb_jobs WHERE kind='rerank_model' AND workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC LIMIT 1")
            from .embedding_api import status as embedding_status
            embedding=embedding_status(root(),connection)
        return {'embedding':embedding,'reranker':reranking.status(root()),
                'index':{**counts,'chunks':chunks},'preparation_job':jobs[0] if jobs else None}

    @router.post('/retrieval-test')
    def retrieval_test(payload:Search):
        require_configuration_host()
        if payload.source_kinds!=['document'] or payload.scope.project_id or payload.scope.library_visibility=='personal':
            raise HTTPException(400,'后台检索测试仅使用系统文献')
        from .retrieval import search
        data=resolve_scope(payload)
        data['scope'].update(library_visibility='system',include_core=True)
        found=search(root(),data)
        fields=('block_id','edition_id','title','label','asset_id','page_index','section_path','score','semantic_score','rerank_score','rank','channels')
        return {**{key:found[key] for key in ('warnings','mode','retrieval','retrieval_run_id','diagnostics')},
                'configuration_source':'draft' if payload.retrieval else 'saved',
                'results':[{**{key:item[key] for key in fields if key in item},'body':item['body'][:2000],
                            'context_characters':item.get('parent_context',{}).get('characters',0)} for item in found['results']]}

    @router.get('/retrieval-settings')
    def retrieval_config():
        from . import retrieval_settings,reranking
        return {**retrieval_settings.read(root()),'model':reranking.status(root())}

    @router.put('/retrieval-settings')
    def save_retrieval_config(payload:RetrievalSettings):
        from . import retrieval_settings,reranking
        return {**retrieval_settings.save(root(),payload.model_dump(mode='json')),'model':reranking.status(root())}

    @router.post('/rerank-model/prepare')
    def prepare_reranker():
        with r.database.transaction(root()) as connection:
            r.execute(connection,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace') || '_rerank_model',0))")
            pending=r.rows(connection,"SELECT * FROM kb_jobs WHERE kind='rerank_model' AND workspace_key=current_setting('shixue.workspace') "
                           "AND state IN ('queued','running') ORDER BY created_at LIMIT 1")
            result=pending[0] if pending else r.queue(connection,'rerank_model',{})
        worker.start(root());return result

    @router.get('/retrieval-runs')
    def recent(limit:int=Query(20,ge=1,le=100)):
        with r.database.transaction(root()) as connection:
            return r.rows(connection,"SELECT id,query,mode,diagnostics,created_at FROM kb_retrieval_runs "
                          "WHERE workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC,id DESC LIMIT :n",n=limit)

    @router.get('/retrieval-runs/{identifier}')
    def trace(identifier:UUID):
        with r.database.transaction(root()) as connection:
            return r.one(connection,"SELECT * FROM kb_retrieval_runs WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)

    @router.post('/chunk-preview')
    def text_preview(payload:ChunkPreview):
        return preview(payload.text,payload.config.model_dump(),root())

    @router.post('/editions/{identifier}/chunk-preview')
    def document_preview(identifier:UUID,payload:Reindex):
        with r.database.transaction(root(),isolation_level='REPEATABLE READ') as connection:
            current=r.edition(connection,identifier)
            if not current['active_revision']:raise HTTPException(409,'请先导入原文')
            if payload.expected_revision and str(current['active_revision'])!=str(payload.expected_revision):
                raise HTTPException(409,'原文已更新，请重新读取后预览')
            previous=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
            config=payload.config.model_dump() if payload.config else stored_config(previous)
            blocks=r.rows(connection,'SELECT id,asset_id,ordinal,body,page_index,kind,section_path,quality FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=current['active_revision'])
        from .adaptive_chunking import plan_blocks
        from .models import embed_cached
        plan=plan_blocks(blocks,config,embedder=lambda texts:embed_cached(root(),texts),metadata=current)
        items=[{'block_id':str(i['block']['id']),'page_index':i['block']['page_index'],'kind':i['block']['kind'],
                'section_path':i['block']['section_path'],'start':i['start'],'end':i['end'],'body':i['body']} for i in plan['items'][:200]]
        return {'revision_id':str(current['active_revision']),'config':{**config,'chunker':IDENTITY},'analysis':plan['analysis'],
                'chunks':items,'total':len(plan['items']),'truncated':len(plan['items'])>len(items),
                'warnings':plan['analysis']['warnings']+['预览保持原文字符范围；OCR、双栏及复杂表格仍需核对。字符预算不是模型 token 预算。']}

    @router.get('/evaluation/suites')
    def suites():
        with r.database.transaction(root()) as connection:
            return r.rows(connection,"SELECT s.*,(SELECT count(*) FROM kb_eval_cases c WHERE c.suite_id=s.id) case_count "
                          "FROM kb_eval_suites s WHERE workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC")

    @router.post('/evaluation/suites')
    def create_suite(payload:EvalSuite):
        with r.database.transaction(root()) as connection:
            identifier=r.uid()
            r.execute(connection,"INSERT INTO kb_eval_suites(id,workspace_key,name) VALUES(:id,current_setting('shixue.workspace'),:n)",id=identifier,n=payload.name)
            return suite(connection,identifier)

    @router.get('/evaluation/suites/{identifier}')
    def detail(identifier:UUID):
        with r.database.transaction(root()) as connection:
            value=suite(connection,identifier)
            value['cases']=r.rows(connection,'SELECT * FROM kb_eval_cases WHERE suite_id=:id ORDER BY created_at,id',id=identifier)
            value['runs']=r.rows(connection,'SELECT er.id,er.report,er.created_at,j.state,j.stage,j.progress,j.error FROM kb_eval_runs er '
                                 'JOIN kb_jobs j ON j.id=er.job_id WHERE er.suite_id=:id ORDER BY er.created_at DESC,er.id DESC LIMIT 20',id=identifier)
            return value

    @router.post('/evaluation/suites/{identifier}/cases')
    def create_case(identifier:UUID,payload:EvalCase):
        return add_case(root(),identifier,payload.model_dump(mode='json'),resolve_scope(payload.search))

    @router.delete('/evaluation/suites/{identifier}/cases/{case_id}')
    def delete_case(identifier:UUID,case_id:UUID):
        with r.database.transaction(root()) as connection:
            suite(connection,identifier)
            r.one(connection,'DELETE FROM kb_eval_cases WHERE id=:c AND suite_id=:s RETURNING id',c=case_id,s=identifier)
        return {'deleted':True}

    @router.post('/evaluation/suites/{identifier}/runs')
    def evaluate(identifier:UUID):
        result=queue_run(root(),identifier);worker.start(root());return result
