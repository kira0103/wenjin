"""Previewed, resumable cleanup of inactive system vector data only."""
import json,threading,time,logging
from pathlib import Path
from uuid import UUID
from fastapi import HTTPException
from . import repository as r,retrieval_settings
from .embedding_config import space,LEGACY_MODEL
logger=logging.getLogger(__name__)


def _directory(root):return Path(root)/'private/index-cleanup/previews'

def preview(root,scope):
    with r.database.transaction(root) as db:
        embedding=retrieval_settings.read(root,db)['options']['embedding'];identity=space(embedding)
        legacy=embedding['provider']=='local' and embedding['model']==LEGACY_MODEL and not any(embedding[k] for k in ('document_prefix','query_prefix','revision'))
        items=r.rows(db,"WITH active AS MATERIALIZED (SELECT DISTINCT active_index id FROM kb_editions WHERE active_index IS NOT NULL) SELECT i.id,v.edition_id,COALESCE(i.config->'embedding'->>'model',split_part(i.model_key,'@',1)) model,COALESCE((i.config->>'dimensions')::int,(i.config->'embedding'->>'dimensions')::int,384) dimensions,a.id IS NOT NULL active,COALESCE(i.config->>'embedding_space'=:space,:legacy) current FROM kb_indexes i JOIN kb_revisions v ON v.id=i.revision_id JOIN kb_editions e ON e.id=v.edition_id LEFT JOIN active a ON a.id=i.id WHERE e.owner_role IS NULL AND i.state='ready' AND NULLIF(i.model_key,'') IS NOT NULL",space=identity,legacy=legacy)
        relevant=[item for item in items if scope=='all_inactive' or not item['current']]
        references={str(row['id']) for row in r.rows(db,"SELECT payload->>'index_id' id FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running') AND payload ? 'index_id'")}
        eligible=[item for item in relevant if not item['active'] and str(item['id']) not in references]
        counts=r.rows(db,'SELECT index_id,count(*) chunks FROM kb_chunks WHERE index_id=ANY(CAST(:ids AS uuid[])) GROUP BY index_id',ids=[str(item['id']) for item in eligible]) if eligible else []
        count_by_id={str(row['index_id']):row['chunks'] for row in counts};models={}
        for item in relevant:
            key=(item['model'],item['dimensions']);group=models.setdefault(key,{'model':key[0],'dimensions':key[1],'eligible_indexes':0,'eligible_chunks':0,'protected_indexes':0,'estimated_vector_bytes':0})
            if item['active'] or str(item['id']) in references:group['protected_indexes']+=1
            else:
                count=count_by_id.get(str(item['id']),0);group['eligible_indexes']+=1;group['eligible_chunks']+=count;group['estimated_vector_bytes']+=count*item['dimensions']*4
    identifier=r.uid();manifest={'created_at':time.time(),'space':identity,'scope':scope,'model':embedding['model'],'targets':[{'id':str(item['id']),'chunks':count_by_id.get(str(item['id']),0)} for item in eligible]}
    from backend.portal_database import save_private_json
    save_private_json(_directory(root)/(identifier+'.json'),manifest)
    return {'preview_id':identifier,'scope':scope,'model':embedding['model'],'eligible_indexes':len(eligible),'eligible_chunks':sum(count_by_id.values()),'estimated_vector_bytes':sum(g['estimated_vector_bytes'] for g in models.values()),'protected_indexes':len(relevant)-len(eligible),'models':list(models.values()),'notice':'仅清理预览中的未使用向量分块，保留索引元信息、原件、录文和引用。释放的数据库空间可复用，文件大小未必立即缩小。'}


def submit(root,preview_id):
    path=_directory(root)/(str(UUID(str(preview_id)))+'.json')
    try:manifest=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise HTTPException(404,'清理预览不存在，请重新预览') from None
    if time.time()-manifest['created_at']>900:raise HTTPException(409,'清理预览已过期，请重新预览')
    if not manifest['targets']:raise HTTPException(409,'没有符合条件的历史索引')
    with r.database.transaction(root) as db:
        current=retrieval_settings.read(root,db)['options']['embedding']
        if space(current)!=manifest['space']:raise HTTPException(409,'模型配置已改变，请重新预览清理范围')
        r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||'index-cleanup',0))")
        existing=r.rows(db,"SELECT id,state,progress,result,error,payload->>'preview_id' preview_id FROM kb_jobs WHERE kind='index_cleanup' AND workspace_key=current_setting('shixue.workspace') AND (state='cleaning' OR payload->>'preview_id'=:preview) ORDER BY created_at DESC LIMIT 1",preview=str(preview_id))
        if existing:
            if existing[0]['state']=='cleaning' and existing[0]['preview_id']!=str(preview_id):raise HTTPException(409,'已有历史索引清理任务在执行，请等待完成')
            return existing[0]
        job=r.queue(db,'index_cleanup',{**manifest,'preview_id':str(preview_id)})
        r.execute(db,"UPDATE kb_jobs SET state='cleaning',stage='cleaning',result=CAST(:result AS jsonb) WHERE id=:id",id=job['id'],result=r.j({'cursor':0,'deleted_chunks':0,'cleaned_indexes':0,'skipped_indexes':0,'total_indexes':len(manifest['targets'])}))
        result={'id':str(job['id']),'state':'cleaning','progress':0,'result':{'total_indexes':len(manifest['targets'])},'error':''}
    runner.start(root);return result


def status(root,identifier):
    with r.database.transaction(root) as db:return r.one(db,"SELECT id,state,stage,progress,result,error,updated_at FROM kb_jobs WHERE id=:id AND kind='index_cleanup' AND workspace_key=current_setting('shixue.workspace')",id=identifier)


def process_slice(root,identifier,owner):
    with r.database.transaction(root) as db:
        job=r.one(db,"SELECT * FROM kb_jobs WHERE id=:id AND kind='index_cleanup' AND workspace_key=current_setting('shixue.workspace') FOR UPDATE",id=identifier)
        if job['state']!='cleaning' or job['owner']!=owner:return False
        payload=job['payload'];result=job['result'];cursor=result['cursor'];targets=payload['targets']
        if space(retrieval_settings.read(root,db)['options']['embedding'])!=payload['space']:
            r.execute(db,"UPDATE kb_jobs SET state='cancelled',error='模型配置已改变，剩余清理已停止，请重新预览',owner=NULL,lease_until=NULL,updated_at=now() WHERE id=:id",id=identifier);return False
        target=targets[cursor];index_id=target['id']
        source=r.rows(db,"SELECT e.id FROM kb_indexes i JOIN kb_revisions v ON v.id=i.revision_id JOIN kb_editions e ON e.id=v.edition_id WHERE i.id=:id AND e.owner_role IS NULL FOR UPDATE OF e SKIP LOCKED",id=index_id)
        protected=not source
        if not protected:
            protected=bool(r.execute(db,'SELECT 1 FROM kb_editions WHERE active_index=:id LIMIT 1',id=index_id).scalar())
            protected=protected or bool(r.execute(db,"SELECT 1 FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running') AND payload->>'index_id'=:id LIMIT 1",id=index_id).scalar())
        if protected:result['skipped_indexes']+=1;cursor+=1
        else:
            index=r.one(db,'SELECT id,state,model_key FROM kb_indexes WHERE id=:id FOR UPDATE',id=index_id)
            if index['state']!='ready' or not index['model_key']:result['skipped_indexes']+=1;cursor+=1
            else:
                removed=r.execute(db,'DELETE FROM kb_chunks WHERE id IN (SELECT id FROM kb_chunks WHERE index_id=:id LIMIT 500)',id=index_id).rowcount
                result['deleted_chunks']+=removed
                result['current_index_deleted']=result.get('current_index_deleted',0)+removed
                if not r.execute(db,'SELECT 1 FROM kb_chunks WHERE index_id=:id LIMIT 1',id=index_id).scalar():
                    r.execute(db,"UPDATE kb_indexes SET state='purged' WHERE id=:id",id=index_id);result['cleaned_indexes']+=1;cursor+=1
        if cursor!=result['cursor']:result['current_index_deleted']=0
        result['cursor']=cursor;done=cursor>=len(targets)
        fraction=min(.99,result.get('current_index_deleted',0)/max(1,target.get('chunks',0))) if not done else 0
        r.execute(db,"UPDATE kb_jobs SET state=:state,stage=:state,progress=:progress,result=CAST(:result AS jsonb),lease_until=now()+interval '30 seconds',updated_at=now() WHERE id=:id AND owner=:owner",id=identifier,owner=owner,state='succeeded' if done else 'cleaning',progress=100 if done else int(100*(cursor+fraction)/len(targets)),result=r.j(result))
        return not done


class Runner:
    def __init__(self):self.lock=threading.RLock();self.thread=None;self.stop_event=threading.Event();self.owner=r.uid()
    def start(self,root):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.stop_event.clear();self.thread=threading.Thread(target=self.loop,args=(root,),daemon=True,name='index-cleanup');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=6)
    def loop(self,root):
        while not self.stop_event.is_set():
            identifier=None
            try:
                with r.database.transaction(root) as db:
                    jobs=r.rows(db,"UPDATE kb_jobs SET owner=:owner,lease_until=now()+interval '30 seconds' WHERE id=(SELECT id FROM kb_jobs WHERE kind='index_cleanup' AND workspace_key=current_setting('shixue.workspace') AND state='cleaning' AND (owner=:owner OR lease_until IS NULL OR lease_until<now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id",owner=self.owner)
                if jobs:identifier=jobs[0]['id'];process_slice(root,identifier,self.owner)
                else:self.stop_event.wait(1)
            except Exception as error:
                logger.error('index_cleanup=%s type=%s sqlstate=%s',identifier,type(error).__name__,getattr(getattr(error,'orig',None),'sqlstate',None))
                # Driver errors can contain private payloads; show a bounded error.
                if identifier:
                    try:
                        with r.database.transaction(root) as db:r.execute(db,"UPDATE kb_jobs SET state='failed',error='历史索引清理未完成，已完成部分保留；请重新预览后重试',owner=NULL,lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner AND state='cleaning'",id=identifier,owner=self.owner)
                    except Exception:pass
                self.stop_event.wait(1)

runner=Runner()
