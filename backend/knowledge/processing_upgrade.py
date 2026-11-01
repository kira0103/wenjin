"""Explicit, audited upgrade of unfinished work; completed indexes stay untouched."""
import time
from . import repository as r

VERSION='gpu-bulk-performance-v1'

def optimize_pending(root):
    with r.database.transaction(root) as db:
        r.execute(db,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace')||'processing-performance-upgrade',0))")
        updated=0
        for kind,path in [('index','{config,auto_semantic}'),('import','{processing,chunking,auto_semantic}')]:
            mode="payload->'config'" if kind=='index' else "payload->'processing'->'chunking'"
            result=r.execute(db,"UPDATE kb_jobs SET payload=jsonb_set(payload,CAST(:path AS text[]),'false'::jsonb)||jsonb_build_object('performance_update',CAST(:audit AS jsonb)),updated_at=now() "
                "WHERE workspace_key=current_setting('shixue.workspace') AND kind=:kind AND state='queued' AND ("+mode+")->>'mode'='auto' AND COALESCE(("+mode+")->>'auto_semantic','false')<>'true' AND COALESCE(payload->'performance_update'->>'version','')<>:version",
                path=path,kind=kind,version=VERSION,audit=r.j({'version':VERSION,'updated_at':time.time(),'change':'auto_semantic=false'}))
            updated+=result.rowcount
        active=r.rows(db,"SELECT id,kind,state,payload,owner,attempts FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('index','import') AND (state='running' OR (state='failed' AND error='多次意外中断，请人工重试')) AND COALESCE(payload->'performance_update'->>'version','')<>:version FOR UPDATE",version=VERSION)
        restarted=0;retried=0
        for job in active:
            payload=job['payload'];config=payload.get('config',{}) if job['kind']=='index' else payload.get('processing',{}).get('chunking',{})
            if config.get('mode')=='auto' and not config.get('auto_semantic'):config['auto_semantic']=False
            payload['performance_update']={'version':VERSION,'updated_at':time.time(),'previous_state':job['state'],'change':'restart_with_accelerated_runtime'}
            r.execute(db,"UPDATE kb_jobs SET state='queued',stage='queued',progress=0,result='{}',error='',owner=NULL,lease_until=NULL,attempts=:attempts,payload=CAST(:payload AS jsonb),updated_at=now() WHERE id=:id",
                id=job['id'],attempts=0 if job['state']=='failed' else max(0,job['attempts']-1),payload=r.j(payload))
            if job['state']=='running':restarted+=1
            else:retried+=1
        return {'queued_auto_updated':updated,'running_requeued':restarted,'interrupted_retried':retried}
