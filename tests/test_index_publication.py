"""Long background writes must keep cancellation responsive and publish atomically."""
import math,os,unittest
from unittest.mock import patch
from types import SimpleNamespace
from sqlalchemy import event
from fastapi import HTTPException
from backend.knowledge import repository as r,ingestion as ingestion
from backend.knowledge.schemas import ChunkConfig
from backend.knowledge.failure_diagnostics import capture

class DatabaseTimeoutDiagnostics(unittest.TestCase):
    def test_statement_lock_and_operator_cancellation_are_distinct(self):
        class DriverError(Exception):
            def __init__(self,state,message):self.sqlstate=state;self.diag=SimpleNamespace(message_primary=message)
        cases=[('57014','canceling statement due to statement timeout','DATABASE_STATEMENT_TIMEOUT','timeout'),('55P03','canceling statement due to lock timeout','DATABASE_LOCK_TIMEOUT','timeout'),('57014','canceling statement due to user request','DATABASE_QUERY_CANCELLED','database'),('57014','unknown SECRET body','DATABASE_57014','database')]
        for state,message,code,category in cases:
            value=capture(DriverError(state,message),'writing_index')
            self.assertEqual(value['code'],code);self.assertEqual(value['category'],category)
            self.assertNotIn('SECRET',str(value))

@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class IndexPublicationNative(unittest.TestCase):
    from tests.test_rag_pipeline import PipelineNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    document=_Fixture.document

    def source(self,label):
        edition,asset,revision=self.document(label,'财政赋税与地方治理。'*100)
        return {'edition_id':edition,'revision_id':revision,'config':ChunkConfig(mode='recursive',size=80,overlap=10).model_dump()},asset

    def counts(self,edition):
        with r.database.transaction(self.root) as db:
            return r.one(db,'SELECT e.active_revision,e.active_index,(SELECT count(*) FROM kb_indexes i JOIN kb_revisions v ON v.id=i.revision_id WHERE v.edition_id=e.id) indexes,(SELECT count(*) FROM kb_chunks c JOIN kb_blocks b ON b.id=c.block_id JOIN kb_revisions v ON v.id=b.revision_id WHERE v.edition_id=e.id) chunks FROM kb_editions e WHERE id=:id',id=edition)

    def vectors(self):return patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.]+[0.]*383 for _ in values],'publication-fixture'))

    def test_batched_write_uses_its_own_timeout_and_keeps_default_queries_short(self):
        payload,_=self.source('后台写入独立时限');observed=[];progress=[]
        engine=r.database.engine(self.root)
        def before(db,cursor,statement,parameters,context,many):
            if statement.startswith('INSERT INTO kb_chunks') and 'CAST(' in statement:
                observed.append((len(parameters) if many else 1,db.exec_driver_sql('SHOW statement_timeout').scalar(),db.exec_driver_sql('SHOW lock_timeout').scalar()))
                # Reproduce a database operation longer than the former 5s cap.
                if len(observed)==1:db.exec_driver_sql('SELECT pg_sleep(5.1)')
        event.listen(engine,'before_cursor_execute',before)
        try:
            with self.vectors(),patch.object(ingestion,'INDEX_WRITE_BATCH_SIZE',2):result=ingestion.process_index(self.root,payload,lambda:None,lambda *args:progress.append(args))
        finally:event.remove(engine,'before_cursor_execute',before)
        self.assertGreater(result['chunks'],2);self.assertEqual(len(observed),math.ceil(result['chunks']/2))
        self.assertTrue(all(size<=2 and timeout in ('10min','600s') and lock=='60s' for size,timeout,lock in observed),observed)
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.execute(db,'SHOW statement_timeout').scalar(),'5s');self.assertEqual(r.execute(db,'SHOW lock_timeout').scalar(),'3s')
        writing=[args[2] for args in progress if args[0]=='writing_index']
        self.assertEqual(writing[0]['completed'],0);self.assertEqual(writing[-1]['completed'],result['chunks'])
        self.assertTrue(all(v['embedded']==result['chunks'] for v in writing))
        self.assertEqual(str(self.counts(payload['edition_id'])['active_index']),result['index_id'])

    def test_partial_write_failure_rolls_back_every_new_row(self):
        payload,_=self.source('后台写入原子回退');before_state=self.counts(payload['edition_id']);calls=0
        engine=r.database.engine(self.root)
        def fail(db,cursor,statement,parameters,context,many):
            nonlocal calls
            if statement.startswith('INSERT INTO kb_chunks') and 'CAST(' in statement:
                calls+=1
                if calls==2:raise RuntimeError('simulated publication failure')
        event.listen(engine,'before_cursor_execute',fail)
        try:
            with self.vectors(),patch.object(ingestion,'INDEX_WRITE_BATCH_SIZE',2):
                with self.assertRaises(RuntimeError):ingestion.process_index(self.root,payload,lambda:None,lambda *args:None)
        finally:event.remove(engine,'before_cursor_execute',fail)
        self.assertEqual(calls,2);self.assertEqual(self.counts(payload['edition_id']),before_state)

    def test_task_can_be_cancelled_while_new_chunks_are_uncommitted(self):
        from backend.knowledge.worker import Worker,Cancelled
        payload,_=self.source('后台写入取消不阻塞');before_state=self.counts(payload['edition_id'])
        with r.database.transaction(self.root) as db:job=r.queue(db,'index',payload)
        worker=Worker();worker.root=self.root;claimed=dict(worker.claim());self.assertEqual(str(claimed['id']),str(job['id']))
        payload.update(_job_id=str(job['id']),_job_owner=claimed['owner']);cancelled=False
        def progress(stage,value,detail=None):
            nonlocal cancelled
            if stage=='writing_index' and detail['completed']>0 and not cancelled:
                # A separate connection must not wait behind the bulk writer's job lock.
                with r.database.transaction(self.root) as db:
                    self.assertEqual(r.execute(db,"SELECT count(*) FROM kb_indexes WHERE id=:id",id=before_state['active_index']).scalar(),1)
                    r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE id=:id",id=job['id'])
                cancelled=True
        def check():
            with r.database.transaction(self.root) as db:
                if r.execute(db,'SELECT state FROM kb_jobs WHERE id=:id',id=job['id']).scalar()=='cancelled':raise Cancelled()
        with self.vectors(),patch.object(ingestion,'INDEX_WRITE_BATCH_SIZE',2):
            with self.assertRaises(Cancelled):ingestion.process_index(self.root,payload,check,progress)
        self.assertTrue(cancelled);self.assertEqual(self.counts(payload['edition_id']),before_state)

    def test_concurrent_revision_change_wins_over_stale_publication(self):
        payload,asset=self.source('后台写入版本围栏');changed=None
        def progress(stage,value,detail=None):
            nonlocal changed
            if stage=='writing_index' and detail['completed']>0 and not changed:
                with r.database.transaction(self.root) as db:changed=ingestion.publish(db,payload['edition_id'],[{'asset_id':asset,'page_index':1,'body':'重新修订的正文'}])
        with self.vectors(),patch.object(ingestion,'INDEX_WRITE_BATCH_SIZE',2):
            with self.assertRaises(HTTPException):ingestion.process_index(self.root,payload,lambda:None,progress)
        self.assertIsNotNone(changed);state=self.counts(payload['edition_id']);self.assertEqual(str(state['active_revision']),changed['revision_id']);self.assertEqual(str(state['active_index']),changed['index_id'])

    def test_total_write_budget_is_bounded_and_preserves_previous_index(self):
        payload,_=self.source('后台写入总预算');before_state=self.counts(payload['edition_id'])
        with self.vectors(),patch.object(ingestion,'INDEX_WRITE_BUDGET_SECONDS',0):
            with self.assertRaises(TimeoutError):ingestion.process_index(self.root,payload,lambda:None,lambda *args:None)
        self.assertEqual(self.counts(payload['edition_id']),before_state)

if __name__=='__main__':unittest.main()
