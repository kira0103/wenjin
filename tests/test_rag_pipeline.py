"""RAG processing must execute in the backend and preserve original evidence."""
import io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock,patch
from fastapi import HTTPException
from backend.knowledge.cleaning import clean,index_text
from backend.knowledge.chunking import configured_spans,preview
from backend.knowledge.schemas import ChunkConfig,IngestionOptions,RetrievalOptions,QueryOptions
from backend.knowledge.query_processing import transform


class PipelineRules(unittest.TestCase):
    def test_cleaning_keeps_source_map_and_rare_characters(self):
        source='𠀀原文\x01  赋税\n重复行\n重复行\n联系 a@example.org https://example.org\n\n\n后文'
        options={'remove_control_characters':True,'normalize_whitespace':True,'remove_urls':True,'remove_emails':True,'deduplicate_lines':True}
        text,positions,changes=clean(source,options)
        self.assertIn('𠀀原文 赋税',text);self.assertEqual(text.count('重复行'),1)
        self.assertNotIn('http',text);self.assertNotIn('@',text);self.assertNotIn('\x01',text)
        self.assertEqual(len(text),len(positions));self.assertEqual(positions,sorted(positions))
        self.assertTrue(changes)
        for c,i in zip(text,positions):self.assertTrue(c==source[i] or c in ' \n')
        plan=preview(source,ChunkConfig(cleaning=options).model_dump())
        for part in plan['chunks']:self.assertEqual(part['body'],source[part['start']:part['end']])

    def test_delimiter_and_parent_child_chunks_keep_original_offsets(self):
        source='卷一𠀀正文。'*80
        config=ChunkConfig(mode='parent_child',size=80,overlap=10,parent_size=300)
        children=list(configured_spans(source,config.model_dump()))
        parents=list(configured_spans(source,{**config.model_dump(),'mode':'recursive','size':300,'overlap':0}))
        self.assertGreater(len(children),len(parents))
        for start,end,body in children:
            self.assertEqual(body,source[start:end]);self.assertLessEqual(end-start,80)
            self.assertTrue(any(s<=start<end<=e for s,e,_ in parents))
        config=ChunkConfig(mode='delimiter',size=80,overlap=0,separator='|')
        self.assertEqual([body for _,_,body in configured_spans('甲'*30+'|'+'乙'*60,config.model_dump())],['甲'*30+'|','乙'*60])

    def test_cleaning_is_applied_before_chunking_not_restarted_inside_each_chunk(self):
        from backend.knowledge.chunking import index_spans
        source='重复标题\n'+'甲'*45+'\n重复标题\n'+'乙'*45
        config=ChunkConfig(size=50,overlap=0,cleaning={'deduplicate_lines':True}).model_dump()
        spans=list(index_spans(source,config))
        indexed=''.join(item[3] for item in spans)
        self.assertEqual(indexed,clean(source,config['cleaning'])[0]);self.assertEqual(indexed.count('重复标题'),1)
        for start,end,body,_ in spans:self.assertEqual(body,source[start:end])

    def test_off_and_exact_never_call_model(self):
        model=Mock()
        self.assertEqual(transform('原句',QueryOptions().model_dump(),model,{})['outcome'],'disabled')
        self.assertEqual(transform('原句',QueryOptions(mode='rewrite').model_dump(),model,{},exact=True)['outcome'],'exact_bypass')
        model.assert_not_called()

    def test_query_expansion_is_bounded_deduplicated_and_sanitized(self):
        settings={'model':'mock','base_url':'http://127.0.0.1'}
        model=Mock(return_value={'text':json.dumps({'queries':['财政赋税','财政赋税','税收','其他']}),'usage':{'total_tokens':15,'secret':'hidden'}})
        result=transform('原问题',QueryOptions(mode='multi_query',max_queries=3).model_dump(),model,settings)
        self.assertEqual(result['queries'],['原问题','财政赋税','税收']);self.assertEqual(result['model_calls'],1)
        self.assertEqual(result['usage'],{'total_tokens':15})
        model.side_effect=ValueError('private-provider-secret')
        result=transform('原问题',QueryOptions(mode='rewrite').model_dump(),model,settings)
        self.assertEqual(result['queries'],['原问题']);self.assertNotIn('private-provider-secret',json.dumps(result))
        with self.assertRaises(HTTPException):transform('原问题',QueryOptions(mode='rewrite',failure_policy='error').model_dump(),model,settings)

    def test_hyde_is_only_a_vector_input_and_skips_keyword_queries(self):
        model=Mock(return_value={'text':'{"passage":"假设性文献回答"}'})
        policy=QueryOptions(mode='hyde').model_dump()
        result=transform('问题',policy,model,{'base_url':'http://127.0.0.1','model':'mock'})
        self.assertEqual(result['queries'],['问题']);self.assertEqual(result['hypothetical_passage'],'假设性文献回答')
        model.reset_mock();self.assertEqual(transform('问题',policy,model,{},semantic=False)['outcome'],'keyword_bypass');model.assert_not_called()

    def test_invalid_query_responses_fall_back(self):
        for output in ('not json','{"queries":[]}','{"queries":[3]}','{"queries":["'+('字'*1001)+'"]}'):
            result=transform('问题',QueryOptions(mode='rewrite').model_dump(),lambda *args:{'text':output},{'base_url':'http://127.0.0.1','model':'mock'})
            self.assertEqual(result['outcome'],'model_error');self.assertEqual(result['queries'],['问题'])

    def test_html_csv_and_office_loaders_do_not_execute_content(self):
        from backend.knowledge.file_loaders import load
        self.assertIn('原文𠀀',load('.html','<p>原文𠀀</p><script>fetch("secret")</script>'.encode()))
        self.assertNotIn('secret',load('.html','<p>原文</p><script>secret</script>'.encode()))
        self.assertIn('甲,乙',load('.csv','姓名,内容\n甲,"甲,乙"'.encode()))
        for suffix,files,expected in [('.pptx',{'ppt/slides/slide1.xml':'<a xmlns="urn:t"><t>幻灯原文</t></a>'},'幻灯原文'),
            ('.xlsx',{'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><f>EXECUTE()</f><v>7</v></c></row></sheetData></worksheet>'},'A1：7')]:
            data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as archive:
                for name,body in files.items():archive.writestr(name,body)
            self.assertIn(expected,load(suffix,data.getvalue()))

    def test_pdf_ocr_policy_controls_actual_parser(self):
        import pymupdf as fitz
        from backend.knowledge.ingestion import parse
        doc=fitz.open();page=doc.new_page();page.insert_text((72,72),'Readable text layer')
        data=doc.tobytes();doc.close()
        with patch('backend.knowledge.ingestion.recognize',return_value=('OCR 替换录文',{'source':'ocr'})) as ocr:
            pages,_=parse('policy.pdf',data,ocr_mode='never');ocr.assert_not_called()
            self.assertIn('Readable',pages[0][1])
            pages,_=parse('policy.pdf',data,ocr_mode='always');ocr.assert_called_once()
            self.assertEqual(pages[0][1],'OCR 替换录文')


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class PipelineNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_retrieval_quality import QualityNativeTests
        QualityNativeTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):
        from tests.test_retrieval_quality import QualityNativeTests
        QualityNativeTests.tearDownClass.__func__(cls)
    def setUp(self):
        from tests.test_retrieval_quality import QualityNativeTests
        from backend.knowledge.worker import worker
        from backend.knowledge import repository as r,retrieval_settings
        QualityNativeTests.setUp(self)
        self.disabled=patch.object(worker,'enabled',False);self.disabled.start()
        current=retrieval_settings.read(self.root)
        retrieval_settings.save(self.root,{'version':current['version'],'options':RetrievalOptions().model_dump()})
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE state IN ('queued','running')")
    def tearDown(self):
        from tests.test_retrieval_quality import QualityNativeTests
        self.disabled.stop();QualityNativeTests.tearDown(self)
    def checked(self,response):
        self.assertEqual(response.status_code,200,response.text);return response.json()
    def document(self,*args):
        from tests.test_retrieval_quality import QualityNativeTests
        return QualityNativeTests.document(self,*args)
    def save(self,**changes):
        current=self.checked(self.client.get('/api/knowledge/retrieval-settings'))
        return self.checked(self.client.put('/api/knowledge/retrieval-settings',json={'version':current['version'],'options':{**current['options'],**changes}}))

    def test_existing_core_library_can_enter_vectors_without_reupload(self):
        from backend.knowledge import repository as r
        from backend.knowledge.core_corpus import process_import
        from backend.knowledge.worker import worker
        from backend.knowledge.embedding_config import EmbeddingOptions
        source=self.root/'existing-core';(source/'儒藏').mkdir(parents=True)
        original='---\ntitle: 已有知识库向量加工\n---\n学而时习之。财政赋税制度𠀀。\n'
        (source/'儒藏'/'已有原文.md').write_text(original,encoding='utf-8')
        process_import(self.root,{'source_path':str(source),'slug':'existing-vectors','name':'已有资料'})
        listing=self.checked(self.client.get('/api/knowledge/rag-documents?query=已有知识库向量加工&state=pending'))
        doc=listing['items'][0];self.assertTrue(doc['core']);self.assertEqual(doc['vector_state'],'pending')
        self.assertEqual(listing['summary']['pending'],1)
        payload={'operation':'process','documents':[{'id':doc['id'],'revision_id':doc['active_revision']}]}
        queued=self.checked(self.client.post('/api/knowledge/rag-process',json=payload))
        self.assertEqual(queued['jobs'][0]['kind'],'index')
        self.assertEqual(self.checked(self.client.post('/api/knowledge/rag-process',json=payload))['jobs'][0]['id'],queued['jobs'][0]['id'])
        self.assertEqual(self.checked(self.client.get('/api/knowledge/rag-documents?query=已有知识库向量加工&state=processing'))['total'],1)
        worker.root=self.root;worker.stop_event.clear();job=worker.claim()
        self.assertEqual(str(job['id']),queued['jobs'][0]['id'])
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.0]+[0.0]*383 for _ in values],'existing-core-fixture')):worker.run(dict(job))
        ready=self.checked(self.client.get('/api/knowledge/rag-documents?query=已有知识库向量加工&state=ready'))
        self.assertEqual(ready['total'],1);self.assertEqual(ready['summary']['pending'],0)
        self.assertEqual(ready['items'][0]['job']['state'],'succeeded')
        self.assertEqual(ready['items'][0]['active_revision'],doc['active_revision'])
        with r.database.transaction(self.root) as db:
            count=r.execute(db,'SELECT count(*) FROM kb_chunks c JOIN kb_editions e ON e.active_index=c.index_id WHERE e.id=:id',id=doc['id']).scalar_one()
            self.assertGreater(count,0)
        self.assertEqual((source/'儒藏'/'已有原文.md').read_text(encoding='utf-8'),original)
        self.save(embedding=EmbeddingOptions(model='BAAI/bge-small-zh-v1.5',dimensions=512).model_dump(mode='json'))
        mismatch=self.checked(self.client.get('/api/knowledge/rag-documents?query=已有知识库向量加工&state=mismatch'))
        self.assertEqual(mismatch['total'],1);self.assertEqual(mismatch['summary']['ready'],0)
        self.assertEqual(mismatch['embedding']['dimensions'],512)
        stale=self.client.post('/api/knowledge/rag-process',json={**payload,'expected_policy_version':listing['policy_version']})
        self.assertEqual(stale.status_code,409)
        with r.database.transaction(self.root) as db:
            self.assertEqual(r.execute(db,"SELECT count(*) FROM kb_jobs WHERE payload->>'edition_id'=:id AND state IN ('queued','running')",id=doc['id']).scalar_one(),0)

    def test_process_original_only_document_parses_then_vectorizes(self):
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.worker import worker
        from backend.knowledge import repository as r
        catalog=CatalogService(self.root);edition=catalog.create({'title':'已有原件自动加工','item_type':'article'})
        upload=catalog.import_file(edition['id'],'原件.txt','仅有原件的财政赋税文献𠀀。'.encode())
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE id=:id",id=upload['id'])
        response=self.checked(self.client.post('/api/knowledge/rag-process',json={'operation':'process','documents':[{'id':str(edition['id']),'revision_id':None}]}))
        self.assertEqual(response['jobs'][0]['kind'],'import')
        worker.root=self.root;worker.stop_event.clear();worker.run(dict(worker.claim()))
        midway=self.checked(self.client.get('/api/knowledge/rag-documents?query=已有原件自动加工'))['processing_progress']['batch']
        self.assertEqual(midway['total'],1);self.assertEqual(midway['percent'],20)
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.0]+[0.0]*383 for _ in values],'original-only-fixture')):worker.run(dict(worker.claim()))
        ready=self.checked(self.client.get('/api/knowledge/rag-documents?query=已有原件自动加工&state=ready'))
        self.assertEqual(ready['total'],1);self.assertIsNotNone(ready['items'][0]['active_revision'])
        self.assertEqual(ready['processing_progress']['batch']['total'],1)
        self.assertEqual(ready['processing_progress']['batch']['percent'],100)

    def test_batch_and_document_progress_remain_visible_across_filters_and_legacy_jobs(self):
        from backend.knowledge import repository as r
        from backend.knowledge.worker import worker
        a,_,ra=self.document('进度夹具甲','财政制度原文。'*800)
        b,_,rb=self.document('进度夹具乙','财政制度原文。'*800)
        processing=IngestionOptions(chunking=ChunkConfig(mode='fixed',size=80,overlap=0)).model_dump()
        previous_total=self.checked(self.client.get('/api/knowledge/rag-documents'))['processing_progress']['overall']['total']
        result=self.checked(self.client.post('/api/knowledge/rag-process',json={'operation':'process','processing':processing,'documents':[{'id':a,'revision_id':ra},{'id':b,'revision_id':rb}]}))
        # Existing submitted queues predate the explicit batch marker.
        with r.database.transaction(self.root) as db:
            for job in result['jobs']:r.execute(db,"UPDATE kb_jobs SET payload=payload-'processing_batch' WHERE id=:id",id=job['id'])
        listing=self.checked(self.client.get('/api/knowledge/rag-documents?query=不存在的文献&offset=20'))
        self.assertEqual(listing['items'],[]);self.assertEqual(listing['processing_progress']['batch']['total'],2)
        self.assertEqual(listing['processing_progress']['batch']['queued'],2)
        worker.root=self.root;worker.stop_event.clear();job=worker.claim();seen=[]
        def embed(root,values):
            data=self.checked(self.client.get('/api/knowledge/rag-documents?query=进度夹具'))
            row=next(d for d in data['items'] if d['job']['id']==str(job['id']))
            self.assertEqual(row['job']['stage'],'embedding');self.assertTrue(row['job']['lease_valid'])
            detail=row['job']['detail'];self.assertEqual(detail['unit'],'chunks');self.assertGreater(detail['total'],32)
            self.assertEqual(sum(f['completed'] for f in detail['files']),detail['completed'])
            seen.append(detail['completed'])
            return [[1.]+[0.]*383 for _ in values],'progress-fixture'
        with patch('backend.knowledge.ingestion.embed',side_effect=embed):worker.run(dict(job))
        self.assertGreater(len(seen),1);self.assertEqual(seen,sorted(seen));self.assertGreater(seen[-1],0)
        data=self.checked(self.client.get('/api/knowledge/rag-documents?query=进度夹具'))
        batch=data['processing_progress']['batch'];self.assertEqual(batch['succeeded'],1);self.assertEqual(batch['queued'],1)
        self.assertEqual(batch['percent'],50)
        self.assertEqual(data['processing_progress']['overall']['total'],previous_total+2)
        self.assertGreaterEqual(data['processing_progress']['overall']['succeeded'],1)
        completed=next(d for d in data['items'] if d['job']['id']==str(job['id']))
        self.assertEqual(completed['job']['progress'],100);self.assertGreater(completed['job']['chunks'],0)
        queued=next(d for d in data['items'] if d['job']['state']=='queued')
        self.assertEqual(queued['job']['queue_position'],1)
        self.checked(self.client.post('/api/knowledge/rag-process',json={'operation':'index','processing':processing,'documents':[{'id':job['payload']['edition_id'],'revision_id':job['payload']['revision_id']}]}))
        rebuilt=self.checked(self.client.get('/api/knowledge/rag-documents'))['processing_progress']
        self.assertEqual(rebuilt['overall']['total'],previous_total+2,'rebuilding a document must not double-count submitted documents')

    def test_ingestion_freezes_cleaning_and_chunking_and_retains_source_evidence(self):
        from backend.knowledge import repository as r
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.ingestion import process_import,process_index
        processing=IngestionOptions(chunking=ChunkConfig(size=80,cleaning={'remove_urls':True})).model_dump()
        self.save(ingestion=processing)
        catalog=CatalogService(self.root);edition=catalog.create({'title':'清洗冻结测试','item_type':'article'})
        source='𠀀财政赋税 https://example.org 政策原文。'*10
        job=catalog.import_file(edition['id'],'原文.txt',source.encode())
        self.save(ingestion=IngestionOptions(chunking=ChunkConfig(size=200)).model_dump())
        with r.database.transaction(self.root) as db:payload=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=job['id'])['payload']
        parsed=process_import(self.root,payload,lambda:None,lambda *a:None)
        with r.database.transaction(self.root) as db:
            cfg=r.one(db,'SELECT config FROM kb_indexes WHERE id=:id',id=parsed['index_id'])['config']
            self.assertEqual(cfg['size'],80);self.assertTrue(cfg['cleaning']['remove_urls'])
            block=r.one(db,'SELECT * FROM kb_blocks WHERE revision_id=:id ORDER BY ordinal LIMIT 1',id=parsed['revision_id'])
            self.assertIn('https://example.org',block['body'])
            anchor=r.anchor(db,block['id'],0,5);self.assertEqual(anchor['quote'],'𠀀财政赋税')
        captured=[]
        def embed(root,values):captured.extend(values);return [[1.0]+[0.0]*383 for _ in values],'pipeline-fixture'
        with patch('backend.knowledge.ingestion.embed',side_effect=embed):
            indexed=process_index(self.root,{'edition_id':str(edition['id']),'revision_id':parsed['revision_id'],'config':cfg},lambda:None,lambda *a:None)
        self.assertTrue(captured);self.assertFalse(any('https://' in text for text in captured))
        with r.database.transaction(self.root) as db:
            self.assertEqual(str(r.one(db,'SELECT active_revision FROM kb_editions WHERE id=:id',id=edition['id'])['active_revision']),parsed['revision_id'])
            for item in r.rows(db,'SELECT c.*,b.body original FROM kb_chunks c JOIN kb_blocks b ON b.id=c.block_id WHERE c.index_id=:id',id=indexed['index_id']):
                self.assertEqual(item['body'],item['original'][item['start_offset']:item['end_offset']]);self.assertNotIn('https://',item['normalized'])

    def test_rag_role_isolation_with_identical_keywords_vectors_and_query_expansion(self):
        """Exercise real RLS with maximally similar public/A/B candidates."""
        import uuid
        from backend.identity import workspace_key
        from backend.portal_shared_database import provision_private
        from backend.knowledge import repository as r
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.ingestion import publish,process_index
        from backend.knowledge.retrieval import search
        admin=self.product.manager.url.render_as_string(hide_password=False)
        library={'root':str(self.root),'key':workspace_key(self.root),'runtime_url':admin,'owner_url':admin,'admin_url':admin}
        users=[provision_private(library,uuid.uuid4().hex,self.root/f'isolation-{name}.json') for name in ('a','b')]
        vector=[1.0]+[0.0]*383
        def environment(user):
            return {'SHIXUE_KB_DATABASE_URL':user['runtime_url'],'SHIXUE_PRIVATE_SCHEMA':user['schema'],
                    'SHIXUE_LIBRARY_KEY':library['key'],'SHIXUE_LIBRARY_ROOT':library['root']}
        def document(root,label):
            item=CatalogService(root).create({'title':label,'item_type':'article'})
            body='租税隔离夹具 '+label+' 原始证据𠀀'
            job=CatalogService(root).import_file(item['id'],'isolation.txt',body.encode())
            with r.database.transaction(root) as db:
                version=publish(db,item['id'],[{'asset_id':str(job['asset_id']),'page_index':1,'body':body}])
            with patch('backend.knowledge.ingestion.embed',return_value=([vector],'isolation-model')):
                process_index(root,{'edition_id':str(item['id']),'revision_id':version['revision_id'],
                                   'config':ChunkConfig(mode='parent_child',parent_mode='structure',size=80,parent_size=500).model_dump()},lambda:None,lambda *a:None)
            return str(item['id'])
        public=document(self.root,'公共系统资料')
        personal=[]
        for n,user in enumerate(users):
            with patch.dict(os.environ,environment(user)):
                personal.append(document(self.root/f'user-{n}',f'用户{n}私密资料'))
                with r.database.transaction(self.root/f'user-{n}') as db:
                    role=r.one(db,'SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    self.assertFalse(role['rolsuper']);self.assertFalse(role['rolbypassrls'])
        with r.database.transaction(self.root) as db:
            foreign=[{'block_id':str(b['id']),'start':0,'end':len(b['body'])} for b in r.rows(db,'SELECT b.* FROM kb_blocks b JOIN kb_revisions rev ON rev.id=b.revision_id WHERE rev.edition_id=ANY(CAST(:editions AS uuid[]))',editions=[public,*personal])]
            for row in r.rows(db,'SELECT c.id,c.parent_spans FROM kb_chunks c JOIN kb_indexes i ON i.id=c.index_id JOIN kb_revisions rev ON rev.id=i.revision_id WHERE rev.edition_id=ANY(CAST(:editions AS uuid[]))',editions=[public,*personal]):
                r.execute(db,'UPDATE kb_chunks SET parent_spans=CAST(:spans AS jsonb) WHERE id=:id',id=row['id'],spans=r.j(row['parent_spans']+foreign))
        options=RetrievalOptions(rerank_enabled=False,parent_enabled=True).model_dump()
        for n,user in enumerate(users):
            with patch.dict(os.environ,environment(user)),patch('backend.knowledge.retrieval.embed',return_value=([vector],'isolation-model')):
                root=self.root/f'user-{n}';allowed={public,personal[n]};forbidden=personal[1-n]
                for mode in ('keyword','semantic','hybrid'):
                    with self.subTest(user=n,mode=mode):
                        payload={'query':'租税隔离夹具','mode':mode,'limit':20,'source_kinds':['document'],'scope':{'edition_ids':[public,*personal]},'retrieval':options}
                        found=search(root,payload)
                        self.assertEqual({str(hit['edition_id']) for hit in found['results']},allowed)
                        self.assertTrue(all('parent_spans' not in hit for hit in found['results']))
                        self.assertNotIn(f'用户{1-n}私密资料',json.dumps(found['results'],ensure_ascii=False,default=str))
                        if mode=='semantic':
                            with self.assertRaises(HTTPException) as error:search(root,{**payload,'scope':{'edition_ids':[forbidden]}})
                            self.assertEqual(error.exception.status_code,409)
                        else:
                            denied=search(root,{**payload,'scope':{'edition_ids':[forbidden]}})
                            self.assertEqual(denied['results'],[])
                        unfiltered=search(root,{**payload,'scope':{}})
                        self.assertEqual({str(hit['edition_id']) for hit in unfiltered['results']},allowed)
                for strategy,response in [('rewrite','{"queries":["租税隔离夹具"]}'),
                    ('multi_query','{"queries":["租税隔离夹具","原始证据"]}'),('hyde','{"passage":"租税隔离夹具 假设回答 用户私密资料"}')]:
                    with self.subTest(user=n,query_processing=strategy):
                        policy={**options,'query':{**options['query'],'mode':strategy}}
                        found=search(root,{**payload,'mode':'hybrid','retrieval':policy},query_completion=lambda *a:{'text':response},query_settings={'base_url':'http://127.0.0.1','model':'fixture'})
                        self.assertEqual({str(hit['edition_id']) for hit in found['results']},allowed)
                        with r.database.transaction(root) as db:
                            run=r.one(db,'SELECT snapshot,results FROM kb_retrieval_runs WHERE id=:id',id=found['retrieval_run_id'])
                            self.assertEqual({item['edition_id'] for item in run['snapshot']},allowed)
                            self.assertNotIn(forbidden,json.dumps(run))
        r.database.dispose()

    def test_preview_and_processing_jobs_are_explicit_versioned_and_deduplicated(self):
        from backend.knowledge import repository as r
        edition,_,revision=self.document('显式加工文献','清洗加工原文')
        processing=IngestionOptions(chunking=ChunkConfig(mode='parent_child',size=80,parent_size=500)).model_dump()
        plan=self.checked(self.client.post('/api/knowledge/rag-preview',json={'text':'原文  正文\n'*50,'processing':processing}))
        self.assertGreater(plan['total'],0)
        payload={'operation':'index','processing':processing,'documents':[{'id':edition,'revision_id':revision}]}
        first=self.checked(self.client.post('/api/knowledge/rag-process',json=payload))
        again=self.checked(self.client.post('/api/knowledge/rag-process',json=payload))
        self.assertEqual(first['jobs'][0]['id'],again['jobs'][0]['id'])
        payload['processing']['chunking']['size']=90
        self.assertEqual(self.client.post('/api/knowledge/rag-process',json=payload).status_code,409)
        with r.database.transaction(self.root) as db:self.assertEqual(str(r.one(db,'SELECT active_revision FROM kb_editions WHERE id=:id',id=edition)['active_revision']),revision)
        listing=self.checked(self.client.get('/api/knowledge/rag-documents?query=显式加工'))
        self.assertEqual(listing['items'][0]['job']['state'],'queued')

    def test_manual_input_is_saved_as_an_original_and_queued(self):
        from backend.knowledge import repository as r
        value=self.checked(self.client.post('/api/knowledge/rag-text',json={'title':'直接录入测试','text':'管理员录入的原文𠀀'}))
        with r.database.transaction(self.root) as db:
            job=r.one(db,'SELECT * FROM kb_jobs WHERE id=:id',id=value['job_id']);self.assertEqual(job['kind'],'import')
            self.assertIn('processing',job['payload'])
            asset=r.one(db,'SELECT a.storage_key FROM kb_assets a JOIN kb_edition_assets ea ON ea.asset_id=a.id WHERE ea.edition_id=:id',id=value['edition_id'])
        self.assertEqual(r.asset_path(self.root,asset['storage_key']).read_text(encoding='utf-8'),'管理员录入的原文𠀀')

    def test_core_corpus_migration_copies_only_requested_sources_and_indexes(self):
        import psycopg
        from backend.knowledge.core_corpus import process_import,search
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.portal_corpus_migrate import copy_corpus
        from backend.portal_database import native_url
        from backend import app as service
        source=self.root/'迁移源';(source/'儒藏').mkdir(parents=True)
        raw='---\ntitle: 迁移论语\n---\n学而时习之，不亦说乎。𠀀\n'
        (source/'儒藏'/'论语.md').write_text(raw,encoding='utf-8')
        process_import(self.root,{'source_path':str(source),'slug':'migration-fixture','name':'迁移夹具'})
        private=CatalogService(self.root).create({'title':'不能随语料公开的其他文献','item_type':'article'})
        with tempfile.TemporaryDirectory(dir=self.root.parent) as folder:
            destination=Path(folder)/'library';target=ProductDatabase(destination,'internal')
            try:
                target.start();self.assertFalse(target.startup_error)
                with psycopg.connect(native_url(self.product.manager.url)) as old,psycopg.connect(native_url(target.manager.url)) as new:
                    old.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    result=copy_corpus(old,new,source,destination,'migration-fixture')
                    self.assertEqual(result['books'],1);self.assertEqual(result['files'],1)
                    self.assertFalse(new.execute('SELECT 1 FROM kb_works WHERE id=%s',(private['work_id'],)).fetchone())
                    self.assertEqual(new.execute('SELECT count(*) FROM kb_works').fetchone()[0],1)
                self.assertEqual((destination/'corpora/migration-fixture/儒藏/论语.md').read_text(encoding='utf-8'),raw)
                found=search(destination,query='学而时习之',mode='literal')
                self.assertTrue(found['items']);self.assertEqual(found['items'][0]['title'],'迁移论语')
            finally:target.stop();service.initialize(self.root)

    def test_parent_child_retrieval_returns_the_matching_parent_with_source_positions(self):
        from backend.knowledge import repository as r
        from backend.knowledge.ingestion import process_index
        body='甲'*260+'目标税制'+'乙'*260
        edition,_,revision=self.document('父子块边界',body)
        config=ChunkConfig(mode='parent_child',size=80,overlap=10,parent_size=256).model_dump()
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.0]+[0.0]*383 for _ in values],'parent-fixture')):
            process_index(self.root,{'edition_id':edition,'revision_id':revision,'config':config},lambda:None,lambda *args:None)
        found=self.checked(self.client.post('/api/knowledge/search',json={'query':'目标税制','mode':'keyword','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}}))
        self.assertTrue(found['results']);parts=found['results'][0]['parent_context']['parts']
        self.assertEqual(len(parts),1);self.assertEqual(parts[0]['page_index'],2)
        self.assertGreaterEqual(parts[0]['start'],256);self.assertLessEqual(parts[0]['end'],512)
        self.assertEqual(parts[0]['body'],body[parts[0]['start']:parts[0]['end']])

    def test_cancelled_processing_cannot_switch_the_published_index(self):
        from backend.knowledge import repository as r
        from backend.knowledge.ingestion import process_index
        edition,_,revision=self.document('取消加工发布测试','不可被取消任务覆盖的原文')
        response=self.checked(self.client.post('/api/knowledge/rag-process',json={'operation':'index','documents':[{'id':edition,'revision_id':revision}]}))
        identifier=response['jobs'][0]['id'];owner=r.uid()
        with r.database.transaction(self.root) as db:
            before=str(r.one(db,'SELECT active_index FROM kb_editions WHERE id=:id',id=edition)['active_index'])
            payload=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=identifier)['payload']
            r.execute(db,"UPDATE kb_jobs SET state='running',owner=:owner,lease_until=now()+interval '45 seconds' WHERE id=:id",owner=owner,id=identifier)
        self.checked(self.client.post('/api/knowledge/jobs/'+identifier+'/cancel'))
        payload.update(_job_id=identifier,_job_owner=owner)
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.0]+[0.0]*383 for _ in values],'cancel-fixture')):
            with self.assertRaises(HTTPException):process_index(self.root,payload,lambda:None,lambda *args:None)
        with r.database.transaction(self.root) as db:self.assertEqual(str(r.one(db,'SELECT active_index FROM kb_editions WHERE id=:id',id=edition)['active_index']),before)

    def test_query_expansion_retrieves_real_sources_without_changing_permissions(self):
        from backend.knowledge.worker import worker
        allowed,_,_=self.document('允许的赋税文献','明代财政赋税史料')
        denied,_,_=self.document('范围外文献','宋代其他税制材料')
        self.save(default_mode='keyword',query=QueryOptions(mode='multi_query').model_dump())
        complete=Mock(return_value={'text':json.dumps({'queries':['财政赋税','其他税制']}),'usage':{'total_tokens':10}})
        settings=lambda:{'base_url':'http://127.0.0.1','model':'query-fixture'}
        with patch.object(worker,'research_completion',complete),patch.object(worker,'research_model_settings',settings):
            result=self.checked(self.client.post('/api/knowledge/search',json={'query':'古代财税怎么研究','scope':{'edition_ids':[allowed]}}))
        complete.assert_called_once();self.assertTrue(result['results']);self.assertEqual({hit['edition_id'] for hit in result['results']},{allowed})
        self.assertEqual(result['diagnostics']['query_processing']['queries'],['古代财税怎么研究','财政赋税','其他税制'])
        self.assertNotIn(denied,{hit['edition_id'] for hit in result['results']})

    def test_hyde_never_becomes_cited_source_and_exact_search_skips_processing(self):
        from backend.knowledge.worker import worker
        from backend.knowledge import repository as r
        edition,_,_=self.document('HyDE 真原文','假设不得替代的真实赋税原文')
        self.save(default_mode='semantic',query=QueryOptions(mode='hyde').model_dump())
        vector=[1.0]+[0.0]*383
        with r.database.transaction(self.root) as db:
            r.execute(db,"UPDATE kb_indexes SET state='ready',model_key='hyde-fixture' WHERE id=(SELECT active_index FROM kb_editions WHERE id=:id)",id=edition)
            r.execute(db,'UPDATE kb_chunks c SET embedding=CAST(:v AS vector) WHERE index_id=(SELECT active_index FROM kb_editions WHERE id=:id)',id=edition,v=r.j(vector))
        complete=Mock(return_value={'text':'{"passage":"这只是生成的假设文本"}'})
        embedded=[]
        def embed(root,values):embedded.extend(values);return [vector],'hyde-fixture'
        with patch.object(worker,'research_completion',complete),patch.object(worker,'research_model_settings',lambda:{'base_url':'http://127.0.0.1','model':'mock'}),patch('backend.knowledge.retrieval.embed',side_effect=embed):
            result=self.checked(self.client.post('/api/knowledge/search',json={'query':'税收如何征收','scope':{'edition_ids':[edition]}}))
            complete.reset_mock();exact=self.checked(self.client.post('/api/knowledge/search',json={'query':'真实赋税','mode':'exact','scope':{'edition_ids':[edition]}}));complete.assert_not_called()
        self.assertEqual(embedded,['这只是生成的假设文本']);self.assertTrue(result['results'])
        self.assertTrue(all('这只是生成' not in hit['body'] for hit in result['results']))
        self.assertEqual(exact['diagnostics']['query_processing']['outcome'],'exact_bypass')


if __name__=='__main__':unittest.main()
