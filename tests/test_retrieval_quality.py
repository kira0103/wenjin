"""Quality contracts: scope, source offsets, reproducibility and honest metrics."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.chunking import configured_spans
from backend.knowledge.diagnostics import fusion
from backend.knowledge.evaluation import metrics
from backend.knowledge.ingestion import page_signals,publish,process_index
from backend.knowledge.schemas import Search,ChunkConfig,EvalCase


class QualityRules(unittest.TestCase):
    def test_many_chunks_cannot_inflate_block_fusion_score(self):
        first={'block_id':'a','body':'相同段落'};second={'block_id':'b','body':'其他段落'}
        found,duplicates=fusion([('keyword',[first]*20+[second]),('semantic',[second,first])])
        self.assertAlmostEqual(found[0]['score'],1/61+1/62)
        self.assertAlmostEqual(found[1]['score'],1/61+1/62)
        self.assertEqual(duplicates,21)
        self.assertEqual(found[1]['channels'][0]['rank'],2)
        self.assertEqual(found[1]['channels'][0]['chunk_rank'],21)

    def test_structured_profiles_keep_unicode_and_all_nonspace_characters(self):
        body='第一章 𠀀古籍\n'+('税赋制度及其变化。正文与注文应分别辨识！'*25)+'\n参考文献\n作者与题名。'
        for profile in ('legacy','ancient','paper','general'):
            with self.subTest(profile=profile):
                items=list(configured_spans(body,{'profile':profile,'size':90,'overlap':15}))
                covered=set()
                for start,end,text in items:
                    self.assertEqual(text,body[start:end]);self.assertLessEqual(end-start,90)
                    covered.update(range(start,end))
                self.assertTrue(all(index in covered for index,char in enumerate(body) if not char.isspace()))

    def test_metrics_match_fixed_spans_and_do_not_credit_other_versions(self):
        gold=[{'block_id':'a','start':20,'end':30,'relevance':3}]
        hits=[{'block_id':'other-version','start_offset':20,'end_offset':30},
              {'block_id':'a','start_offset':25,'end_offset':40}]
        value=metrics(hits,gold)
        self.assertEqual(value['recall'],1);self.assertEqual(value['precision'],.5);self.assertEqual(value['mrr'],.5)
        self.assertAlmostEqual(value['ndcg'],1/1.584962500721156)
        self.assertEqual(metrics([],[],True)['negative_pass'],True)
        self.assertIsNone(metrics([],[],True)['recall'])
        self.assertEqual(metrics([{'block_id':'a','start_offset':0,'end_offset':10}],gold)['recall'],0)
        same_block=gold+[{'block_id':'a','start':30,'end':40,'relevance':1}]
        self.assertEqual(metrics([{'block_id':'a','start_offset':20,'end_offset':40}],same_block)['ndcg'],1)

    def test_blank_queries_and_unlabeled_cases_are_rejected(self):
        for constructor in (lambda:Search(query=' \n '),lambda:ChunkConfig(size=80,overlap=40),
                            lambda:EvalCase(search={'query':'制度'})):
            with self.assertRaises(ValidationError):constructor()

    def test_pdf_sparse_nonempty_scan_and_damaged_layer_request_ocr(self):
        from types import SimpleNamespace
        page=SimpleNamespace(rect=SimpleNamespace(width=100,height=100),get_image_info=lambda:[{'bbox':(0,0,100,100)}],get_text=lambda mode:[])
        value=page_signals(page,'已有错误文本层')
        self.assertIn('image_page_with_sparse_text',value['ocr_reasons'])
        self.assertIn('damaged_text_layer',page_signals(page,'\ufffd\ufffd乱码')['ocr_reasons'])
        self.assertNotIn('image_page_with_sparse_text',page_signals(page,'原文'*100)['ocr_reasons'])


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要显式启用独立内置 PostgreSQL')
class QualityNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge.activation import CatalogActivation
        cls.folder=tempfile.TemporaryDirectory(prefix='文献库检索质量-')
        cls.root=Path(cls.folder.name)/'独立资料'
        cls.environment=patch.dict(os.environ,{},clear=False);cls.environment.start()
        os.environ.pop('SHIXUE_KB_DATABASE_URL',None)
        cls.product=ProductDatabase(cls.root,'internal')
        try:
            cls.product.start()
            if cls.product.startup_error:raise RuntimeError(cls.product.startup_error)
            service.initialize(cls.root)
            CatalogActivation(cls.root,service.connection).migrate()
        except Exception:
            cls.product.stop();cls.environment.stop();cls.folder.cleanup();raise

    @classmethod
    def tearDownClass(cls):
        from backend.knowledge.worker import worker
        worker.stop();cls.product.stop();cls.environment.stop();cls.folder.cleanup()

    def setUp(self):
        from backend.knowledge.worker import worker
        worker.stop();service.initialize(self.root);service.service.token='quality-native'
        self.client=TestClient(service.app,headers={'X-App-Token':'quality-native'})

    def tearDown(self):
        from backend.knowledge.worker import worker
        worker.stop();self.client.close()

    def document(self,title,body):
        from backend.knowledge.catalog import CatalogService
        edition=CatalogService(self.root).create({'title':title,'item_type':'ancient','label':'质量测试甲本'})
        h,key=r.store_file(self.root,'原文.txt',body.encode())
        with r.database.transaction(self.root) as connection:
            asset=r.execute(connection,'SELECT id FROM kb_assets WHERE sha256=:h',h=h).scalar()
            if not asset:
                asset=r.uid();r.execute(connection,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:h,:k,0)',id=asset,h=h,k=key)
            r.execute(connection,"INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind) VALUES(:e,:a,'原文.txt','page')",e=edition['id'],a=asset)
            published=publish(connection,edition['id'],[{'asset_id':str(asset),'page_index':1,'body':'范围外财政赋税'},
                {'asset_id':str(asset),'page_index':2,'body':body},{'asset_id':str(asset),'page_index':3,'body':'范围外财政赋税'}])
        return str(edition['id']),str(asset),published['revision_id']

    def checked(self,response):
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def test_trace_persists_and_project_page_scope_is_enforced(self):
        from backend.knowledge.project_sources import ProjectSourceService
        edition,asset,revision=self.document('范围诊断','财政赋税𠀀原文')
        ProjectSourceService(self.root,service.connection).put('project-main',edition,{'roles':['reference'],'mode':'ranges',
            'ranges':[{'asset_id':asset,'page_from':2,'page_to':2}]})
        found=self.checked(self.client.post('/api/knowledge/search',json={'query':'财政赋税','mode':'exact','scope':{'project_id':'project-main','edition_ids':[edition]}}))
        self.assertEqual([item['page_index'] for item in found['results']],[2])
        r.database.dispose()
        trace=self.checked(self.client.get('/api/knowledge/retrieval-runs/'+found['retrieval_run_id']))
        self.assertEqual(trace['snapshot'][0]['revision_id'],revision)
        self.assertEqual(trace['results'][0]['page_index'],2)
        self.assertNotIn('full_body',trace['results'][0])
        self.assertEqual(self.client.post('/api/knowledge/search',json={'query':' \n '}).status_code,422)

    def test_preview_and_reindex_change_only_index_and_preserve_old_anchors(self):
        edition,asset,revision=self.document('分块预览','𠀀财政赋税制度。'*35)
        with r.database.transaction(self.root) as connection:
            block=r.one(connection,'SELECT * FROM kb_blocks WHERE revision_id=:r AND page_index=2',r=revision)
            anchor=r.anchor(connection,block['id'],0,2)
        config={'profile':'ancient','size':180,'overlap':20}
        plan=self.checked(self.client.post('/api/knowledge/editions/'+edition+'/chunk-preview',json={'expected_revision':revision,'config':config}))
        self.assertGreater(plan['total'],0)
        for chunk in plan['chunks']:
            if chunk['block_id']==str(block['id']):self.assertEqual(chunk['body'],block['body'][chunk['start']:chunk['end']])
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,values:([[1.0]+[0.0]*383 for _ in values],'test-vector')):
            indexed=process_index(self.root,{'edition_id':edition,'revision_id':revision,'config':config},lambda:None,lambda *args:None)
        with r.database.transaction(self.root) as connection:
            self.assertEqual(str(r.edition(connection,edition)['active_revision']),revision)
            self.assertEqual(r.one(connection,'SELECT quote FROM kb_anchors WHERE id=:id',id=anchor['id'])['quote'],'𠀀财')
            self.assertEqual(r.one(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=indexed['index_id'])['config']['profile'],'ancient')
        self.assertEqual(self.client.post('/api/knowledge/editions/'+edition+'/chunk-preview',json={'expected_revision':r.uid(),'config':config}).status_code,409)

    def test_evaluation_dispatch_and_stale_labels_are_not_scored_zero(self):
        from backend.knowledge.worker import worker
        edition,asset,revision=self.document('评测版本','财政赋税𠀀评测原文')
        query={'query':'财政赋税','mode':'exact','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}}
        hit=self.checked(self.client.post('/api/knowledge/search',json=query))['results'][0]
        suite=self.checked(self.client.post('/api/knowledge/evaluation/suites',json={'name':'真实范围契约'}))
        case=self.checked(self.client.post('/api/knowledge/evaluation/suites/'+suite['id']+'/cases',json={'search':query,
            'expected':[{'block_id':hit['block_id'],'start':hit['start_offset'],'end':hit['end_offset']}]}))
        self.assertEqual(case['expected'][0]['revision_id'],revision)
        def run():
            with patch.object(worker,'start'):
                self.checked(self.client.post('/api/knowledge/evaluation/suites/'+suite['id']+'/runs'))
            worker.root=self.root;worker.stop_event.clear();job=worker.claim();self.assertEqual(job['kind'],'evaluation');worker.run(dict(job))
            return self.checked(self.client.get('/api/knowledge/evaluation/suites/'+suite['id']))['runs'][0]
        result=run();self.assertEqual(result['state'],'succeeded');self.assertEqual(result['report']['metrics']['recall'],1)
        self.assertEqual(result['report']['scope_leaks'],0)
        with r.database.transaction(self.root) as connection:publish(connection,edition,[{'asset_id':asset,'page_index':2,'body':'新版财政赋税'}])
        result=run();self.assertEqual(result['state'],'succeeded');self.assertEqual(result['report']['stale'],1)
        self.assertIsNone(result['report']['metrics']['recall'])
        r.database.dispose();self.assertEqual(self.checked(self.client.get('/api/knowledge/evaluation/suites/'+suite['id']))['runs'][0]['report']['stale'],1)

    def test_gold_outside_scope_is_rejected_and_empty_case_has_separate_metric(self):
        from backend.knowledge.evaluation import process_evaluation,queue_run
        edition,asset,revision=self.document('负例标注','财政赋税负例')
        suite=self.checked(self.client.post('/api/knowledge/evaluation/suites',json={'name':'范围负例'}))
        with r.database.transaction(self.root) as connection:outside=r.one(connection,'SELECT id FROM kb_blocks WHERE revision_id=:r AND page_index=1',r=revision)
        query={'query':'绝不存在检索词','mode':'exact','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}}
        self.assertEqual(self.client.post('/api/knowledge/evaluation/suites/'+suite['id']+'/cases',json={'search':query,
            'expected':[{'block_id':str(outside['id']),'start':0,'end':2}]}).status_code,400)
        self.checked(self.client.post('/api/knowledge/evaluation/suites/'+suite['id']+'/cases',json={'search':query,'expect_empty':True}))
        queued=queue_run(self.root,suite['id']);process_evaluation(self.root,{'run_id':queued['id']},lambda:None,lambda *args:None)
        with r.database.transaction(self.root) as connection:
            report=r.one(connection,'SELECT report FROM kb_eval_runs WHERE id=:id',id=queued['id'])['report']
            r.execute(connection,"UPDATE kb_jobs SET state='cancelled' WHERE id=:id",id=queued['job_id'])
        self.assertEqual(report['metrics']['negative_pass'],1);self.assertIsNone(report['metrics']['recall'])

    def test_answer_has_trace_and_rejected_citation_is_recorded(self):
        from backend.knowledge.retrieval import answer
        from fastapi import HTTPException
        edition,asset,revision=self.document('问答诊断','财政赋税问答原文')
        query={'query':'财政赋税','mode':'exact','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}}
        no_model=answer(self.root,query,lambda *args:None,{})
        self.assertEqual(no_model['diagnostics']['answer_outcome'],'no_generation_model')
        with self.assertRaises(HTTPException):answer(self.root,query,lambda *args:{'text':'无效引用 [E999]'}, {'base_url':'local','model':'fixture'})
        trace=self.checked(self.client.get('/api/knowledge/retrieval-runs'))[0]
        self.assertEqual(trace['diagnostics']['answer_outcome'],'invalid_citation')
        good=answer(self.root,query,lambda *args:{'text':'资料有赋税记载。[E1]'}, {'base_url':'local','model':'fixture'})
        with r.database.transaction(self.root) as connection:
            self.assertEqual(str(r.one(connection,'SELECT retrieval_run_id FROM kb_qa_runs WHERE id=:id',id=good['id'])['retrieval_run_id']),good['retrieval_run_id'])

    def test_mixed_notes_are_visible_without_exceeding_limit(self):
        edition,asset,revision=self.document('混合笔记范围','混合检索契约')
        self.checked(self.client.post('/api/knowledge/notes',json={'title':'混合检索契约笔记','body':'独立的人工作为笔记'}))
        found=self.checked(self.client.post('/api/knowledge/search',json={'query':'混合检索契约','mode':'exact',
            'source_kinds':['document','note'],'limit':2}))
        self.assertEqual(len(found['results']),2)
        self.assertEqual({item['source_kind'] for item in found['results']},{'document','note'})

    def test_failed_model_stage_is_recorded_without_provider_secret(self):
        edition,asset,revision=self.document('模型失败记录','模型调用失败财政赋税')
        with r.database.transaction(self.root) as connection:
            r.execute(connection,"UPDATE kb_indexes SET model_key='failure-test',state='ready' WHERE id=(SELECT active_index FROM kb_editions WHERE id=:id)",id=edition)
        with patch('backend.knowledge.retrieval.embed',side_effect=ValueError('private-provider-secret')):
            response=self.client.post('/api/knowledge/search',json={'query':'财政赋税','mode':'semantic','scope':{'edition_ids':[edition]}})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private-provider-secret',response.text)
        traces=self.checked(self.client.get('/api/knowledge/retrieval-runs'))
        self.assertEqual(traces[0]['diagnostics']['failed_stage'],'semantic')
        self.assertEqual(traces[0]['diagnostics']['outcome'],'failed')
        import json
        self.assertNotIn('private-provider-secret',json.dumps(traces))

    def test_search_snapshot_does_not_mix_concurrent_revisions(self):
        edition,asset,revision=self.document('并发修订','并发检索财政赋税旧文')
        original=r.rows;changed=False
        def concurrent(connection,statement,**values):
            nonlocal changed
            if 'SELECT b.id block_id' in statement and not changed:
                changed=True
                with r.database.transaction(self.root) as writer:
                    publish(writer,edition,[{'asset_id':asset,'page_index':2,'body':'并发修改的新原文'}])
            return original(connection,statement,**values)
        with patch.object(r,'rows',side_effect=concurrent):
            found=self.checked(self.client.post('/api/knowledge/search',json={'query':'财政赋税','mode':'exact','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}}))
        self.assertTrue(changed);self.assertEqual(len(found['results']),1)
        self.assertEqual(found['results'][0]['revision_id'],revision)
        trace=self.checked(self.client.get('/api/knowledge/retrieval-runs/'+found['retrieval_run_id']))
        self.assertEqual(trace['snapshot'][0]['revision_id'],revision)


if __name__=='__main__':unittest.main()
