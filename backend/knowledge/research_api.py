"""Authenticated run lifecycle; scope is resolved on the server."""
from uuid import UUID
from fastapi import Query,HTTPException
from . import repository as r
from .research_assistant import ResearchRequest,create,detail
from .worker import worker


def register(router,root,resolve_scope,model_settings,completion):
    worker.research_model_settings=model_settings
    worker.research_completion=completion

    @router.get('/research-runs')
    def recent(limit:int=Query(30,ge=1,le=100),project_id:str|None=Query(None,max_length=100)):
        with r.database.transaction(root()) as connection:
            return r.rows(connection,"SELECT rr.id,rr.job_id,rr.question,rr.verified,rr.created_at,j.state,j.stage,j.progress,j.error "
                          "FROM kb_research_runs rr JOIN kb_jobs j ON j.id=rr.job_id "
                          "WHERE rr.workspace_key=current_setting('shixue.workspace') AND rr.snapshot->'execution'->>'team_id' IS NULL "
                          "AND (CAST(:project AS text) IS NULL OR COALESCE(rr.request->>'context_project_id',rr.request->'scope'->>'project_id')=:project) ORDER BY rr.created_at DESC LIMIT :n",n=limit,project=project_id)

    @router.post('/research-runs')
    def start(payload:ResearchRequest):
        result=create(root(),payload,resolve_scope(payload),model_settings())
        worker.start(root());return result

    @router.get('/research-runs/{identifier}')
    def get(identifier:UUID):return detail(root(),identifier)

    @router.post('/research-runs/{identifier}/cancel')
    def cancel(identifier:UUID):
        current=detail(root(),identifier)
        with r.database.transaction(root()) as connection:
            r.execute(connection,"UPDATE kb_jobs SET state='cancelled',updated_at=now() WHERE id=:id "
                      "AND workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running')",id=current['job_id'])
        return detail(root(),identifier)

    @router.post('/research-runs/{identifier}/retry')
    def retry(identifier:UUID):
        current=detail(root(),identifier)
        if current['snapshot'].get('execution',{}).get('team_id'):raise HTTPException(409,'请在协同研究中重试分支')
        if current['state'] in ('queued','running'):raise HTTPException(409,'研究仍在运行，请先停止')
        return start(ResearchRequest.model_validate(current['request']).model_copy(update={'request_key':None}))
