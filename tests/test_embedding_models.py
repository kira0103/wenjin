"""Configurable embeddings without real model downloads or third-party requests."""
import json,os,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from pydantic import ValidationError
from backend.knowledge.embedding_config import EmbeddingOptions,space
from backend.knowledge.embedding_service import validate_vectors


class EmbeddingRules(unittest.TestCase):
    def test_cache_inventory_is_read_only_and_requires_all_model_files(self):
        import tempfile
        from pathlib import Path
        from fastembed import TextEmbedding
        from backend.knowledge.embedding_config import PRESETS
        from backend.knowledge.embedding_cache import inventory
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache=root/'cache'
            with patch.dict(os.environ,{'SHIXUE_KB_MODEL_CACHE':str(cache)}),patch.object(TextEmbedding,'__init__',side_effect=AssertionError('Status must not load or download models')):
                before=inventory(root,PRESETS)
                self.assertTrue(all(v['state']=='missing' for v in before.values()));self.assertFalse(cache.exists())
                repo=cache/'models--Qdrant--bge-small-zh-v1.5';folder=repo/'snapshots'/('a'*40)
                folder.mkdir(parents=True);(repo/'refs').mkdir();(repo/'refs'/'main').write_text('a'*40)
                (folder/'model_optimized.onnx').write_bytes(b'weight-file')
                self.assertEqual(inventory(root,PRESETS)['BAAI/bge-small-zh-v1.5']['state'],'incomplete')
                (repo/'files_metadata.json').write_text('[]')
                self.assertEqual(inventory(root,PRESETS)['BAAI/bge-small-zh-v1.5']['state'],'unknown')
                (repo/'files_metadata.json').unlink()
                (folder/'tokenizer.json').write_text('{}');(folder/'tokenizer_config.json').write_text('{}')
                files={p:p.stat().st_mtime_ns for p in cache.rglob('*')}
                result=inventory(root,PRESETS)
                self.assertEqual(result['BAAI/bge-small-zh-v1.5']['state'],'available')
                self.assertEqual(result['BAAI/bge-base-en-v1.5']['state'],'missing')
                self.assertEqual({p:p.stat().st_mtime_ns for p in cache.rglob('*')},files)
                (repo/'files_metadata.json').write_text(json.dumps({'snapshots/'+('a'*40)+'/model_optimized.onnx':{'size':100}}))
                self.assertEqual(inventory(root,PRESETS)['BAAI/bge-small-zh-v1.5']['state'],'incomplete')

    def test_cache_inventory_checks_external_weight_files_and_legacy_layout(self):
        import tempfile
        from pathlib import Path
        from backend.knowledge.embedding_config import PRESETS
        from backend.knowledge.embedding_cache import inventory
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache=root/'models';folder=cache/'fast-multilingual-e5-large';folder.mkdir(parents=True)
            for name in ('model.onnx','tokenizer.json','tokenizer_config.json'):(folder/name).write_bytes(b'file')
            with patch.dict(os.environ,{'SHIXUE_KB_MODEL_CACHE':str(cache)}):
                self.assertEqual(inventory(root,PRESETS)['intfloat/multilingual-e5-large']['state'],'incomplete')
                (folder/'model.onnx_data').write_bytes(b'external-weights')
                self.assertEqual(inventory(root,PRESETS)['intfloat/multilingual-e5-large']['state'],'available')
    def test_fixed_and_adjustable_dimensions_are_validated(self):
        self.assertEqual(EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).dimensions,512)
        for values in ({'model':'BAAI/bge-small-zh-v1.5','dimensions':384},
            {'provider':'openai_compatible','model':'text-embedding-3-small','base_url':'https://api.openai.com/v1','dimensions':3072},
            {'provider':'openai_compatible','model':'text-embedding-v4','base_url':'http://localhost/v1','dimensions':1000},
            {'provider':'openai_compatible','model':'custom','base_url':'http://user:secret@localhost/v1'}):
            with self.assertRaises(ValidationError):EmbeddingOptions(**values)

    def test_credentials_and_transport_settings_do_not_change_vector_space(self):
        original=EmbeddingOptions().model_dump(mode='json')
        self.assertEqual(space(original),space({**original,'timeout_seconds':120,'batch_size':1,'credential_id':'00000000-0000-0000-0000-000000000001'}))
        self.assertNotEqual(space(original),space({**original,'query_prefix':'query: '}))
        self.assertNotEqual(space(original),space({**original,'revision':'updated-model'}))

    def test_invalid_vectors_are_rejected_instead_of_padded_or_truncated(self):
        for vector in ([1.]*31,[0.]*32,[float('nan')]*32,[True]*32):
            with self.assertRaises(RuntimeError):validate_vectors([vector],1,32)
        self.assertAlmostEqual(sum(x*x for x in validate_vectors([[2.]*32],1,32)[0]),1)

    def test_model_routes_belong_to_administration(self):
        from backend.portal import restricted_workspace_api
        for path in ('embedding-info','embedding-test','embedding-credential','embedding-prepare'):
            self.assertTrue(restricted_workspace_api('/api/knowledge/'+path,'POST'))


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class EmbeddingNative(unittest.TestCase):
    from tests.test_rag_pipeline import PipelineNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    document=_Fixture.document
    checked=_Fixture.checked
    def test_download_queue_deduplicates_supports_cancel_retry_and_keeps_settings(self):
        from backend.knowledge.worker import worker
        from backend.knowledge import repository as r
        before=self.checked(self.client.get('/api/knowledge/retrieval-settings'))
        profile=EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json')
        with patch.object(worker,'start'):
            first=self.checked(self.client.post('/api/knowledge/embedding-prepare',json={'config':profile}))
            again=self.checked(self.client.post('/api/knowledge/embedding-prepare',json={'config':profile}))
            self.assertEqual(first['id'],again['id'])
            self.checked(self.client.post(f"/api/knowledge/jobs/{first['id']}/cancel"))
            retry=self.checked(self.client.post(f"/api/knowledge/jobs/{first['id']}/retry"))
            self.assertEqual(retry['state'],'queued')
            with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='failed',error='download failed' WHERE id=:id",id=first['id'])
            newer=self.checked(self.client.post('/api/knowledge/embedding-prepare',json={'config':profile}))
            self.assertNotEqual(first['id'],newer['id'])
            self.assertEqual(self.client.post(f"/api/knowledge/jobs/{first['id']}/retry").status_code,409)
        after=self.checked(self.client.get('/api/knowledge/retrieval-settings'))
        self.assertEqual(before['version'],after['version']);self.assertEqual(before['options'],after['options'])

    def test_worker_verifies_model_download_and_reports_retryable_failure(self):
        from backend.knowledge.worker import worker
        from backend.knowledge import repository as r
        from fastapi import HTTPException
        profile=EmbeddingOptions().model_dump(mode='json')
        with patch.object(worker,'start'):job=self.checked(self.client.post('/api/knowledge/embedding-prepare',json={'config':profile}))
        worker.root=self.root;worker.stop_event.clear()
        with patch('backend.knowledge.embedding_download.prepare',side_effect=HTTPException(503,'模型下载或校验失败，请检查后台网络')):worker.run(dict(worker.claim()))
        failure=self.checked(self.client.get('/api/knowledge/jobs/'+str(job['id'])))
        self.assertEqual(failure['state'],'failed');self.assertIn('后台网络',failure['error'])
        with patch.object(worker,'start'):self.checked(self.client.post(f"/api/knowledge/jobs/{job['id']}/retry"))
        def prepare(root,config,check,progress):
            check();progress('verify_embedding',95);return {'model_key':'download-fixture','dimensions':384}
        with patch('backend.knowledge.embedding_download.prepare',side_effect=prepare):worker.run(dict(worker.claim()))
        finished=self.checked(self.client.get('/api/knowledge/jobs/'+str(job['id'])))
        self.assertEqual(finished['state'],'succeeded');self.assertEqual(finished['progress'],100)
        self.assertEqual(finished['result']['dimensions'],384)
    def test_model_inventory_separates_local_files_external_services_and_jobs(self):
        from pathlib import Path
        from backend.knowledge.embedding_config import EmbeddingOptions
        from backend.knowledge.worker import worker
        with patch.dict(os.environ,{'SHIXUE_KB_MODEL_CACHE':str(self.root/'inventory-cache')}):
            info=self.checked(self.client.get('/api/knowledge/embedding-info'))
            local=[p for p in info['presets'] if p['provider']=='local']
            self.assertEqual(len(local),4);self.assertTrue(all(p['availability']['state']=='missing' for p in local))
            self.assertTrue(all(p['availability']['state']=='external' for p in info['presets'] if p['provider']!='local'))
            profile=EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json')
            with patch.object(worker,'start'):
                prepared=self.checked(self.client.post('/api/knowledge/embedding-prepare',json={'config':profile}))
            info=self.checked(self.client.get('/api/knowledge/embedding-info'))
            bge=next(p for p in info['presets'] if p['id']=='bge-small-zh')
            self.assertEqual(bge['availability']['state'],'preparing')
            self.assertEqual(next(p for p in info['presets'] if p['id']=='minilm')['availability']['state'],'missing')
            self.assertEqual(str(info['preparation_jobs'][0]['id']),str(prepared['id']))
            self.assertFalse((self.root/'inventory-cache').exists())
    def setUp(self):
        self._Fixture.setUp(self)
        self.calls=[]
        calls=self.calls
        class Model(BaseHTTPRequestHandler):
            def do_POST(inner):
                body=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])));calls.append({'body':body,'authorization':inner.headers.get('Authorization')})
                dimensions=body.get('dimensions',512);bad=body['model']=='bad-model'
                result={'data':[{'index':i,'embedding':([1.]+[0.]*(dimensions-1)) if not bad else [1.]} for i,_value in enumerate(body['input'])][::-1]}
                data=json.dumps(result).encode();inner.send_response(200);inner.send_header('Content-Type','application/json');inner.send_header('Content-Length',str(len(data)));inner.end_headers();inner.wfile.write(data)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Model)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}/v1'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5);self._Fixture.tearDown(self)
    def config(self,dimensions=512,model='fixture-model'):
        return EmbeddingOptions(provider='openai_compatible',model=model,base_url=self.base,dimensions=dimensions,send_dimensions=True,batch_size=2,document_prefix='D: ',query_prefix='Q: ').model_dump(mode='json')
    def save(self,config):
        from backend.knowledge import retrieval_settings
        current=retrieval_settings.read(self.root)
        return retrieval_settings.save(self.root,{'version':current['version'],'options':{**current['options'],'embedding':config}})

    def test_real_dimension_storage_search_and_mismatch_are_isolated(self):
        from backend.knowledge import repository as r
        from backend.knowledge.ingestion import process_index
        from backend.knowledge.retrieval import search
        from fastapi import HTTPException
        first=self.config(512);self.save(first)
        a,_,ra=self.document('模型甲','模型维度财政证据')
        one=process_index(self.root,{'edition_id':a,'revision_id':ra,'embedding':first},lambda:None,lambda *a:None)
        found=search(self.root,{'query':'财政','mode':'semantic','scope':{'edition_ids':[a]}})
        self.assertTrue(found['results']);self.assertTrue(any(c['body']['input'][0].startswith('Q: ') for c in self.calls))
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.one(db,'SELECT vector_dims(embedding) dimensions FROM kb_chunks WHERE index_id=:id LIMIT 1',id=one['index_id'])['dimensions'],512)
        second=self.config(3072,'other-model');self.save(second)
        with self.assertRaises(HTTPException) as error:search(self.root,{'query':'财政','mode':'semantic','scope':{'edition_ids':[a]}})
        self.assertEqual(error.exception.status_code,409)
        b,_,rb=self.document('模型乙','模型维度财政证据')
        two=process_index(self.root,{'edition_id':b,'revision_id':rb,'embedding':second},lambda:None,lambda *a:None)
        found=search(self.root,{'query':'财政','mode':'semantic','scope':{'edition_ids':[a,b]}})
        self.assertEqual({str(h['edition_id']) for h in found['results']},{b});self.assertTrue(found['warnings'])
        with r.database.transaction(self.root) as db:
            self.assertEqual(str(r.one(db,'SELECT active_index FROM kb_editions WHERE id=:id',id=a)['active_index']),one['index_id'])
            self.assertEqual(r.one(db,'SELECT vector_dims(embedding) dimensions FROM kb_chunks WHERE index_id=:id LIMIT 1',id=two['index_id'])['dimensions'],3072)

    def test_queued_model_is_frozen_and_bad_vectors_cannot_publish(self):
        from backend.knowledge import repository as r
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.ingestion import process_import,process_index
        first=self.config(512);self.save(first)
        edition=CatalogService(self.root).create({'title':'模型冻结任务','item_type':'article'})
        job=CatalogService(self.root).import_file(edition['id'],'sample.txt','模型固定原文'.encode())
        self.save(self.config(768,'new-model'))
        self.assertEqual(job['payload']['embedding']['dimensions'],512)
        parsed=process_import(self.root,job['payload'],lambda:None,lambda *a:None)
        with r.database.transaction(self.root) as db:
            index_job=r.one(db,"SELECT payload FROM kb_jobs WHERE kind='index' AND payload->>'edition_id'=:id ORDER BY created_at DESC LIMIT 1",id=str(edition['id']))['payload']
        self.assertEqual(index_job['embedding']['dimensions'],512)
        good=process_index(self.root,index_job,lambda:None,lambda *a:None)
        bad={**index_job,'embedding':self.config(512,'bad-model')}
        with self.assertRaises(RuntimeError):process_index(self.root,bad,lambda:None,lambda *a:None)
        with r.database.transaction(self.root) as db:self.assertEqual(str(r.one(db,'SELECT active_index FROM kb_editions WHERE id=:id',id=edition['id'])['active_index']),good['index_id'])

    def test_credentials_do_not_echo_and_are_bound_to_one_endpoint(self):
        secret='isolated-key-never-echo-123'
        stored=self.checked(self.client.post('/api/knowledge/embedding-credential',json={'api_key':secret,'base_url':self.base}))
        config={**self.config(),'credential_id':stored['credential_id']};self.save(config)
        from backend.knowledge.embedding_secrets import directory
        self.assertNotIn(secret,next(directory(self.root).glob('*.json')).read_text())
        response=self.checked(self.client.post('/api/knowledge/embedding-test',json={'config':config}))
        self.assertEqual(response['dimensions'],512);self.assertEqual(response['query_dimensions'],512)
        self.assertEqual(self.calls[-1]['authorization'],'Bearer '+secret)
        self.assertNotIn(secret,self.client.get('/api/knowledge/embedding-info').text)
        self.assertNotIn(secret,self.client.get('/api/knowledge/retrieval-settings').text)
        count=len(self.calls)
        invalid=self.client.post('/api/knowledge/embedding-test',json={'config':{**config,'base_url':self.base+'/other'}})
        self.assertEqual(invalid.status_code,400);self.assertEqual(len(self.calls),count);self.assertNotIn(secret,invalid.text)
