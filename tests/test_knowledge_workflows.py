import io
import os
import time
import unittest
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image,ImageDraw,ImageFont
from psycopg import sql

from tests.test_knowledge_postgres import PostgresFoundationIntegration
from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.ingestion import process_import,process_index
from backend.knowledge.models import recognize
from backend.knowledge.worker import worker
from backend.knowledge.backup import export_bundle,verify_bundle,restore_bundle


@unittest.skipUnless(os.environ.get('SHIXUE_KB_INTEGRATION')=='1','需要独立 PostgreSQL 测试')
class KnowledgeWorkflows(PostgresFoundationIntegration):
    @classmethod
    def setUpClass(cls):
        super().setUpClass();cls.workspace=tempfile.TemporaryDirectory();cls.workspace_root=Path(cls.workspace.name)

    @classmethod
    def tearDownClass(cls):
        worker.stop()
        if worker.thread:worker.thread.join(30)
        super().tearDownClass();cls.workspace.cleanup()

    def setUp(self):
        worker.stop()
        if worker.thread:worker.thread.join(30)
        self.root=self.workspace_root
        service.initialize(self.root);service.service.token='workflow-test'
        self.environment=patch.dict(os.environ,{
            'SHIXUE_KB_DATABASE_URL':self.app_url.render_as_string(hide_password=False),
            'SHIXUE_KB_MODEL_CACHE':str(Path(os.environ['APPDATA'])/'shixue-workbench'/'models')})
        self.environment.start();self.client=TestClient(service.app,headers={'X-App-Token':'workflow-test'})

    def tearDown(self):
        worker.stop()
        if worker.thread:worker.thread.join(30)
        r.database.dispose();self.client.close();self.environment.stop()

    def create(self,title,body):
        response=self.client.post('/api/knowledge/editions',json={'title':title,'item_type':'ancient','label':'测试版本'})
        self.assertEqual(response.status_code,200,response.text);identifier=response.json()['id']
        data=body.encode();digest,key=r.store_file(self.root,'test.txt',data)
        with r.database.transaction(self.root) as connection:
            asset=r.uid()
            existing=r.execute(connection,'SELECT id FROM kb_assets WHERE sha256=:h',h=digest).scalar()
            if existing:asset=existing
            else:r.execute(connection,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:h,:k,:s)',id=asset,h=digest,k=key,s=len(data))
            r.execute(connection,'INSERT INTO kb_edition_assets(edition_id,asset_id,filename) VALUES(:e,:a,:f)',e=identifier,a=asset,f='test.txt')
        process_import(self.root,{'edition_id':identifier},lambda:None,lambda *args:None)
        return self.client.get('/api/knowledge/editions/'+identifier).json()

    def query(self,query,ids,mode='hybrid'):
        result=self.client.post('/api/knowledge/search',json={'query':query,'mode':mode,'scope':{'edition_ids':ids}})
        self.assertEqual(result.status_code,200,result.text);return result.json()['results']

    def test_exact_search_crosses_chunks_and_scope_is_enforced(self):
        first=self.create('原句测试','甲'*97+'跨界原句𠀀乙税赋制度'+'丙'*80)
        second=self.create('其他版本','跨界原句𠀀乙税赋制度')
        results=self.query('跨界原句𠀀乙税赋制度',[first['id']],'exact')
        self.assertEqual(len(results),1);self.assertEqual(results[0]['edition_id'],first['id'])
        count=self.client.post('/api/knowledge/occurrences',json={'query':'税赋','scope':{'edition_ids':[first['id']]}}).json()
        self.assertEqual(count['count'],1)

    def test_anchor_survives_new_revision_and_notes_reject_stale_writes(self):
        document=self.create('来源测试','甲𠀀乙原文')
        block=document['blocks'][0]
        anchor=self.client.post('/api/knowledge/anchors',json={'block_id':block['id'],'start':1,'end':3}).json()
        self.assertEqual(anchor['quote'],'𠀀乙')
        payload={'title':'判断笔记','body':'**阅读判断**','sources':[{'anchor_id':anchor['id'],'relation':'support'}]}
        note=self.client.post('/api/knowledge/notes',json=payload).json()
        self.assertEqual(self.client.patch('/api/knowledge/notes/'+note['id'],json={**payload,'version':1,'body':'更新'}).status_code,200)
        self.assertEqual(self.client.patch('/api/knowledge/notes/'+note['id'],json={**payload,'version':1}).status_code,409)
        result=self.client.post('/api/knowledge/editions/'+document['id']+'/revisions',json={'expected_revision':document['active_revision'],
                    'pages':[{'asset_id':document['files'][0]['id'],'page_index':1,'body':'人工校对的新文本','kind':'commentary','section_path':'卷一'}]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.client.get('/api/knowledge/anchors/'+anchor['id']).json()['quote'],'𠀀乙')
        self.assertEqual(self.client.get('/api/knowledge/notes/'+note['id']).json()['sources'][0]['relation'],'support')

    def test_topics_and_project_scope(self):
        document=self.create('专题文献','制度沿革')
        note=self.client.post('/api/knowledge/notes',json={'title':'专题笔记','body':'制度'}).json()
        topic=self.client.post('/api/knowledge/topics',json={'title':'制度史','question':'如何变化','note_ids':[note['id']],'edition_ids':[document['id']]}).json()
        self.assertEqual(len(topic['notes']),1);self.assertEqual(len(topic['editions']),1)
        self.client.put('/api/knowledge/editions/'+document['id']+'/project',json={'project_id':'project-main','roles':['reference']})
        found=self.client.post('/api/knowledge/search',json={'query':'制度','mode':'exact','scope':{'project_id':'project-main'}}).json()['results']
        self.assertTrue(any(item['edition_id']==document['id'] for item in found))
        self.client.delete('/api/knowledge/editions/'+document['id']+'/project/project-main')
        found=self.client.post('/api/knowledge/search',json={'query':'制度','mode':'exact','scope':{'project_id':'project-main'}}).json()['results']
        self.assertFalse(any(item['edition_id']==document['id'] for item in found))

    def test_versions_have_independent_publication_years(self):
        first=self.client.post('/api/knowledge/editions',json={'title':'版本年份测试','date_label':'作品成文年代','publication_year':2020}).json()
        second=self.client.post('/api/knowledge/editions',json={'title':'版本年份测试','work_id':first['work_id'],'publication_year':2023}).json()
        response=self.client.patch('/api/knowledge/editions/'+first['id'],json={'title':'版本年份测试','work_id':first['work_id'],'publication_year':2021,'date_label':'作品成文年代'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/knowledge/editions/'+second['id']).json()['publication_year'],2023)

    def test_manual_revision_requires_explicit_review_and_valid_original_page(self):
        document=self.create('核验状态测试','待核验原文')
        page={'source_block_id':document['blocks'][0]['id'],'asset_id':document['files'][0]['id'],'page_index':999,'body':'改后文本'}
        route='/api/knowledge/editions/'+document['id']+'/revisions'
        self.assertEqual(self.client.post(route,json={'expected_revision':document['active_revision'],'pages':[page]}).status_code,404)
        page['page_index']=1
        result=self.client.post(route,json={'expected_revision':document['active_revision'],'pages':[page]})
        self.assertEqual(result.status_code,200,result.text)
        current=self.client.get('/api/knowledge/editions/'+document['id']).json()
        self.assertTrue(current['blocks'][0]['quality']['review_required'])

    def test_durable_worker_import_and_duplicate_are_idempotent(self):
        document=self.client.post('/api/knowledge/editions',json={'title':'后台导入测试'}).json()
        route='/api/knowledge/editions/'+document['id']+'/imports?filename=worker.txt'
        imported=self.client.post(route,content='后台任务正文'.encode())
        self.assertEqual(imported.status_code,200,imported.text)
        job=imported.json()
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            state=self.client.get('/api/knowledge/jobs/'+job['id']).json()
            if state['state'] in {'succeeded','failed'}:break
            time.sleep(.05)
        self.assertEqual(state['state'],'succeeded',state)
        duplicate=self.client.post(route,content='后台任务正文'.encode()).json()
        self.assertTrue(duplicate['duplicate']);self.assertEqual(duplicate['id'],job['id'])
        blocks=self.client.get('/api/knowledge/editions/'+document['id']).json()['blocks']
        self.assertEqual(blocks[0]['body'],'后台任务正文')

    def test_streaming_validates_final_evidence(self):
        document=self.create('流式证据','赋税是官府收入的来源。')
        from backend.knowledge.streaming import events
        from contextlib import nullcontext
        import json
        class Response:
            def raise_for_status(self):pass
            def close(self):pass
            def iter_lines(self):
                for value in ('收入来自赋税。','[E1]'):
                    yield 'data: '+json.dumps({'choices':[{'delta':{'content':value}}]})
                yield 'data: [DONE]'
        with patch('backend.knowledge.streaming.httpx.stream',return_value=nullcontext(Response())),patch('backend.knowledge.streaming.keyring.get_password',return_value=None):
            output=''.join(events(self.root,{'query':'赋税','mode':'exact','scope':{'edition_ids':[document['id']]}},{'base_url':'http://127.0.0.1','model':'test'}))
        self.assertIn('"type": "delta"',output);self.assertIn('"type": "complete"',output)
        self.assertNotIn('"type": "error"',output)

    def test_real_local_embeddings_support_semantic_retrieval(self):
        taxes=self.create('财政研究','古代国家征收赋税，用于财政支出和官府运作。')
        poems=self.create('诗歌研究','山水诗表达诗人对自然景色和田园生活的感受。')
        for document in (taxes,poems):process_index(self.root,{'edition_id':document['id']},lambda:None,lambda *args:None)
        results=self.query('政府的收入来自税收',[taxes['id'],poems['id']],'semantic')
        self.assertTrue(results);self.assertEqual(results[0]['edition_id'],taxes['id'])

    def test_real_ocr_reads_chinese_scan(self):
        image=Image.new('RGB',(1000,220),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',48)
        draw.text((30,50),'明代财政制度与赋税改革',font=font,fill='black')
        buffer=io.BytesIO();image.save(buffer,format='PNG')
        body,quality=recognize(buffer.getvalue())
        self.assertIn('财政',body);self.assertTrue(quality['review_required'])

    def test_rag_rejects_fabricated_evidence_and_keeps_source_snapshot(self):
        document=self.create('问答证据','官府收入主要来自赋税。')
        endpoint='/api/knowledge/ask';payload={'query':'官府收入','mode':'keyword','scope':{'edition_ids':[document['id']]}}
        from backend.knowledge.retrieval import answer
        good=answer(self.root,payload,lambda *args:{'text':'资料记载官府收入来自赋税。[E1]'}, {'base_url':'http://127.0.0.1','model':'fixture'})
        self.assertTrue(good['evidence']);self.assertIn('id',good)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):answer(self.root,payload,lambda *args:{'text':'错误 [E999]'}, {'base_url':'local','model':'fixture'})
        empty=answer(self.root,{'query':'不存在的词','mode':'exact','scope':{'edition_ids':[document['id']]}},lambda *args:None,{})
        self.assertEqual(empty['evidence'],[])

    def test_bridge_creates_fixed_input_and_migration_preserves_original_ids(self):
        document=self.create('桥接文献','某古籍原文与注文。')
        bridge=self.client.post('/api/knowledge/editions/'+document['id']+'/bridge',json={'project_id':'project-main','roles':['base']})
        self.assertEqual(bridge.status_code,200,bridge.text)
        material=self.client.get('/api/materials/'+bridge.json()['material_id']).json()
        self.assertIn('固定输入快照',material['pages'][0]['text'])
        original=self.client.post('/api/materials/import?filename=legacy.txt&project_id=project-main',content='旧资料正文𠀀'.encode()).json()
        from backend.knowledge.legacy import migrate
        result=migrate(self.root,{'material_ids':[original['id']]},lambda:None,lambda *args:None)
        self.assertEqual(result['verified'],1)
        again=migrate(self.root,{'material_ids':[original['id']]},lambda:None,lambda *args:None)
        self.assertEqual(again['copied'],0)
        current=self.client.get('/api/materials/'+original['id'])
        self.assertEqual(current.status_code,200,current.text)
        self.assertEqual(current.json()['id'],original['id']);self.assertIn('knowledge_edition_id',current.json())
        self.assertEqual(self.client.put('/api/library-items/'+original['entry_id'],json={'title':'非法旧库修改'}).status_code,409)

    def test_bibliography_only_entry_is_migrated_without_claiming_fulltext(self):
        source=self.client.post('/api/library-items',json={'title':'仅书目旧条目','item_type':'article','authors':'某作者'}).json()
        from backend.knowledge.legacy import migrate
        migrate(self.root,{'material_ids':[]},lambda:None,lambda *args:None)
        with r.database.transaction(self.root) as connection:
            link=r.one(connection,'SELECT edition_id FROM kb_legacy_entries WHERE entry_id=:id',id=source['id'])
            item=r.edition(connection,link['edition_id'])
            self.assertIsNone(item['active_revision'])
        self.assertEqual(self.client.put('/api/library-items/'+source['id'],json={'title':'旧库覆盖'}).status_code,409)

    def test_bundle_is_verified_and_restores_to_new_database_and_directory(self):
        document=self.create('备份测试','备份原文')
        original=self.client.post('/api/materials/import?filename=snapshot.txt&project_id=project-main',content='需要移动的原件'.encode()).json()
        result=export_bundle(self.root)
        bundle=self.root/result['storage_key'];manifest=verify_bundle(bundle)
        self.assertNotIn('knowledge.json',manifest['files'])
        target='shixue_kb_test_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
        destination=self.root.parent/(self.root.name+'-restored')
        try:
            url=self.owner_url.set(username='shixue_admin',password=self.passwords['admin'],database=target)
            restored=restore_bundle(bundle,destination,url)
            self.assertTrue(restored['restored'])
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(destination/'library.sqlite')) as connection:
                asset=connection.execute('SELECT asset_path FROM materials WHERE id=?',(original['id'],)).fetchone()[0]
                self.assertTrue(Path(asset).is_file());self.assertTrue(destination.resolve() in Path(asset).resolve().parents)
            with patch.dict(os.environ,{'SHIXUE_KB_DATABASE_URL':self.app_url.set(database=target).render_as_string(hide_password=False)}):
                self.assertTrue(r.database.health(destination)['ready'])
                with r.database.transaction(destination) as connection:
                    self.assertEqual(r.edition(connection,document['id'])['title'],'备份测试')
        finally:
            r.database.dispose()
            self.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(target)))
            if destination.exists():
                import shutil
                shutil.rmtree(destination)


if __name__=='__main__':unittest.main()
