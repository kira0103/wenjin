"""Shared failure selection, diagnostics, export and original-configuration retry."""
import hashlib
import json
import time
from pathlib import Path
from uuid import UUID
from fastapi import HTTPException
from . import repository as r
from .failure_diagnostics import CATEGORIES, EMPTY, GENERIC, STAGES, diagnostic, public_config, redact

CATEGORY_SQL="COALESCE(NULLIF(j.result->'diagnostic'->>'category',''),j.result->'diagnostic_history'->-1->>'category',CASE WHEN j.error=:empty THEN 'empty_text' WHEN j.error IS NULL OR j.error='' OR j.error=:generic THEN 'unknown' ELSE 'other' END)"
BASE="""WITH jobs AS MATERIALIZED (
 SELECT DISTINCT ON (payload->>'edition_id') id,kind,payload,result,error,state,stage,attempts,created_at,updated_at
 FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index')
 ORDER BY payload->>'edition_id',CASE WHEN state IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC,id DESC
), failed_ids AS MATERIALIZED (
 SELECT DISTINCT payload->>'edition_id' edition_id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index')
 AND (state='failed' OR result ? 'diagnostic_history')
), scoped AS (
 SELECT j.*,e.id edition_id,w.title,e.active_revision,e.active_index,COALESCE(j.result->'diagnostic'->>'stage',j.result->'diagnostic_history'->-1->>'stage',j.stage) failure_stage,"""+CATEGORY_SQL+""" category
 FROM jobs j JOIN kb_editions e ON e.id=CAST(j.payload->>'edition_id' AS uuid) JOIN kb_works w ON w.id=e.work_id
 WHERE NOT e.deleted AND e.owner_role IS NULL AND (w.title ILIKE :q OR j.id::text ILIKE :q)
 AND (:batch='' OR COALESCE(j.payload->>'processing_batch','legacy:'||j.created_at::text)=:batch)
 AND ((:view='failed' AND j.state='failed') OR (:view='processing' AND j.state IN ('queued','running') AND e.id::text IN (SELECT edition_id FROM failed_ids))
 OR (:view='recovered' AND j.state='succeeded' AND e.id::text IN (SELECT edition_id FROM failed_ids))
 OR (:view='history' AND e.id::text IN (SELECT edition_id FROM failed_ids)))
) """

def filters(query='',batch='',stage='',category='',view='failed'):
    return {'q':'%'+query+'%','batch':batch,'stage':stage,'category':category,'view':view,'empty':EMPTY,'generic':GENERIC}

WHERE=" WHERE (:stage='' OR failure_stage=:stage) AND (:category='' OR category=:category) "

def present(job):
    value=diagnostic(job)
    source=(job.get('result') or {}).get('progress') or {}
    progress={k:source[k] for k in ('completed','total','unit','filename','embedded','statement_timeout_seconds') if k in source}
    if 'filename' in progress:progress['filename']=redact(progress['filename'])
    if isinstance(source.get('files'),list):
        progress['files']=[{k:(redact(v[k]) if k=='filename' else v[k]) for k in ('filename','completed','total') if k in v} for v in source['files'][:100] if isinstance(v,dict)]
    return {'id':str(job['edition_id']),'title':job['title'],'job_id':str(job['id']),'kind':job['kind'],'state':job['state'],
            'stage':value.get('stage') or job['stage'],'stage_label':STAGES.get(value.get('stage') or job['stage'],value.get('stage') or job['stage']),
            'updated_at':job['updated_at'],'diagnostic':value,'config':public_config(job['payload']),
            'progress':progress,'attempts':job['attempts']}

def listing(root,selection,offset,limit):
    with r.database.transaction(root) as db:
        total=r.execute(db,BASE+'SELECT count(*) FROM scoped'+WHERE,**selection).scalar()
        categories=r.rows(db,BASE+"SELECT category,count(*) total FROM scoped WHERE (:stage='' OR failure_stage=:stage) GROUP BY category ORDER BY count(*) DESC",**selection)
        stages=r.rows(db,BASE+"SELECT failure_stage stage,count(*) total FROM scoped WHERE (:category='' OR category=:category) GROUP BY failure_stage ORDER BY count(*) DESC",**selection)
        counts=r.rows(db,BASE+'SELECT state,count(*) total FROM scoped'+WHERE+'GROUP BY state',**{**selection,'view':'history'})
        items=r.rows(db,BASE+'SELECT * FROM scoped'+WHERE+'ORDER BY updated_at DESC,id DESC LIMIT :limit OFFSET :offset',**selection,limit=limit,offset=offset)
        batches=r.rows(db,"SELECT COALESCE(payload->>'processing_batch','legacy:'||created_at::text) id,max(created_at) created_at FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') GROUP BY 1 ORDER BY max(created_at) DESC LIMIT 100")
    return {'total':total,'offset':offset,'limit':limit,'items':[present(j) for j in items],'counts':{v['state']:v['total'] for v in counts},
            'categories':[{**v,'label':CATEGORIES.get(v['category'],'其他已记录错误')} for v in categories],
            'stages':[{**v,'label':STAGES.get(v['stage'],v['stage'])} for v in stages],'batches':batches}

def detail(root,edition,job_id=None):
    with r.database.transaction(root) as db:
        rows=r.rows(db,"SELECT j.*,e.id edition_id,w.title,e.active_revision,e.active_index FROM kb_jobs j JOIN kb_editions e ON e.id=CAST(j.payload->>'edition_id' AS uuid) JOIN kb_works w ON w.id=e.work_id WHERE j.workspace_key=current_setting('shixue.workspace') AND j.kind IN ('import','index') AND e.id=:edition AND NOT e.deleted AND e.owner_role IS NULL ORDER BY j.created_at DESC,j.id DESC LIMIT 100",edition=edition)
        if not rows:raise HTTPException(404,'未找到该系统文献的加工记录')
        chosen=next((j for j in rows if str(j['id'])==str(job_id)),None) if job_id else rows[0]
        if not chosen:raise HTTPException(409,'任务记录不在最近 100 项中，请刷新后重新选择')
        assets=r.rows(db,'SELECT ea.asset_id,ea.filename,a.availability FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=:edition ORDER BY ea.filename',edition=edition)
        history=[{**present(j),'diagnostics':(j['result'] or {}).get('diagnostic_history',[]),'created_at':j['created_at'],'retry_of':j['payload'].get('retry_of_job_id')} for j in rows]
    return {**present(chosen),'batch_id':chosen['payload'].get('processing_batch'),'created_at':chosen['created_at'],
            'revision_id':str(chosen['active_revision']) if chosen['active_revision'] else None,'has_index':bool(chosen['active_index']),
            'files':assets,'history':history,'history_limit':100}

def export(root,selection):
    with r.database.transaction(root) as db:
        jobs=r.rows(db,BASE+'SELECT * FROM scoped'+WHERE+'ORDER BY updated_at DESC,id DESC',**selection)
    return [{**present(j),'title':redact(j['title'])} for j in jobs]

def signature(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def preview(root,selection,ids,excluded):
    selection={**selection,'view':'failed'}
    extra=''
    if ids is not None:extra+=' AND edition_id=ANY(CAST(:ids AS uuid[]))'
    if excluded:extra+=' AND NOT (edition_id=ANY(CAST(:excluded AS uuid[])))'
    with r.database.transaction(root) as db:
        jobs=r.rows(db,BASE+'SELECT * FROM scoped'+WHERE+extra+'ORDER BY edition_id',**selection,ids=ids or [],excluded=excluded)
        targets=[];skipped=0;groups={}
        for j in jobs:
            payload=j['payload'];revision=payload.get('revision_id') if j['kind']=='index' else payload.get('expected_revision')
            active=str(j['active_revision']) if j['active_revision'] else None
            embedding=payload.get('embedding') or {}
            config=payload.get('config') if j['kind']=='index' else (payload.get('processing') or {}).get('chunking')
            complete=all(embedding.get(k) is not None for k in ('provider','model','dimensions')) and isinstance(config,dict) and all(k in config for k in ('mode','size','overlap'))
            if not complete or (j['kind']=='import' and 'ocr_mode' not in (payload.get('processing') or {})) or revision!=active:
                skipped+=1;continue
            config=public_config(payload);config.pop('revision_id',None)
            key=json.dumps(config,sort_keys=True)
            group=groups.setdefault(key,{'config':config,'count':0});group['count']+=1
            targets.append({'id':str(j['edition_id']),'revision_id':active,'job_id':str(j['id']),'signature':signature(payload)})
    if not targets:raise HTTPException(409,'所选失败文献的原配置缺失或录文已改变，请查看详情并选择新的处理配置')
    identity=r.uid();manifest={'created_at':time.time(),'filters':selection,'targets':targets,'groups':list(groups.values()),'skipped':skipped}
    from backend.portal_database import save_private_json
    save_private_json(Path(root)/'private'/'failure-previews'/(identity+'.json'),manifest)
    return {'preview_id':identity,'total':len(targets),'skipped':skipped,'groups':list(groups.values()),'expires_in':900}

def submit(root,identity,take=None):
    path=Path(root)/'private'/'failure-previews'/(str(UUID(str(identity)))+'.json')
    try:manifest=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise HTTPException(404,'重试预览不存在，请重新预览') from None
    if time.time()-manifest['created_at']>900:raise HTTPException(409,'重试预览已过期，请重新预览')
    targets=manifest['targets'][:take] if take else manifest['targets']
    with r.database.transaction(root) as db:
        r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||'rag-bulk',0))")
        prior=r.rows(db,"SELECT id FROM kb_jobs WHERE kind='rag_batch' AND workspace_key=current_setting('shixue.workspace') AND payload->>'retry_preview'=:preview",preview=str(identity))
        if prior:raise HTTPException(409,'这份预览已经提交，请查看队列；处理其余文献需重新筛选预览')
        if r.execute(db,"SELECT 1 FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind='rag_batch' AND state='dispatching' LIMIT 1").scalar():raise HTTPException(409,'已有批次正在分发，请等待其分发完成后提交重试')
        job=r.queue(db,'rag_batch',{'retry_original':True,'retry_preview':str(identity),'targets':targets,'config_groups':manifest['groups']})
        r.execute(db,"UPDATE kb_jobs SET state='dispatching',stage='dispatching',result=CAST(:result AS jsonb) WHERE id=:id",id=job['id'],result=r.j({'cursor':0,'scheduled':0,'skipped':0,'total':len(targets)}))
    from .bulk_processing import dispatcher
    dispatcher.start(root)
    return {'batch_id':str(job['id']),'total':len(targets),'state':'dispatching'}

def dispatch_retry(db,controller,owner):
    payload=controller['payload'];status=controller['result'];targets=payload['targets'];start=status.get('cursor',0);scheduled=status.get('scheduled',0);skipped=status.get('skipped',0)
    for target in targets[start:start+50]:
        edition=r.rows(db,'SELECT active_revision FROM kb_editions WHERE id=:id AND NOT deleted AND owner_role IS NULL FOR UPDATE',id=target['id'])
        if not edition or (str(edition[0]['active_revision']) if edition[0]['active_revision'] else None)!=target['revision_id']:skipped+=1;continue
        latest=r.rows(db,"SELECT * FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') AND payload->>'edition_id'=:id ORDER BY CASE WHEN state IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC,id DESC LIMIT 1 FOR UPDATE",id=target['id'])
        if not latest or str(latest[0]['id'])!=target['job_id'] or latest[0]['state']!='failed' or signature(latest[0]['payload'])!=target['signature']:skipped+=1;continue
        original=latest[0];values={k:v for k,v in original['payload'].items() if not k.startswith('_job_')}
        values.update(processing_batch=str(controller['id']),retry_of_job_id=target['job_id'])
        queued=r.queue(db,original['kind'],values)
        history=(original['result'] or {}).get('diagnostic_history') or [{**diagnostic(original),'failed_at':str(original['updated_at'])}]
        r.execute(db,"UPDATE kb_jobs SET result=jsonb_build_object('diagnostic_history',CAST(:history AS jsonb)) WHERE id=:id",id=queued['id'],history=r.j(history))
        scheduled+=1
    cursor=min(start+50,len(targets));done=cursor==len(targets)
    r.execute(db,"UPDATE kb_jobs SET result=CAST(:result AS jsonb),progress=:progress,state=:state,stage=:stage,lease_until=now()+interval '30 seconds',updated_at=now() WHERE id=:id AND owner=:owner",id=controller['id'],owner=owner,result=r.j({'cursor':cursor,'scheduled':scheduled,'skipped':skipped,'total':len(targets)}),progress=int(100*cursor/max(1,len(targets))),state='succeeded' if done else 'dispatching',stage='dispatched' if done else 'dispatching')
    return not done
