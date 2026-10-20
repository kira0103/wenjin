"""Quality tools stay inside the unified library and authenticated API."""
from uuid import UUID

from fastapi import HTTPException,Query

from . import repository as r
from .schemas import EvalSuite,EvalCase,ChunkPreview,Reindex,RetrievalSettings
from .chunking import preview,configured_spans,stored_config,IDENTITY
from .evaluation import suite,add_case,queue_run
from .worker import worker


def register(router,root,resolve_scope):
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
        return preview(payload.text,payload.config.model_dump())

    @router.post('/editions/{identifier}/chunk-preview')
    def document_preview(identifier:UUID,payload:Reindex):
        with r.database.transaction(root(),isolation_level='REPEATABLE READ') as connection:
            current=r.edition(connection,identifier)
            if not current['active_revision']:raise HTTPException(409,'请先导入原文')
            if payload.expected_revision and str(current['active_revision'])!=str(payload.expected_revision):
                raise HTTPException(409,'原文已更新，请重新读取后预览')
            previous=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
            config=payload.config.model_dump() if payload.config else stored_config(previous)
            blocks=r.rows(connection,'SELECT id,body,page_index,kind,section_path FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=current['active_revision'])
            items=[];count=0
            for block in blocks:
                for start,end,body in configured_spans(block['body'],config):
                    count+=1
                    if len(items)<200:items.append({'block_id':str(block['id']),'page_index':block['page_index'],
                                                  'kind':block['kind'],'section_path':block['section_path'],'start':start,'end':end,'body':body})
            return {'revision_id':str(current['active_revision']),'config':{**config,'chunker':IDENTITY},
                    'chunks':items,'total':count,'truncated':count>len(items),
                    'warnings':['预览保持各原文段落及字符范围；不自动识别竖排、双栏或复杂表格。字符预算不是模型 token 预算。']}

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
