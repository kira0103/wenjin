"""Reranking and parent context must preserve scope, evidence and fallback semantics."""
import json
import os
from pathlib import Path
from time import perf_counter
import unittest
from unittest.mock import patch

from backend.knowledge import repository as r
from backend.knowledge import reranking
from backend.knowledge.schemas import RetrievalOptions
from backend.knowledge.parent_context import window


class RerankRules(unittest.TestCase):
    def test_preparing_another_model_cannot_change_embedding_identity(self):
        import tempfile
        from types import SimpleNamespace
        from backend.knowledge import models
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cache=root/'models';directory=cache/'embedding';directory.mkdir(parents=True)
            (directory/'weights.onnx').write_bytes(b'fixed-embedding-weights')
            (directory/'tokenizer.json').write_text('{}',encoding='utf-8')
            instance=SimpleNamespace(model=SimpleNamespace(_model_dir=directory))
            with patch.dict(os.environ,{'SHIXUE_KB_MODEL_CACHE':str(cache)}),patch('fastembed.TextEmbedding',return_value=instance):
                _,original=models.embedding_model(root)
                models._models.pop(str(cache.resolve()))
                neighbor=cache/'reranker';neighbor.mkdir();(neighbor/'weights.onnx').write_bytes(b'unrelated-new-weights')
                (neighbor/'tokenizer.json').write_text('{"other":true}',encoding='utf-8')
                _,after=models.embedding_model(root)
                models._models.pop(str(cache.resolve()))
            self.assertEqual(original,after)

    def test_parent_window_contains_long_hit_when_budget_allows(self):
        body='前'*120+'𠀀'*400+'后'*400
        start,end,text=window(body,120,520,500)
        self.assertLessEqual(start,120);self.assertGreaterEqual(end,520)
        self.assertEqual(text,body[start:end]);self.assertLessEqual(len(text),500)

    def test_bad_scores_fall_back_without_mutating_original_order(self):
        items=[{'block_id':'a','title':'甲','body':'财政','rank':1},{'block_id':'b','title':'乙','body':'制度','rank':2}]
        options=RetrievalOptions(rerank_enabled=True).model_dump()
        for values in ([1.0],[float('nan'),2.0],[float('inf'),2.0]):
            with patch.object(reranking,'score',return_value=(values,{})):
                result,diag=reranking.apply(Path('.'),'问题',items,options,'hybrid')
            self.assertIs(result,items);self.assertEqual(diag['outcome'],'model_error')
            self.assertNotIn('rerank_score',items[0])

    def test_exact_bypass_and_successful_threshold_do_not_use_error_fallback(self):
        items=[{'block_id':'a','title':'甲','body':'财政','rank':1},{'block_id':'b','title':'乙','body':'制度','rank':2}]
        options=RetrievalOptions(rerank_enabled=True,rerank_min_score=0).model_dump()
        with patch.object(reranking,'score') as provider:
            result,diag=reranking.apply(Path('.'),'财政',items,options,'exact')
        provider.assert_not_called();self.assertIs(result,items);self.assertEqual(diag['outcome'],'exact_bypass')
        with patch.object(reranking,'score',return_value=([-4.0,-2.0],{'model_key':'fixture'})):
            result,diag=reranking.apply(Path('.'),'问题',items,options,'hybrid')
        self.assertEqual(result,[]);self.assertEqual(diag['outcome'],'below_threshold')

    def test_search_never_prepares_or_downloads_missing_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'SHIXUE_KB_RERANK_CACHE':folder}),patch.object(reranking,'prepare') as download:
            items=[{'block_id':'a','title':'甲','body':'财政','rank':1}]
            found,diag=reranking.apply(Path(folder),'财政',items,RetrievalOptions(rerank_enabled=True).model_dump(),'keyword')
        download.assert_not_called();self.assertIs(found,items);self.assertEqual(diag['outcome'],'model_not_ready')


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class ParentRerankNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_retrieval_quality as fixture
        fixture.QualityNativeTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests import test_retrieval_quality as fixture
        fixture.QualityNativeTests.tearDownClass.__func__(cls)

    def setUp(self):
        from tests import test_retrieval_quality as fixture
        fixture.QualityNativeTests.setUp(self)
        from backend.knowledge import retrieval_settings
        state=retrieval_settings.read(self.root)
        retrieval_settings.save(self.root,{'version':state['version'],'options':RetrievalOptions().model_dump()})

    def tearDown(self):
        from tests import test_retrieval_quality as fixture
        fixture.QualityNativeTests.tearDown(self)

    def document(self,*args):
        from tests import test_retrieval_quality as fixture
        return fixture.QualityNativeTests.document(self,*args)

    def checked(self,response):
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def query(self,edition,query,**kwargs):
        return self.checked(self.client.post('/api/knowledge/search',json={'query':query,'mode':'exact','scope':{'edition_ids':[edition]},**kwargs}))

    def test_parent_context_crosses_allowed_pages_with_independent_anchors(self):
        from backend.knowledge.retrieval import answer
        edition,asset,revision=self.document('父子跨页','主命中𠀀句')
        query={'query':'主命中','mode':'exact','scope':{'edition_ids':[edition]}}
        found=self.query(edition,'主命中');parts=found['results'][0]['parent_context']['parts']
        self.assertEqual([part['page_index'] for part in parts],[1,2,3])
        self.assertTrue(all(part['revision_id']==revision and part['asset_id']==asset for part in parts))
        reply=answer(self.root,query,lambda *args:None,{})
        self.assertEqual({item['locator']['page_index'] for item in reply['evidence']},{1,2,3})
        self.assertEqual(reply['evidence'][0]['quote'],'主命中𠀀句')
        for source in reply['evidence']:
            with r.database.transaction(self.root) as connection:
                anchor=r.one(connection,'SELECT * FROM kb_anchors WHERE id=:id',id=source['anchor_id'])
                block=r.one(connection,'SELECT body FROM kb_blocks WHERE id=:id',id=anchor['block_id'])
            self.assertEqual(source['quote'],block['body'][anchor['start_offset']:anchor['end_offset']])

    def test_parent_expansion_stops_at_project_page_boundaries(self):
        from backend import app as service
        from backend.knowledge.project_sources import ProjectSourceService
        edition,asset,revision=self.document('父子范围限制','唯一命中𠀀原文')
        ProjectSourceService(self.root,service.connection).put('project-main',edition,{'roles':['reference'],'mode':'ranges',
            'ranges':[{'asset_id':asset,'page_from':2,'page_to':2}]})
        found=self.checked(self.client.post('/api/knowledge/search',json={'query':'唯一命中','mode':'exact','scope':{'project_id':'project-main','edition_ids':[edition]}}))
        self.assertEqual([part['page_index'] for part in found['results'][0]['parent_context']['parts']],[2])
        self.assertEqual({boundary['reason'] for boundary in found['results'][0]['parent_context']['boundaries']},{'scope_boundary'})
        reply=self.checked(self.client.post('/api/knowledge/ask',json={'query':'唯一命中','mode':'exact','scope':{'project_id':'project-main','edition_ids':[edition]}}))
        self.assertTrue(all(item['locator']['page_index']==2 for item in reply['evidence']))

    def test_parent_stops_at_kind_section_asset_and_revision_boundaries(self):
        from backend.knowledge.ingestion import publish
        edition,asset,revision=self.document('边界测试','边界主命中')
        other,other_asset,_=self.document('其他版本与附件','边界主命中𠀀')
        with r.database.transaction(self.root) as connection:
            r.execute(connection,"INSERT INTO kb_edition_assets(edition_id,asset_id,filename) VALUES(:e,:a,'另一附件.txt') ON CONFLICT DO NOTHING",e=edition,a=other_asset)
        for boundary,first,last in (
            ('kind_boundary',{'kind':'commentary'},{'kind':'reference'}),
            ('section_boundary',{'section_path':'另一篇'},{'section_path':'另一篇'}),
            ('asset_boundary',{'asset_id':other_asset},{'asset_id':other_asset})):
            with self.subTest(boundary=boundary),r.database.transaction(self.root) as connection:
                pages=[{'asset_id':asset,'page_index':1,'body':'前文',**first},
                       {'asset_id':asset,'page_index':2,'body':'边界主命中'},
                       {'asset_id':asset,'page_index':3,'body':'后文',**last}]
                current=publish(connection,edition,pages)
            found=self.query(edition,'边界主命中');item=found['results'][0]
            self.assertEqual(len(item['parent_context']['parts']),1)
            self.assertEqual({point['reason'] for point in item['parent_context']['boundaries']},{boundary})
            self.assertTrue(all(part['revision_id']==current['revision_id'] for part in item['parent_context']['parts']))

    def test_inline_heading_stops_context_when_directory_metadata_is_empty(self):
        from backend.knowledge.retrieval import answer
        edition,asset,revision=self.document('无目录古籍','第一章\n前章内容不应进入。\n第二章\n本章唯一命中𠀀及限定语。\n第三章\n后章内容不应进入。')
        found=self.query(edition,'唯一命中');parts=found['results'][0]['parent_context']['parts']
        self.assertEqual(len(parts),1)
        self.assertIn('第二章',parts[0]['body']);self.assertNotIn('前章内容',parts[0]['body']);self.assertNotIn('后章内容',parts[0]['body'])
        reply=answer(self.root,{'query':'唯一命中','mode':'exact','scope':{'edition_ids':[edition]}},lambda *args:None,{})
        self.assertTrue(all('前章内容' not in item['quote'] and '后章内容' not in item['quote'] for item in reply['evidence']))

    def test_rerank_applies_after_fusion_and_records_fallback_threshold_and_scores(self):
        first,_,_=self.document('甲排序','税赋来自官府财政')
        second,_,_=self.document('乙排序','税赋只是词语，本文讨论诗歌')
        query={'query':'税赋','mode':'keyword','scope':{'edition_ids':[first,second],'page_from':2,'page_to':2},
               'retrieval':RetrievalOptions(rerank_enabled=True).model_dump()}
        def rank(root,question,texts):return [5.0 if '官府' in text else -5.0 for text in texts],{'model_key':'fixed-fixture'}
        with patch.object(reranking,'score',side_effect=rank):found=self.checked(self.client.post('/api/knowledge/search',json=query))
        self.assertEqual(found['results'][0]['edition_id'],first);self.assertEqual(found['results'][0]['rerank_score'],5)
        trace=self.checked(self.client.get('/api/knowledge/retrieval-runs/'+found['retrieval_run_id']))
        stage=next(stage for stage in trace['diagnostics']['stages'] if stage['name']=='rerank')
        self.assertEqual(stage['model_key'],'fixed-fixture');self.assertEqual(stage['outcome'],'completed')
        with patch.object(reranking,'score',side_effect=ValueError('private-key-provider')):
            fallback=self.checked(self.client.post('/api/knowledge/search',json=query))
        self.assertEqual(len(fallback['results']),2);self.assertTrue(fallback['warnings']);self.assertNotIn('private-key-provider',json.dumps(fallback))
        with patch.object(reranking,'score',side_effect=rank):
            rejected=self.checked(self.client.post('/api/knowledge/search',json={**query,'retrieval':{**query['retrieval'],'rerank_min_score':6}}))
        self.assertEqual(rejected['results'],[])

    def test_configuration_is_versioned_and_evaluation_freezes_current_settings(self):
        from backend.knowledge.evaluation import queue_run
        state=self.checked(self.client.get('/api/knowledge/retrieval-settings'))
        updated=self.checked(self.client.put('/api/knowledge/retrieval-settings',json={'version':state['version'],
            'options':{**state['options'],'rerank_candidates':7}}))
        self.assertEqual(updated['version'],state['version']+1)
        self.assertEqual(self.client.put('/api/knowledge/retrieval-settings',json={'version':state['version'],'options':state['options']}).status_code,409)
        suite=self.checked(self.client.post('/api/knowledge/evaluation/suites',json={'name':'配置冻结'}))
        self.checked(self.client.post('/api/knowledge/evaluation/suites/'+suite['id']+'/cases',json={'search':{'query':'无结果问题','mode':'exact'},'expect_empty':True}))
        queued=queue_run(self.root,suite['id'])
        with r.database.transaction(self.root) as connection:
            run=r.one(connection,'SELECT case_snapshot FROM kb_eval_runs WHERE id=:id',id=queued['id'])
            r.execute(connection,"UPDATE kb_jobs SET state='cancelled' WHERE id=:id",id=queued['job_id'])
        self.assertEqual(run['case_snapshot'][0]['search']['retrieval']['rerank_candidates'],7)

    def test_restore_to_new_directory_preserves_configuration_and_trace_workspace(self):
        from backend.knowledge import retrieval_settings
        from backend.knowledge.backup import export_bundle,restore_managed_bundle,workspace_key
        from backend.knowledge.product_runtime import ProductDatabase
        from backend import app as service
        state=retrieval_settings.read(self.root)
        retrieval_settings.save(self.root,{'version':state['version'],'options':RetrievalOptions(rerank_candidates=9).model_dump()})
        edition,asset,revision=self.document('恢复父子配置','恢复记录唯一命中𠀀')
        found=self.query(edition,'唯一命中')
        with r.database.transaction(self.root) as connection:
            # A shared external DB can hold another workspace; never collapse its settings.
            r.execute(connection,"INSERT INTO kb_retrieval_settings(workspace_key,options) VALUES('foreign-workspace',CAST(:o AS jsonb)) ON CONFLICT DO NOTHING",o=r.j(RetrievalOptions(rerank_candidates=79).model_dump()))
        bundle=export_bundle(self.root);destination=self.root.parent/'恢复到新目录'
        restored=None
        try:
            restore_managed_bundle(self.root/bundle['storage_key'],destination)
            restored=ProductDatabase(destination,'internal');restored.start();self.assertFalse(restored.startup_error)
            self.assertEqual(retrieval_settings.read(destination)['options']['rerank_candidates'],9)
            with r.database.transaction(destination) as connection:
                trace=r.one(connection,"SELECT id FROM kb_retrieval_runs WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=found['retrieval_run_id'])
                self.assertEqual(str(trace['id']),found['retrieval_run_id'])
                self.assertEqual(r.one(connection,"SELECT options FROM kb_retrieval_settings WHERE workspace_key='foreign-workspace'")['options']['rerank_candidates'],79)
                self.assertEqual(r.one(connection,"SELECT count(*) n FROM kb_retrieval_settings WHERE workspace_key=:key",key=workspace_key(destination))['n'],1)
        finally:
            if restored:restored.stop()
            service.initialize(self.root)


@unittest.skipUnless(os.environ.get('SHIXUE_RERANK_REAL_TESTS')=='1','需要已准备的独立本地重排模型')
class RealRerankTests(unittest.TestCase):
    def test_real_multilingual_model_and_long_pair_diagnostics(self):
        started=perf_counter()
        scores,meta=reranking.score(Path('.local/rerank-proof'),'明代国家财政收入与赋税制度有什么关系？',[
            '明代赋税是国家财政收入的重要来源，征收和折银制度影响财政运行。',
            '这篇论文研究宋代诗歌的意象与修辞。','本文讨论现代计算机处理器的性能测试。'])
        self.assertGreater(scores[0],scores[1]);self.assertGreater(scores[0],scores[2])
        self.assertIn('@',meta['model_key']);self.assertEqual(meta['truncated_pairs'],0)
        long_scores,long_meta=reranking.score(Path('.local/rerank-proof'),'赋税制度',['赋税制度影响财政运行。'*1000])
        self.assertEqual(len(long_scores),1);self.assertEqual(long_meta['truncated_pairs'],1)
        output=Path('.local/rerank-context-20261013');output.mkdir(parents=True,exist_ok=True)
        (output/'real-model-result.json').write_text(json.dumps({'passed':True,'scores':scores,'metadata':meta,
            'long_pair':long_meta,'elapsed_seconds':perf_counter()-started},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':unittest.main()
