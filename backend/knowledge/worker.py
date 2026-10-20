import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from . import repository as r
from .ingestion import process_import,process_index


class Cancelled(Exception):pass


class Worker:
    def __init__(self):
        self.stop_event=threading.Event();self.lock=threading.RLock();self.thread=None;self.root=None
        self.sync_callback=None
        self.research_model_settings=None
        self.research_completion=None

    def start(self,root):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.root=root;self.stop_event.clear()
            self.thread=threading.Thread(target=self.loop,daemon=True,name='knowledge-worker');self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)

    def claim(self,research_only=False):
        with r.database.transaction(self.root) as connection:
            r.execute(connection,"UPDATE kb_jobs SET state='failed',error='多次意外中断，请人工重试' WHERE workspace_key=current_setting('shixue.workspace') AND state='running' AND lease_until<now() AND attempts>=3")
            # A crashed worker's lease expires; explicit failures require user retry.
            return r.execute(connection,"UPDATE kb_jobs SET state='running',stage='starting',attempts=attempts+1,owner=:owner,"
                "lease_until=now()+interval '45 seconds',updated_at=now() WHERE id=(SELECT id FROM kb_jobs "
                "WHERE workspace_key=current_setting('shixue.workspace') AND kind<>'collaboration' "+("AND kind='research' " if research_only else '')+"AND (state='queued' OR (state='running' AND lease_until<now() AND attempts<3)) "
                "AND NOT EXISTS (SELECT 1 FROM kb_research_runs rr JOIN kb_collaboration_nodes n ON n.research_run_id=rr.id JOIN kb_collaboration_runs t ON t.id=n.collaboration_id "
                "WHERE rr.job_id=kb_jobs.id AND (SELECT count(*) FROM kb_collaboration_nodes sibling JOIN kb_research_runs sr ON sr.id=sibling.research_run_id "
                "JOIN kb_jobs sj ON sj.id=sr.job_id WHERE sibling.collaboration_id=t.id AND sj.state='running' AND sj.lease_until>now()) >= COALESCE((t.request->>'max_parallel')::integer,2)) "
                "ORDER BY CASE WHEN state='running' THEN 0 ELSE 1 END,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",owner=r.uid()).mappings().first()

    def loop(self):
        # Research tasks may overlap; all existing mutation jobs remain exclusive.
        pending=[]
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='research-worker') as pool:
            while not self.stop_event.is_set():
                try:
                    pending=[future for future in pending if not future.done()]
                    if self.sync_callback and self.sync_callback() is False:
                        self.stop_event.wait(1);continue
                    if self.research_model_settings:
                        from .collaboration import advance
                        advance(self.root,self.research_model_settings())
                    if len(pending)<2:
                        job=self.claim(research_only=bool(pending))
                        if job:
                            if job['kind']=='research':pending.append(pool.submit(self.run,dict(job)))
                            else:self.run(dict(job))
                            continue
                    self.stop_event.wait(.5)
                except Exception:self.stop_event.wait(1)

    def run(self,job):
        root=self.root
        finished=threading.Event()
        owner=job['owner'];identifier=job['id']
        job['payload']={**job['payload'],'_job_id':str(identifier),'_job_owner':owner}
        def check():
            if self.stop_event.is_set():raise Cancelled()
            with r.database.transaction(root) as connection:
                state=r.one(connection,'SELECT state,owner,lease_until>clock_timestamp() lease_valid FROM kb_jobs WHERE id=:id',id=identifier)
                parent=r.execute(connection,"SELECT j.state FROM kb_collaboration_nodes n JOIN kb_collaboration_runs t ON t.id=n.collaboration_id "
                                 "JOIN kb_jobs j ON j.id=t.job_id WHERE n.research_run_id=:run",run=job['payload'].get('run_id')).scalar() if job['kind']=='research' else None
            if state['state']!='running' or state['owner']!=owner or not state['lease_valid']:raise Cancelled()
            if parent and parent!='running':raise Cancelled()
        def progress(stage,value):
            with r.database.transaction(root) as connection:
                r.execute(connection,'UPDATE kb_jobs SET stage=:stage,progress=:p,updated_at=now() WHERE id=:id AND owner=:owner AND state=\'running\' AND lease_until>clock_timestamp()',
                          stage=stage,p=value,id=identifier,owner=owner)
        def heartbeat():
            while not finished.wait(8):
                try:
                    with r.database.transaction(root) as connection:
                        r.execute(connection,"UPDATE kb_jobs SET lease_until=now()+interval '45 seconds' WHERE id=:id AND owner=:owner AND state='running' AND lease_until>clock_timestamp()",id=identifier,owner=owner)
                except Exception:pass
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            if job['kind']=='import':result=process_import(self.root,job['payload'],check,progress)
            elif job['kind']=='document_batch':
                from .document_imports import process_batch
                result=process_batch(self.root,job['payload'],check,progress)
            elif job['kind']=='core_import':
                from .core_corpus import process_import as import_core
                result=import_core(self.root,job['payload'],check,progress)
            elif job['kind']=='index':result=process_index(self.root,job['payload'],check,progress)
            elif job['kind']=='evaluation':
                from .evaluation import process_evaluation
                result=process_evaluation(self.root,job['payload'],check,progress)
            elif job['kind']=='rerank_model':
                from .reranking import prepare
                result=prepare(self.root,check,progress)
            elif job['kind']=='research':
                from .research_assistant import process
                if not self.research_model_settings or not self.research_completion:raise HTTPException(503,'研究模型配置暂不可用')
                process(root,job['payload'],check,progress,self.research_completion,self.research_model_settings())
                return # Research publishes its answer and successful job atomically.
            elif job['kind']=='migration':
                from .legacy import migrate
                result=migrate(self.root,job['payload'],check,progress)
            elif job['kind']=='export':
                from .backup import export_bundle
                with r.database.transaction(self.root) as connection:
                    r.execute(connection,"UPDATE kb_jobs SET lease_until=now()+interval '10 minutes' WHERE id=:id AND owner=:owner",id=identifier,owner=owner)
                result=export_bundle(self.root,check,progress)
            else:raise HTTPException(400,'不支持的后台任务')
            check()
            with r.database.transaction(self.root) as connection:
                r.execute(connection,"UPDATE kb_jobs SET state='succeeded',stage='complete',progress=100,result=CAST(:r AS jsonb),error='',lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner AND state='running' AND lease_until>clock_timestamp()",id=identifier,owner=owner,r=r.j(result))
        except Cancelled:
            pass # Cancelled tasks stay cancelled; stopped workers recover after lease expiry.
        except Exception as error:
            message=error.detail if isinstance(error,HTTPException) else '处理失败，请检查原件、模型文件或数据库状态后重试'
            try:
                with r.database.transaction(self.root) as connection:
                    r.execute(connection,"UPDATE kb_jobs SET state='failed',error=:error,lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner AND state='running' AND lease_until>clock_timestamp()",id=identifier,owner=owner,error=str(message)[:500])
            except Exception:pass
        finally:finished.set();thread.join(timeout=1)


worker=Worker()
