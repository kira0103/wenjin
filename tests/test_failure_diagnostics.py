"""Failure review must not guess old causes or retry with today's defaults."""
import json,os,unittest
from unittest.mock import patch
from fastapi import HTTPException
from backend.knowledge import failure_diagnostics as d,repository as r,bulk_processing as bulk

class Diagnostics(unittest.TestCase):
    def test_legacy_unknown_and_recorded_empty_are_distinct(self):
        self.assertEqual(d.legacy(d.GENERIC,'embedding')['category'],'unknown')
        self.assertFalse(d.legacy(d.GENERIC)['recorded'])
        self.assertEqual(d.legacy(d.EMPTY)['category'],'empty_text')
        self.assertEqual(d.capture(HTTPException(400,d.EMPTY))['category'],'empty_text')

    def test_capture_is_bounded_and_does_not_expose_exception_content(self):
        secret='PRIVATE-BODY-AND-API-KEY'
        try:raise ValueError(secret)
        except ValueError as error:
            with self.assertLogs('wenjin.ingestion') as logs:value=d.capture(error,'embedding',2)
        output=json.dumps(value)+str(logs.output)
        self.assertNotIn(secret,output);self.assertTrue(value['diagnostic_id']);self.assertEqual(value['stage'],'embedding')
        self.assertEqual(value['category'],'unknown');self.assertEqual(value['exception_types'],['ValueError'])
        self.assertTrue(value['frames']);self.assertNotIn('E:',output)
        self.assertEqual(d.capture(MemoryError(secret))['category'],'resource')
        self.assertEqual(d.capture(FileNotFoundError(secret))['category'],'file')

    def test_sqlstate_and_frozen_config_are_safe(self):
        class DbError(Exception):sqlstate='53100'
        value=d.capture(DbError('INSERT SECRET SQL PARAMS'))
        self.assertEqual(value['sqlstate'],'53100');self.assertNotIn('SECRET',json.dumps(value))
        config=d.public_config({'embedding':{'model':'original','dimensions':384,'api_key':'SECRET'},'config':{'mode':'parent_child','size':300,'cleaning':{'remove_urls':True}},'text':'SECRET'})
        self.assertNotIn('SECRET',json.dumps(config));self.assertEqual(config['chunking']['mode'],'parent_child')
        redacted=d.redact('token=SECRET https://name:SECRET@example.org E:\\private\\SECRET')
        self.assertNotIn('SECRET',redacted)
        self.assertNotIn('SECRET',d.redact('{"api_key": "SECRET WITH SPACE", "password":"SECRET"}'))

@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class FailureNative(unittest.TestCase):
    from tests.test_bulk_processing import BulkNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    checked=_Fixture.checked
    document=_Fixture.document
    save=_Fixture.save
    seed=_Fixture.seed
    own=_Fixture.own

    def failed(self,prefix,count=1,error=d.GENERIC,batch='fixture-batch'):
        items=self.seed(prefix,count)
        with r.database.transaction(self.root) as db:
            for item in items:
                payload={'edition_id':item['id'],'revision_id':item['revision_id'],'embedding':{'provider':'local','model':'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','dimensions':384,'batch_size':16},'config':{'mode':'recursive','size':300,'overlap':30},'processing_batch':batch}
                job=r.queue(db,'index',payload);item['job_id']=str(job['id'])
                r.execute(db,"UPDATE kb_jobs SET state='failed',stage='embedding',error=:error WHERE id=:id",id=job['id'],error=error)
        return items

    def test_full_facets_batch_filter_details_export_and_privacy(self):
        self.failed('诊断统计跨页',61)
        empty=self.failed('诊断统计跨页空录文',2,d.EMPTY,'other-batch')
        listing=self.checked(self.client.get('/api/knowledge/rag-failures?query=诊断统计跨页&limit=20'))
        self.assertEqual(listing['total'],63);self.assertEqual(len(listing['items']),20)
        self.assertEqual({v['category']:v['total'] for v in listing['categories']},{'unknown':61,'empty_text':2})
        self.assertEqual(listing['counts']['failed'],63)
        filtered=self.checked(self.client.get('/api/knowledge/rag-failures?query=诊断统计跨页&batch=other-batch'))
        self.assertEqual(filtered['total'],2)
        detail=self.checked(self.client.get('/api/knowledge/rag-failures/'+empty[0]['id']))
        self.assertEqual(detail['stage'],'embedding');self.assertEqual(detail['config']['embedding']['dimensions'],384)
        self.assertEqual(detail['files'][0]['filename'],'批量原文.txt')
        export=self.checked(self.client.post('/api/knowledge/rag-failures/export',json={'query':'诊断统计跨页'}))
        content=json.loads(export['content']);self.assertEqual(content['total'],63)
        self.assertNotIn('财政制度原文',export['content'])
        with patch.dict(os.environ,{'SHIXUE_PRIVATE_SCHEMA':'w_'+'a'*32}):
            self.assertEqual(self.client.get('/api/knowledge/rag-failures').status_code,403)
            self.assertEqual(self.client.post('/api/knowledge/rag-failures/export',json={}).status_code,403)

    def test_cross_page_preview_original_config_retry_and_idempotence(self):
        items=self.failed('诊断冻结配置',62)
        preview=self.checked(self.client.post('/api/knowledge/rag-failures/retry-preview',json={'query':'诊断冻结配置','excluded':[items[-1]['id']]}))
        self.assertEqual(preview['total'],61);self.assertEqual(preview['groups'][0]['config']['embedding']['dimensions'],384)
        self.assertEqual(len(preview['groups']),1,'录文修订不能把相同策略拆成多组')
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.execute(db,"SELECT count(*) FROM kb_jobs WHERE state='queued' AND payload->>'edition_id'=ANY(:ids)",ids=[i['id'] for i in items]).scalar(),0)
        from backend.knowledge.embedding_config import EmbeddingOptions
        self.save(embedding=EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json'))
        with patch.object(bulk.dispatcher,'start'):
            result=self.checked(self.client.post('/api/knowledge/rag-failures/retry',json={'preview_id':preview['preview_id'],'confirm':True}))
            self.assertEqual(self.client.post('/api/knowledge/rag-failures/retry',json={'preview_id':preview['preview_id'],'confirm':True}).status_code,409)
        owner=self.own(result['batch_id']);self.assertTrue(bulk.dispatch_slice(self.root,result['batch_id'],owner));self.assertFalse(bulk.dispatch_slice(self.root,result['batch_id'],owner))
        with r.database.transaction(self.root) as db:
            queued=r.rows(db,"SELECT * FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=result['batch_id'])
            self.assertEqual(len(queued),61);self.assertTrue(all(j['payload']['embedding']['dimensions']==384 for j in queued))
            self.assertTrue(all(j['payload']['config']['mode']=='recursive' for j in queued))
            self.assertNotIn(items[-1]['id'],[j['payload']['edition_id'] for j in queued])
            first=queued[0];r.execute(db,"UPDATE kb_jobs SET state='succeeded',result=jsonb_build_object('diagnostic_history',result->'diagnostic_history') WHERE id=:id",id=first['id'])
        active=self.checked(self.client.get('/api/knowledge/rag-failures?query=诊断冻结配置&view=processing'))
        self.assertEqual(active['total'],60)
        old_batch=self.checked(self.client.get('/api/knowledge/rag-failures?query=诊断冻结配置&batch=fixture-batch'))
        self.assertEqual(old_batch['total'],1,'旧批失败任务已被后续任务替代时不能继续算当前失败')
        recovered=self.checked(self.client.get('/api/knowledge/rag-failures?query=诊断冻结配置&view=recovered'))
        self.assertEqual(recovered['total'],1)
        detail=self.checked(self.client.get('/api/knowledge/rag-failures/'+first['payload']['edition_id']))
        self.assertEqual(len(detail['history']),2);self.assertTrue(detail['history'][0]['diagnostics'])
        self.assertEqual(self.client.post('/api/knowledge/jobs/'+detail['history'][1]['job_id']+'/retry').status_code,409)

    def test_preview_skips_changed_revision_and_small_trial_is_exact(self):
        items=self.failed('诊断小批重试',8)
        preview=self.checked(self.client.post('/api/knowledge/rag-failures/retry-preview',json={'query':'诊断小批重试'}))
        with patch.object(bulk.dispatcher,'start'):
            result=self.checked(self.client.post('/api/knowledge/rag-failures/retry',json={'preview_id':preview['preview_id'],'confirm':True,'take':5}))
        self.assertEqual(result['total'],5);bulk.dispatch_slice(self.root,result['batch_id'],self.own(result['batch_id']))
        with r.database.transaction(self.root) as db:self.assertEqual(r.execute(db,"SELECT count(*) FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=result['batch_id']).scalar(),5)
        item=self.failed('诊断失效预览')[0]
        preview=self.checked(self.client.post('/api/knowledge/rag-failures/retry-preview',json={'ids':[item['id']]}))
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='queued' WHERE id=:id",id=item['job_id'])
        with patch.object(bulk.dispatcher,'start'):
            result=self.checked(self.client.post('/api/knowledge/rag-failures/retry',json={'preview_id':preview['preview_id'],'confirm':True}))
        bulk.dispatch_slice(self.root,result['batch_id'],self.own(result['batch_id']))
        with r.database.transaction(self.root) as db:self.assertEqual(r.one(db,'SELECT result FROM kb_jobs WHERE id=:id',id=result['batch_id'])['result']['skipped'],1)

    def test_worker_retains_progress_diagnostics_and_history_after_success(self):
        from backend.knowledge.worker import Worker
        item=self.failed('诊断工作器记录')[0]
        self.checked(self.client.post('/api/knowledge/jobs/'+item['job_id']+'/retry'))
        worker=Worker();worker.root=self.root
        def broken(root,payload,check,progress):
            progress('embedding',40,{'completed':32,'total':150,'unit':'chunks'})
            raise MemoryError('PRIVATE DOCUMENT BODY')
        with patch('backend.knowledge.worker.process_index',side_effect=broken):worker.run(dict(worker.claim()))
        detail=self.checked(self.client.get('/api/knowledge/rag-failures/'+item['id']))
        self.assertEqual(detail['diagnostic']['category'],'resource');self.assertEqual(detail['progress']['completed'],32)
        self.assertNotIn('PRIVATE DOCUMENT BODY',json.dumps(detail))
        self.checked(self.client.post('/api/knowledge/jobs/'+item['job_id']+'/retry'))
        with patch('backend.knowledge.worker.process_index',return_value={'index_id':'fixture'}):worker.run(dict(worker.claim()))
        detail=self.checked(self.client.get('/api/knowledge/rag-failures/'+item['id']))
        self.assertEqual(detail['state'],'succeeded');self.assertEqual(len(detail['history'][0]['diagnostics']),2)

    def test_missing_configuration_and_changed_revision_are_never_replaced_by_defaults(self):
        from backend.knowledge.ingestion import publish
        items=self.failed('诊断拒绝替换配置',2)
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET payload=payload-'config' WHERE id=:id",id=items[0]['job_id'])
        preview=self.checked(self.client.post('/api/knowledge/rag-failures/retry-preview',json={'query':'诊断拒绝替换配置'}))
        self.assertEqual(preview['total'],1);self.assertEqual(preview['skipped'],1)
        with r.database.transaction(self.root) as db:
            asset=r.execute(db,'SELECT asset_id FROM kb_edition_assets WHERE edition_id=:id LIMIT 1',id=items[1]['id']).scalar()
            publish(db,items[1]['id'],[{'asset_id':str(asset),'page_index':1,'body':'新的录文修订'}])
        self.assertEqual(self.client.post('/api/knowledge/rag-failures/retry-preview',json={'query':'诊断拒绝替换配置'}).status_code,409)
        with patch.object(bulk.dispatcher,'start'):
            result=self.checked(self.client.post('/api/knowledge/rag-failures/retry',json={'preview_id':preview['preview_id'],'confirm':True}))
        bulk.dispatch_slice(self.root,result['batch_id'],self.own(result['batch_id']))
        with r.database.transaction(self.root) as db:self.assertEqual(r.one(db,'SELECT result FROM kb_jobs WHERE id=:id',id=result['batch_id'])['result']['skipped'],1)

if __name__=='__main__':unittest.main()
