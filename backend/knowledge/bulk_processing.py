"""Durable, resumable manifest dispatch. It never performs vector inference."""
import threading
from fastapi import HTTPException
from . import repository as r
from .processing_selection import relation


def submit(root,request):
    from .retrieval_settings import read
    with r.database.transaction(root) as db:
        policy=read(root,db)
        if request.expected_policy_version is not None and policy['version']!=request.expected_policy_version:raise HTTPException(409,'全局配置已更新，请刷新后重新提交')
        # One active whole-library manifest prevents repeated clicks from
        # generating overlapping 15,000-document queues.
        r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||'rag-bulk',0))")
        prior=r.rows(db,"SELECT id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind='rag_batch' AND state='dispatching' LIMIT 1")
        if prior:raise HTTPException(409,'已有全量批次正在分批入队，请查看其进度后再提交')
        query='' if request.scope=='library' else request.query
        selected_state='all' if request.scope=='library' else request.state
        base,state,ready,parameters=relation(policy['options']['embedding'],query)
        from .processing_inventory import predicate,InventoryFilters
        inventory=request.filters if request.scope!='library' else InventoryFilters()
        extra,values=predicate(inventory);base+=extra;parameters.update(values)
        where=" AND COALESCE(latest_job.state,'') NOT IN ('queued','running') AND (e.active_revision IS NOT NULL OR EXISTS(SELECT 1 FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=e.id AND a.availability='present'))"
        if selected_state!='all':where+=f' AND ({state})=:state';parameters['state']=selected_state
        if request.exclude_ids:
            where+=' AND NOT (e.id=ANY(CAST(:excluded AS uuid[])))';parameters['excluded']=[str(identifier) for identifier in request.exclude_ids]
        if request.skip_ready:where+=f' AND NOT COALESCE({ready},false)'
        targets=r.rows(db,'SELECT e.id,e.active_revision revision_id,e.processing_config,e.processing_version '+base+where+' ORDER BY e.id',**parameters)
        if not targets:raise HTTPException(409,'没有需要新增处理的文献；已排队或已就绪的文献会自动跳过')
        payload={'processing':request.processing.model_dump() if request.processing else policy['options']['ingestion'],'embedding':policy['options']['embedding'],
                 'policy_version':policy['version'],'use_document_policies':request.use_document_policies,'skip_ready':request.skip_ready,'scope':request.scope,'query':query,'state':selected_state,'exclude_ids':[str(identifier) for identifier in request.exclude_ids],'filters':inventory.model_dump(),'targets':targets}
        # UUIDs inside the frozen manifest use JSON values, never Python repr.
        for item in targets:
            item['id']=str(item['id']);item['revision_id']=str(item['revision_id']) if item['revision_id'] else None
        job=r.queue(db,'rag_batch',payload)
        r.execute(db,"UPDATE kb_jobs SET state='dispatching',stage='dispatching',result=CAST(:result AS jsonb) WHERE id=:id",id=job['id'],result=r.j({'cursor':0,'scheduled':0,'skipped':0,'total':len(targets)}))
    dispatcher.start(root)
    return {'id':str(job['id']),'batch_id':str(job['id']),'total':len(targets),'state':'dispatching'}


def control(root,identifier,action):
    with r.database.transaction(root) as db:
        job=r.one(db,"SELECT * FROM kb_jobs WHERE id=:id AND kind='rag_batch' AND workspace_key=current_setting('shixue.workspace') FOR UPDATE",id=identifier)
        if action=='cancel':
            r.execute(db,"UPDATE kb_jobs SET state='cancelled',stage='cancelled',lease_until=NULL,owner=NULL,updated_at=now() WHERE id=:id",id=identifier)
            r.execute(db,"UPDATE kb_jobs SET state='cancelled',updated_at=now() WHERE workspace_key=current_setting('shixue.workspace') AND payload->>'processing_batch'=:batch AND state IN ('queued','running')",batch=str(identifier))
        else:
            if job['state'] not in {'failed','cancelled'}:raise HTTPException(409,'该批次无需重试')
            r.execute(db,"UPDATE kb_jobs SET state='dispatching',stage='dispatching',result=CAST(:result AS jsonb),owner=NULL,lease_until=NULL,progress=0,error='',updated_at=now() WHERE id=:id",id=identifier,result=r.j({'cursor':0,'scheduled':0,'skipped':0,'total':len(job['payload']['targets'])}))
    if action=='retry':dispatcher.start(root)
    return {'id':str(identifier),'state':'cancelled' if action=='cancel' else 'dispatching'}


def dispatch_slice(root,identifier,owner):
    """A cursor and its child jobs commit together; crash recovery cannot duplicate them."""
    with r.database.transaction(root) as db:
        job=r.one(db,"SELECT * FROM kb_jobs WHERE id=:id AND workspace_key=current_setting('shixue.workspace') FOR UPDATE",id=identifier)
        if job['state']!='dispatching' or job['owner']!=owner:return False
        if job['payload'].get('retry_original'):
            from .failure_service import dispatch_retry
            return dispatch_retry(db,job,owner)
        payload=job['payload'];state=job['result'];targets=payload['targets'];start=state.get('cursor',0);scheduled=state.get('scheduled',0);skipped=state.get('skipped',0)
        ids=[selected['id'] for selected in targets[start:start+50]]
        current_rows=r.rows(db,'SELECT id,active_revision,EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id) core FROM kb_editions e WHERE id=ANY(CAST(:ids AS uuid[])) AND NOT deleted AND owner_role IS NULL ORDER BY id FOR UPDATE',ids=ids)
        current_by_id={str(row['id']):row for row in current_rows}
        active_ids={row['edition_id'] for row in r.rows(db,"SELECT payload->>'edition_id' edition_id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') AND state IN ('queued','running') AND payload->>'edition_id'=ANY(CAST(:ids AS text[]))",ids=ids)}
        ready_ids=set()
        if payload['skip_ready']:
            _base,_status,ready,parameters=relation(payload['embedding'])
            ready_ids={str(row['id']) for row in r.rows(db,f'SELECT e.id FROM kb_editions e LEFT JOIN kb_indexes i ON i.id=e.active_index WHERE e.id=ANY(CAST(:ids AS uuid[])) AND {ready}',**parameters,ids=ids)}
        for selected in targets[start:start+50]:
            current=current_by_id.get(selected['id'])
            if not current or (str(current['active_revision']) if current['active_revision'] else None)!=selected['revision_id']:skipped+=1;continue
            if selected['id'] in active_ids or selected['id'] in ready_ids:skipped+=1;continue
            kind='index' if selected['revision_id'] else 'import'
            if kind=='import' and current['core']:skipped+=1;continue
            if kind=='import' and not r.execute(db,"SELECT 1 FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=:id AND a.availability='present' LIMIT 1",id=selected['id']).scalar():skipped+=1;continue
            processing=selected['processing_config'] if payload['use_document_policies'] and selected['processing_config'] else payload['processing']
            values={'edition_id':selected['id'],'processing_batch':str(identifier),'policy_version':payload['policy_version'],'embedding':payload['embedding'],
                    'policy_source':'document' if processing is selected['processing_config'] else 'batch','document_policy_version':selected['processing_version']}
            if kind=='index':values.update(revision_id=selected['revision_id'],config=processing['chunking'])
            else:values.update(expected_revision=None,processing=processing)
            r.queue(db,kind,values);scheduled+=1
        cursor=min(start+50,len(targets));done=cursor==len(targets)
        r.execute(db,"UPDATE kb_jobs SET result=CAST(:result AS jsonb),progress=:progress,state=:state,stage=:stage,lease_until=now()+interval '30 seconds',updated_at=now() WHERE id=:id AND owner=:owner",id=identifier,owner=owner,
                  result=r.j({'cursor':cursor,'scheduled':scheduled,'skipped':skipped,'total':len(targets)}),progress=int(100*cursor/max(1,len(targets))),state='succeeded' if done else 'dispatching',stage='dispatched' if done else 'dispatching')
        return not done


class Dispatcher:
    def __init__(self):self.lock=threading.RLock();self.thread=None;self.stop_event=threading.Event();self.owner=r.uid()
    def start(self,root):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.stop_event.clear();self.thread=threading.Thread(target=self.loop,args=(root,),daemon=True,name='rag-dispatcher');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=6)
    def loop(self,root):
        while not self.stop_event.is_set():
            identifier=None
            try:
                with r.database.transaction(root) as db:
                    rows=r.rows(db,"UPDATE kb_jobs SET owner=:owner,lease_until=now()+interval '30 seconds' WHERE id=(SELECT id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind='rag_batch' AND state='dispatching' AND (owner=:owner OR lease_until IS NULL OR lease_until<now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id",owner=self.owner)
                if rows:
                    identifier=rows[0]['id'];dispatch_slice(root,identifier,self.owner)
                    from .worker import worker
                    worker.start(root)
                else:self.stop_event.wait(1)
            except Exception:
                if identifier:
                    try:
                        with r.database.transaction(root) as db:r.execute(db,"UPDATE kb_jobs SET state='failed',error='分批入队失败，已入队任务保留，可重试继续',lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner AND state='dispatching'",id=identifier,owner=self.owner)
                    except Exception:pass
                self.stop_event.wait(1)


dispatcher=Dispatcher()


def cancel_all(root):
    """Stop only system ingestion, including not-yet-dispatched manifests."""
    with r.database.transaction(root) as db:
        r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||'rag-bulk',0))")
        controllers=r.rows(db,"SELECT id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind='rag_batch' AND (state='dispatching' OR EXISTS(SELECT 1 FROM kb_jobs child WHERE child.workspace_key=current_setting('shixue.workspace') AND child.payload->>'processing_batch'=kb_jobs.id::text AND child.state IN ('queued','running'))) ORDER BY id FOR UPDATE")
        for job in controllers:r.execute(db,"UPDATE kb_jobs SET state='cancelled',stage='cancelled',owner=NULL,lease_until=NULL,updated_at=now() WHERE id=:id",id=job['id'])
        # Wait for an in-flight publish before the next statement; any index
        # child created by that publish is included in the cancellation below.
        r.rows(db,"SELECT id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') AND state IN ('queued','running') ORDER BY id FOR UPDATE")
        cancelled=r.rows(db,"UPDATE kb_jobs SET state='cancelled',stage='cancelled',owner=NULL,lease_until=NULL,updated_at=now() WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') AND state IN ('queued','running') RETURNING kind")
        return {'cancelled_jobs':len(cancelled),'cancelled_dispatches':len(controllers),'by_kind':{kind:sum(j['kind']==kind for j in cancelled) for kind in ('import','index')}}
