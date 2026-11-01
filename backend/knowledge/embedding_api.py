"""Administrator configuration helpers. Public settings never include key bytes."""
import time
from fastapi import HTTPException
from pydantic import SecretStr
from .schemas import Strict
from .embedding_config import EmbeddingOptions,PRESETS,current,space
from . import repository as r


class Credential(Strict):
    api_key:SecretStr
    base_url:str


class ModelRequest(Strict):
    config:EmbeddingOptions


def status(root,connection=None):
    config=current(root,connection)
    from .embedding_secrets import read
    try:has_key=bool(read(root,config))
    except Exception:has_key=False
    matching=other=0
    if connection is not None:
        from .embedding_config import LEGACY_MODEL
        legacy=config['provider']=='local' and config['model']==LEGACY_MODEL and not config['document_prefix'] and not config['query_prefix'] and not config['revision']
        counts=r.one(connection,"SELECT count(*) total,count(*) FILTER(WHERE i.config->>'embedding_space'=:space OR (i.config->>'embedding_space' IS NULL AND :legacy)) matching FROM kb_editions e JOIN kb_indexes i ON i.id=e.active_index WHERE NOT e.deleted AND i.state='ready' AND i.model_key IS NOT NULL",space=space(config),legacy=legacy)
        matching=counts['matching'];other=counts['total']-matching
    from .inference_runtime import status as runtime_status
    return {**config,'inference':runtime_status(root),'execution':'server','has_api_key':has_key,'matching_indexes':matching,'other_model_indexes':other,
            'space':space(config)}


def register(router,root):
    def admin():
        from .shared import private_schema
        if private_schema():raise HTTPException(403,'向量模型由管理员统一配置')

    @router.get('/embedding-info')
    def info():
        admin()
        from .embedding_cache import inventory
        local=inventory(root(),PRESETS)
        with r.database.transaction(root()) as db:
            jobs=r.rows(db,"SELECT id,state,stage,progress,error FROM kb_jobs WHERE kind='embedding_model' AND workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC LIMIT 1")
            preparations=r.rows(db,"SELECT DISTINCT ON (payload->'embedding'->>'model') id,payload->'embedding'->>'model' model,state,stage,progress,error FROM kb_jobs WHERE kind='embedding_model' AND workspace_key=current_setting('shixue.workspace') ORDER BY payload->'embedding'->>'model',created_at DESC,id DESC")
            current_status=status(root(),db)
        for job in preparations:
            if job['model'] in local and job['state'] in {'queued','running'}:
                local[job['model']]={**local[job['model']],'state':'preparing','progress':job['progress'],'stage':job['stage'],'message':'后台正在准备此模型。'}
        presets=[{**p,'availability':local.get(p['model'],{'state':'external','bytes':0,'message':'由外部服务部署，本后台不下载该模型。'})} for p in PRESETS]
        return {'presets':presets,'status':current_status,'job':jobs[0] if jobs else None,'preparation_jobs':preparations}

    @router.post('/embedding-credential')
    def credential(payload:Credential):
        admin()
        value=payload.api_key.get_secret_value().strip()
        if not value or len(value)>2000:raise HTTPException(400,'请填写有效的嵌入服务密钥')
        from .embedding_secrets import store
        try:address=EmbeddingOptions(provider='openai_compatible',model='custom',base_url=payload.base_url).base_url
        except ValueError:raise HTTPException(400,'请先填写有效的嵌入服务根地址') from None
        return {'credential_id':store(root(),value,address),'has_api_key':True}

    @router.post('/embedding-test')
    def test(payload:ModelRequest):
        admin()
        from .models import embedding_context,embedding_model,embed
        start=time.monotonic();config=payload.config.model_dump(mode='json')
        try:
            with embedding_context(config):
                if config['provider']=='local':embedding_model(root(),offline=True)
                documents,key=embed(root(),['文献向量服务连接测试'])
            with embedding_context(config,'query'):queries,query_key=embed(root(),['查找相关文献'])
            if key!=query_key:raise RuntimeError('space changed')
        except Exception:raise HTTPException(400,'测试失败：请检查模型准备状态、服务地址、密钥、维度和查询前缀；服务响应及密钥不会回显') from None
        return {'ok':True,'dimensions':len(documents[0]),'query_dimensions':len(queries[0]),'model_key':key,'elapsed_ms':round((time.monotonic()-start)*1000),
                'notice':'仅使用界面说明中的固定测试句，不发送文献；接口服务可能计费。'}

    @router.post('/embedding-prepare')
    def prepare(payload:ModelRequest):
        admin()
        if payload.config.provider!='local':raise HTTPException(400,'兼容接口的模型由该服务部署，无需下载到本后台')
        with r.database.transaction(root()) as db:
            r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||:model,0))",model='embedding:'+payload.config.model)
            existing=r.rows(db,"SELECT * FROM kb_jobs WHERE kind='embedding_model' AND workspace_key=current_setting('shixue.workspace') AND payload->'embedding'->>'model'=:model AND state IN ('queued','running') ORDER BY created_at,id LIMIT 1",model=payload.config.model)
            result=existing[0] if existing else r.queue(db,'embedding_model',{'embedding':payload.config.model_dump(mode='json')})
        from .worker import worker
        worker.start(root());return result
