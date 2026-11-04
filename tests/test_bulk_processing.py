"""Whole-library dispatch must be durable, scoped, and independent of pages."""
import os,unittest
from unittest.mock import patch
from backend.knowledge import repository as r
from backend.knowledge import bulk_processing as bulk


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class BulkNative(unittest.TestCase):
    from tests.test_rag_pipeline import PipelineNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    tearDown=_Fixture.tearDown
    checked=_Fixture.checked
    document=_Fixture.document
    save=_Fixture.save

    def setUp(self):
        from tests.test_rag_pipeline import PipelineNative
        PipelineNative.setUp(self)
        # Each case gets an independent dispatch state, just as queued/running
        # child jobs are reset by the common fixture.
        with r.database.transaction(self.root) as db:
            r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE kind='rag_batch' AND state='dispatching'")

    def seed(self,prefix,count):
        from backend.knowledge.ingestion import publish
        digest,key=r.store_file(self.root,'批量原文.txt',(prefix+' 原文').encode())
        with r.database.transaction(self.root) as db:
            asset=r.uid();r.execute(db,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:hash,:key,1)',id=asset,hash=digest,key=key)
            items=[]
            for index in range(count):
                edition=r.create_edition(db,{'title':f'{prefix} {index:04}','item_type':'article'})
                r.execute(db,"INSERT INTO kb_edition_assets(edition_id,asset_id,filename) VALUES(:edition,:asset,'批量原文.txt')",edition=edition['id'],asset=asset)
                revision=publish(db,edition['id'],[{'asset_id':asset,'page_index':1,'body':prefix+' 财政制度原文'}])
                items.append({'id':str(edition['id']),'revision_id':revision['revision_id']})
        return items

    def submitted(self,prefix):
        with patch.object(bulk.dispatcher,'start'):
            return self.checked(self.client.post('/api/knowledge/rag-process-all',json={'query':prefix,'skip_ready':True}))

    def own(self,identifier):
        owner=r.uid()
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET owner=:owner,lease_until=now()+interval '30 seconds' WHERE id=:id",id=identifier,owner=owner)
        return owner

    def test_monitor_separates_not_dispatched_skipped_and_cancelled(self):
        items=self.seed('监控状态夹具',3)
        batch=self.submitted('监控状态夹具')
        url='/api/knowledge/rag-documents?batch_id='+batch['id']
        waiting=self.checked(self.client.get(url))['processing_progress']['batch']
        self.assertEqual(waiting['pending_dispatch'],3)
        self.assertEqual(waiting['queued'],0)
        self.assertEqual(waiting['finished'],0)
        # A changed original is deliberately skipped during dispatch, not cancelled.
        with r.database.transaction(self.root) as db:
            r.execute(db,'UPDATE kb_editions SET active_revision=NULL WHERE id=:id',id=items[0]['id'])
        bulk.dispatch_slice(self.root,batch['id'],self.own(batch['id']))
        dispatched=self.checked(self.client.get(url))['processing_progress']['batch']
        self.assertEqual(dispatched['skipped'],1)
        self.assertEqual(dispatched['cancelled'],0)
        self.assertEqual(dispatched['queued'],2)
        self.assertEqual(dispatched['pending_dispatch'],0)
        bulk.control(self.root,batch['id'],'cancel')
        cancelled=self.checked(self.client.get(url))['processing_progress']['batch']
        self.assertEqual(cancelled['skipped'],1)
        self.assertEqual(cancelled['cancelled'],2)
        self.assertEqual(cancelled['finished'],3)

    def test_monitor_distinguishes_dispatch_failure_from_document_failure(self):
        self.seed('入队失败监控夹具',2)
        batch=self.submitted('入队失败监控夹具')
        with r.database.transaction(self.root) as db:
            r.execute(db,"UPDATE kb_jobs SET state='failed' WHERE id=:id",id=batch['id'])
        snapshot=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+batch['id']))['processing_progress']['batch']
        self.assertEqual(snapshot['dispatch_failed'],2)
        self.assertEqual(snapshot['failed'],0)
        self.assertEqual(snapshot['queued'],0)
        self.assertEqual(snapshot['finished'],2)

    def test_monitor_keeps_original_submission_time_after_dispatch(self):
        from datetime import datetime
        self.seed('批次时间夹具',2)
        batch=self.submitted('批次时间夹具')
        bulk.dispatch_slice(self.root,batch['id'],self.own(batch['id']))
        with r.database.transaction(self.root) as db:
            submitted=r.one(db,'SELECT created_at FROM kb_jobs WHERE id=:id',id=batch['id'])['created_at']
            # All targets now have child tasks created after the controller.
            # DISTINCT ON must not replace the true submission with child time.
            r.execute(db,"UPDATE kb_jobs SET created_at=:submitted+interval '2 minutes' WHERE payload->>'processing_batch'=:batch",submitted=submitted,batch=batch['id'])
        monitor=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+batch['id']))['processing_progress']
        self.assertEqual(datetime.fromisoformat(monitor['batch']['submitted_at']),submitted)

    def test_monitor_keeps_one_snapshot_while_worker_advances(self):
        items=self.seed('监控一致性夹具',1)
        result=self.checked(self.client.post('/api/knowledge/rag-process',json={'documents':items}))
        identifier=result['jobs'][0]['id']
        original=r.one;advanced=[]
        def advance(db,query,**parameters):
            value=original(db,query,**parameters)
            if query.endswith(' submitted') and not advanced:
                advanced.append(True)
                with r.database.transaction(self.root) as writer:
                    r.execute(writer,"UPDATE kb_jobs SET state='running',stage='embedding',lease_until=now()+interval '45 seconds' WHERE id=:id",id=identifier)
            return value
        with patch.object(r,'one',side_effect=advance):
            monitor=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+result['batch_id']))['processing_progress']
        self.assertTrue(advanced)
        self.assertEqual(monitor['batch']['queued'],1)
        self.assertEqual(monitor['batch']['running'],0)
        self.assertEqual(monitor['ingestion_queue']['queued'],1)
        self.assertEqual(monitor['ingestion_queue']['running'],0)
        self.assertEqual(monitor['active_jobs'],[])
        next_monitor=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+result['batch_id']))['processing_progress']
        self.assertEqual(next_monitor['batch']['running'],1)
        self.assertEqual(next_monitor['ingestion_queue']['running'],1)
        self.assertEqual(next_monitor['active_jobs'][0]['id'],identifier)

    def test_all_matching_documents_span_pages_and_keep_frozen_model(self):
        from backend.knowledge.ingestion import process_index
        from backend.knowledge.embedding_config import EmbeddingOptions
        items=self.seed('跨页整库夹具',125)
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.]+[0.]*383 for _ in values],'bulk-ready')):
            process_index(self.root,{'edition_id':items[0]['id'],'revision_id':items[0]['revision_id']},lambda:None,lambda *a:None)
        self.checked(self.client.post('/api/knowledge/rag-process',json={'documents':[items[1]]}))
        batch=self.submitted('跨页整库夹具');self.assertEqual(batch['total'],123)
        self.assertNotIn('targets',self.checked(self.client.get('/api/knowledge/jobs/'+batch['id']))['payload'])
        self.assertTrue(all('targets' not in j['payload'] for j in self.checked(self.client.get('/api/knowledge/jobs')) if j['kind']=='rag_batch'))
        listing=self.checked(self.client.get('/api/knowledge/rag-documents?query=跨页整库夹具&limit=100'))
        self.assertEqual(len(listing['items']),100);self.assertEqual(listing['total'],125)
        self.assertEqual(listing['processing_progress']['batch']['total'],123)
        with patch.object(bulk.dispatcher,'start'):
            self.assertEqual(self.client.post('/api/knowledge/rag-process-all',json={'query':'跨页整库夹具'}).status_code,409)
        self.save(embedding=EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json'))
        owner=self.own(batch['id'])
        self.assertTrue(bulk.dispatch_slice(self.root,batch['id'],owner))
        self.assertTrue(bulk.dispatch_slice(self.root,batch['id'],owner))
        self.assertFalse(bulk.dispatch_slice(self.root,batch['id'],owner))
        self.assertFalse(bulk.dispatch_slice(self.root,batch['id'],owner))
        with r.database.transaction(self.root) as db:
            jobs=r.rows(db,"SELECT payload FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=batch['id'])
            self.assertEqual(len(jobs),123);self.assertEqual(len({j['payload']['edition_id'] for j in jobs}),123)
            self.assertTrue(all(j['payload']['embedding']['dimensions']==384 for j in jobs))
        snapshot=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+batch['id']))['processing_progress']
        self.assertEqual(snapshot['batch']['total'],123);self.assertEqual(snapshot['batch']['queued'],123)

    def test_cancel_retry_and_atomic_cursor_do_not_touch_other_batches(self):
        items=self.seed('可取消全批夹具',3)
        other=self.seed('其他批次夹具',1)[0]
        unrelated=self.checked(self.client.post('/api/knowledge/rag-process',json={'documents':[other]}))['jobs'][0]['id']
        batch=self.submitted('可取消全批夹具');owner=self.own(batch['id']);original=r.queue;calls=0
        def interrupted(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('simulate interrupted dispatch')
            return original(*args,**kwargs)
        with patch.object(bulk.r,'queue',side_effect=interrupted):
            with self.assertRaises(RuntimeError):bulk.dispatch_slice(self.root,batch['id'],owner)
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.one(db,'SELECT result FROM kb_jobs WHERE id=:id',id=batch['id'])['result']['cursor'],0)
            self.assertEqual(r.execute(db,"SELECT count(*) FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=batch['id']).scalar_one(),0)
        bulk.dispatch_slice(self.root,batch['id'],owner)
        self.checked(self.client.post(f"/api/knowledge/rag-process-all/{batch['id']}/cancel"))
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.one(db,'SELECT state FROM kb_jobs WHERE id=:id',id=unrelated)['state'],'queued')
            self.assertTrue(all(j['state']=='cancelled' for j in r.rows(db,"SELECT state FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=batch['id'])))
        with patch.object(bulk.dispatcher,'start'):self.checked(self.client.post(f"/api/knowledge/rag-process-all/{batch['id']}/retry"))
        bulk.dispatch_slice(self.root,batch['id'],self.own(batch['id']))
        snapshot=self.checked(self.client.get('/api/knowledge/rag-documents?batch_id='+batch['id']))['processing_progress']['batch']
        self.assertEqual(snapshot['total'],3);self.assertEqual(snapshot['queued'],3)

    def test_cross_page_selection_exclusions_and_library_scope_ignore_filters(self):
        items=self.seed('跨页排除夹具',125)
        other=self.seed('库外筛选夹具',2)
        excluded=[items[0]['id'],items[100]['id']]
        with patch.object(bulk.dispatcher,'start'):
            batch=self.checked(self.client.post('/api/knowledge/rag-process-all',json={'query':'跨页排除夹具','exclude_ids':excluded}))
        self.assertEqual(batch['total'],123)
        owner=self.own(batch['id'])
        while bulk.dispatch_slice(self.root,batch['id'],owner):pass
        with r.database.transaction(self.root) as db:
            ids={j['payload']['edition_id'] for j in r.rows(db,"SELECT payload FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=batch['id'])}
        self.assertTrue(all(identifier not in ids for identifier in excluded))
        self.assertTrue(all(item['id'] not in ids for item in other))
        # Library scope bypasses title/status filters, retaining explicit exclusions.
        with patch.object(bulk.dispatcher,'start'):
            full=self.checked(self.client.post('/api/knowledge/rag-process-all',json={'scope':'library','query':'绝不匹配任何文献','state':'ready','exclude_ids':[items[0]['id']]}))
        self.assertGreaterEqual(full['total'],3)
        with r.database.transaction(self.root) as db:
            job=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=full['id'])
        full_ids={str(item['id']) for item in job['payload']['targets']}
        self.assertEqual(full['total'],len(full_ids))
        self.assertTrue({items[100]['id'],*[item['id'] for item in other]}.issubset(full_ids))
        self.assertNotIn(items[0]['id'],full_ids);self.assertTrue(full_ids.isdisjoint(ids))
        self.assertEqual(job['payload']['scope'],'library')
        self.assertEqual(job['payload']['query'],'');self.assertEqual(job['payload']['state'],'all')

    def test_large_manifest_and_deep_page_remain_readable(self):
        # Match the real 15,000-document queue, including a large TOASTed
        # manifest. Both progress and queue-position queries must stay bounded.
        from backend.knowledge import retrieval_settings
        import uuid
        identifiers=[str(uuid.uuid4()) for _ in range(15000)]
        with r.database.transaction(self.root) as db:
            template=r.create_edition(db,{'title':'全量进度性能夹具','item_type':'article'})
            r.execute(db,"INSERT INTO kb_editions(id,work_id,label) SELECT CAST(value AS uuid),:work,'性能版本' FROM jsonb_array_elements_text(CAST(:ids AS jsonb))",work=template['work_id'],ids=r.j(identifiers))
            options=retrieval_settings.read(self.root,db)['options']
            targets=[{'id':identifier,'revision_id':None,'processing_config':options['ingestion'],'processing_version':0} for identifier in identifiers]
            controller=r.queue(db,'rag_batch',{'embedding':options['embedding'],'targets':targets})
            r.execute(db,"UPDATE kb_jobs SET state='succeeded',result=CAST(:result AS jsonb) WHERE id=:id",id=controller['id'],result=r.j({'cursor':15000,'scheduled':15000,'total':15000}))
            r.execute(db,"INSERT INTO kb_jobs(id,kind,state,stage,workspace_key,payload) SELECT gen_random_uuid(),'index','queued','queued',current_setting('shixue.workspace'),jsonb_build_object('edition_id',value,'processing_batch',CAST(:batch AS text),'embedding',CAST(:embedding AS jsonb)) FROM jsonb_array_elements_text(CAST(:ids AS jsonb))",batch=str(controller['id']),embedding=r.j(options['embedding']),ids=r.j(identifiers))
        # Load realistic planner statistics after the synthetic bulk insert.
        with self.product.manager.connect() as maintenance:
            maintenance.execute('ANALYZE kb_works; ANALYZE kb_editions; ANALYZE kb_jobs')
        listing=self.checked(self.client.get('/api/knowledge/rag-documents?limit=100&offset=14000&query=全量进度性能夹具&batch_id='+str(controller['id'])))
        self.assertEqual(len(listing['items']),100)
        self.assertEqual(listing['total'],15001)
        self.assertEqual(listing['processing_progress']['batch']['total'],15000)
        self.assertEqual(listing['processing_progress']['batch']['queued'],15000)
        self.assertTrue(all(item['job']['queue_position']>0 for item in listing['items'] if item['job']))

    def test_performance_upgrade_preserves_completed_indexes_and_explicit_strategies(self):
        from backend.knowledge.processing_upgrade import optimize_pending
        from backend.knowledge.schemas import ChunkConfig,IngestionOptions
        items=self.seed('性能升级夹具',3)
        jobs=[]
        for item,mode in zip(items,['auto','semantic','fixed']):
            jobs.append(self.checked(self.client.post('/api/knowledge/rag-process',json={'documents':[item],'processing':IngestionOptions(chunking=ChunkConfig(mode=mode)).model_dump()}))['jobs'][0]['id'])
        with r.database.transaction(self.root) as db:
            before=r.rows(db,'SELECT id,active_index,active_revision FROM kb_editions WHERE id=ANY(CAST(:ids AS uuid[]))',ids=[item['id'] for item in items])
            r.execute(db,"UPDATE kb_jobs SET state='running',stage='embedding',progress=40,attempts=1,owner=:owner,lease_until=now()+interval '45 seconds' WHERE id=:id",id=jobs[0],owner=r.uid())
        result=optimize_pending(self.root);self.assertEqual(result['running_requeued'],1)
        with r.database.transaction(self.root) as db:
            after=r.rows(db,'SELECT id,active_index,active_revision FROM kb_editions WHERE id=ANY(CAST(:ids AS uuid[]))',ids=[item['id'] for item in items])
            self.assertEqual({str(row['id']):row for row in before},{str(row['id']):row for row in after})
            current=[r.one(db,'SELECT * FROM kb_jobs WHERE id=:id',id=identifier) for identifier in jobs]
            self.assertEqual(current[0]['state'],'queued');self.assertIsNone(current[0]['owner']);self.assertEqual(current[0]['progress'],0)
            self.assertFalse(current[0]['payload']['config']['auto_semantic'])
            self.assertEqual([row['payload']['config']['mode'] for row in current],['auto','semantic','fixed'])
            self.assertNotIn('performance_update',current[1]['payload']);self.assertNotIn('performance_update',current[2]['payload'])
        self.assertEqual(optimize_pending(self.root),{'queued_auto_updated':0,'running_requeued':0,'interrupted_retried':0})
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='running',owner=:owner,lease_until=now()+interval '45 seconds' WHERE id=:id",id=jobs[0],owner=r.uid())
        self.assertEqual(optimize_pending(self.root),{'queued_auto_updated':0,'running_requeued':0,'interrupted_retried':0})
