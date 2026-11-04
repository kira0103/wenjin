"""Global ingestion cancellation and preview-bound history cleanup."""
import os,unittest,json
from unittest.mock import patch
from fastapi import HTTPException
from backend.knowledge import repository as r,index_cleanup
from backend.knowledge.embedding_config import EmbeddingOptions,space
from backend.knowledge.schemas import ChunkConfig

@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立 PostgreSQL')
class MaintenanceNative(unittest.TestCase):
    from tests.test_bulk_processing import BulkNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    checked=_Fixture.checked
    document=_Fixture.document
    save=_Fixture.save
    seed=_Fixture.seed

    def generations(self):
        from backend.knowledge.ingestion import process_index
        from backend.knowledge.models import configuration
        edition,asset,revision=self.document('历史索引清理样本','财政制度。'*10000)
        configs=[EmbeddingOptions().model_dump(mode='json'),EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json')]
        def vectors(root,texts):
            config=configuration(root)[0]
            return [[1.]+[0.]*(config['dimensions']-1) for _ in texts],'fixture:'+space(config)
        indexes=[]
        with patch('backend.knowledge.ingestion.embed',side_effect=vectors):
            for embedding in configs:
                indexes.append(process_index(self.root,{'edition_id':edition,'revision_id':revision,'embedding':embedding,'config':ChunkConfig(mode='fixed',profile='general',size=80,overlap=0).model_dump()},lambda:None,lambda *a:None)['index_id'])
        self.save(embedding=configs[1])
        return edition,revision,indexes,configs

    def queued_cleanup(self,preview):
        with patch.object(index_cleanup.runner,'start'):
            return self.checked(self.client.post('/api/knowledge/rag-index-cleanup',json={'preview_id':preview['preview_id'],'confirm':True}))

    def own(self,identifier):
        owner=r.uid()
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET owner=:owner,lease_until=now()+interval '30 seconds' WHERE id=:id",id=identifier,owner=owner)
        return owner

    def test_global_cancel_covers_manual_legacy_and_undispatched_batches(self):
        from backend.knowledge import bulk_processing
        from backend.knowledge.ingestion import job_gate
        items=self.seed('全局取消夹具',3)
        manual=self.checked(self.client.post('/api/knowledge/rag-process',json={'documents':[items[0],items[1]]}))
        owner=r.uid()
        with r.database.transaction(self.root) as db:
            r.execute(db,"UPDATE kb_jobs SET state='running',owner=:owner,lease_until=now()+interval '45 seconds' WHERE id=:id",id=manual['jobs'][0]['id'],owner=owner)
            r.execute(db,"UPDATE kb_jobs SET payload=payload-'processing_batch' WHERE id=:id",id=manual['jobs'][1]['id'])
            other=r.queue(db,'embedding_model',{})
            complete=r.queue(db,'index',{'edition_id':items[0]['id']});r.execute(db,"UPDATE kb_jobs SET state='succeeded' WHERE id=:id",id=complete['id'])
        with patch.object(bulk_processing.dispatcher,'start'):
            batch=self.checked(self.client.post('/api/knowledge/rag-process-all',json={'query':'全局取消夹具'}))
        result=self.checked(self.client.post('/api/knowledge/rag-cancel-all',json={}))
        self.assertEqual(result['cancelled_jobs'],2);self.assertEqual(result['cancelled_dispatches'],1)
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.one(db,'SELECT state FROM kb_jobs WHERE id=:id',id=batch['id'])['state'],'cancelled')
            self.assertEqual(r.one(db,'SELECT state FROM kb_jobs WHERE id=:id',id=other['id'])['state'],'queued')
            self.assertEqual(r.one(db,'SELECT state FROM kb_jobs WHERE id=:id',id=complete['id'])['state'],'succeeded')
            with self.assertRaises(HTTPException):job_gate(db,{'_job_id':manual['jobs'][0]['id'],'_job_owner':owner})
        self.assertEqual(self.checked(self.client.post('/api/knowledge/rag-cancel-all',json={}))['cancelled_jobs'],0)

    def test_preview_cleanup_is_chunked_resumable_and_keeps_sources_and_active_index(self):
        edition,revision,indexes,_configs=self.generations();old,current=indexes
        with r.database.transaction(self.root) as db:
            block=r.one(db,'SELECT id FROM kb_blocks WHERE revision_id=:id ORDER BY ordinal LIMIT 1',id=revision)
            anchor=r.anchor(db,block['id'],0,2)
            before=r.execute(db,'SELECT count(*) FROM kb_chunks WHERE index_id=:id',id=old).scalar_one()
        self.assertGreater(before,500)
        preview=self.checked(self.client.get('/api/knowledge/rag-index-history?scope=other_models'))
        self.assertGreaterEqual(preview['eligible_chunks'],before)
        self.assertEqual(self.client.post('/api/knowledge/rag-index-cleanup',json={'preview_id':preview['preview_id'],'confirm':False}).status_code,422)
        job=self.queued_cleanup(preview);owner=self.own(job['id'])
        self.assertEqual(self.queued_cleanup(preview)['id'],job['id'])
        self.assertTrue(index_cleanup.process_slice(self.root,job['id'],owner))
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.execute(db,'SELECT count(*) FROM kb_chunks WHERE index_id=:id',id=old).scalar_one(),before-500)
        # A replacement API executor can resume the committed manifest and counters.
        owner=self.own(job['id'])
        while index_cleanup.process_slice(self.root,job['id'],owner):pass
        result=self.checked(self.client.get('/api/knowledge/rag-index-cleanup/'+job['id']))
        self.assertEqual(result['state'],'succeeded');self.assertEqual(result['result']['deleted_chunks'],before)
        with r.database.transaction(self.root) as db:
            self.assertEqual(str(r.one(db,'SELECT active_index FROM kb_editions WHERE id=:id',id=edition)['active_index']),current)
            self.assertEqual(r.one(db,'SELECT state FROM kb_indexes WHERE id=:id',id=old)['state'],'purged')
            self.assertGreater(r.execute(db,'SELECT count(*) FROM kb_chunks WHERE index_id=:id',id=current).scalar_one(),0)
            self.assertEqual(r.one(db,'SELECT quote FROM kb_anchors WHERE id=:id',id=anchor['id'])['quote'],anchor['quote'])
            self.assertGreater(r.execute(db,'SELECT count(*) FROM kb_blocks WHERE revision_id=:id',id=revision).scalar_one(),0)

    def test_active_index_recheck_and_model_change_protect_previewed_data(self):
        edition,_revision,indexes,configs=self.generations();old,current=indexes
        with r.database.transaction(self.root) as db:reference=r.queue(db,'evaluation',{'index_id':old})
        protected=self.checked(self.client.get('/api/knowledge/rag-index-history?scope=all_inactive'))
        self.assertGreaterEqual(protected['protected_indexes'],2)
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE id=:id",id=reference['id'])
        preview=self.checked(self.client.get('/api/knowledge/rag-index-history?scope=other_models'))
        self.save(embedding=configs[0])
        self.assertEqual(self.client.post('/api/knowledge/rag-index-cleanup',json={'preview_id':preview['preview_id'],'confirm':True}).status_code,409)
        self.save(embedding=configs[1]);job=self.queued_cleanup(preview)
        with r.database.transaction(self.root) as db:r.execute(db,'UPDATE kb_editions SET active_index=:idx WHERE id=:id',idx=old,id=edition)
        owner=self.own(job['id'])
        while index_cleanup.process_slice(self.root,job['id'],owner):pass
        result=index_cleanup.status(self.root,job['id'])
        self.assertGreaterEqual(result['result']['skipped_indexes'],1)
        with r.database.transaction(self.root) as db:
            self.assertGreater(r.execute(db,'SELECT count(*) FROM kb_chunks WHERE index_id=:id',id=old).scalar_one(),0)
            self.assertGreater(r.execute(db,'SELECT count(*) FROM kb_chunks WHERE index_id=:id',id=current).scalar_one(),0)
        with patch.dict(os.environ,{'SHIXUE_PRIVATE_SCHEMA':'w_'+'a'*32}):
            self.assertEqual(self.client.get('/api/knowledge/rag-index-history').status_code,403)
            self.assertEqual(self.client.post('/api/knowledge/rag-cancel-all',json={}).status_code,403)
