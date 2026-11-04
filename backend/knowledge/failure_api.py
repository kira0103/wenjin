import json
from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Query
from pydantic import Field
from .schemas import Strict
from .shared import private_schema
from . import failure_service as service

class Selection(Strict):
    query:str=Field(default='',max_length=200)
    batch:str=Field(default='',max_length=200)
    stage:str=Field(default='',max_length=80)
    category:Literal['','unknown','empty_text','resource','model','file','database','timeout','other']=''
    view:Literal['failed','processing','recovered','history']='failed'

class RetrySelection(Selection):
    ids:list[UUID]|None=Field(default=None,max_length=50000)
    excluded:list[UUID]=Field(default_factory=list,max_length=50000)

class RetrySubmit(Strict):
    preview_id:UUID
    take:Literal[5]|None=None
    confirm:Literal[True]

def register(router,root):
    def admin():
        if private_schema():raise HTTPException(403,'失败诊断与系统文献加工由管理员管理')

    @router.get('/rag-failures')
    def failures(query:str=Query('',max_length=200),batch:str=Query('',max_length=200),stage:str=Query('',max_length=80),category:Literal['','unknown','empty_text','resource','model','file','database','timeout','other']='',view:Literal['failed','processing','recovered','history']='failed',offset:int=Query(0,ge=0),limit:int=Query(50,ge=20,le=100)):
        admin()
        return service.listing(root(),service.filters(query,batch,stage,category,view),offset,limit)

    @router.get('/rag-failures/{identifier}')
    def detail(identifier:UUID,job_id:UUID|None=None):
        admin()
        return service.detail(root(),identifier,job_id)

    @router.post('/rag-failures/export')
    def export(body:Selection):
        admin()
        from datetime import datetime
        records=service.export(root(),service.filters(**body.model_dump()))
        return {'filename':'入库诊断-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json','content':json.dumps({'scope':body.model_dump(),'total':len(records),'records':records},ensure_ascii=False,indent=2,default=str)}

    @router.post('/rag-failures/retry-preview')
    def preview(body:RetrySelection):
        admin()
        return service.preview(root(),service.filters(**body.model_dump(exclude={'ids','excluded'})),None if body.ids is None else [str(v) for v in body.ids],[str(v) for v in body.excluded])

    @router.post('/rag-failures/retry')
    def retry(body:RetrySubmit):
        admin()
        return service.submit(root(),body.preview_id,body.take)
