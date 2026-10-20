import os
import json
import uuid
import unittest
from pathlib import Path
from tests.test_research_assistant import ResearchNative as _Fixture
from backend.knowledge import repository as r
from backend.knowledge.catalog import CatalogService
from backend.knowledge import core_corpus as core
from backend.knowledge.retrieval import search
from backend import app as service


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class LibraryLayoutNative(unittest.TestCase):
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    document=_Fixture.document
    queued=_Fixture.queued
    execute=_Fixture.execute
    fake=_Fixture.fake

    def test_catalog_classification_filters_and_legacy_preservation(self):
        catalog=CatalogService(self.root)
        name='分类测试'+uuid.uuid4().hex
        paper=catalog.create({'title':name,'item_type':'article','publication_year':2025,'tags':['地方史']})
        thesis=catalog.create({'title':name+'学位','item_type':'thesis'})
        legacy=catalog.create({'title':name+'档案','item_type':'archive'})
        papers=catalog.list(kind='papers',query=name)['items']
        self.assertEqual({str(p['id']) for p in papers},{str(paper['id']),str(thesis['id'])})
        self.assertEqual(str(catalog.list(kind='papers',query=name,year='2025',tag='地方史')['items'][0]['id']),str(paper['id']))
        unknown=catalog.list(kind='unclassified',query=name)['items']
        self.assertEqual(str(unknown[0]['id']),str(legacy['id']))
        self.assertFalse(catalog.list(kind='ancient',query=name,include_core=True)['items'])
        self.assertEqual(len(catalog.list(kind='papers',query=name,state='metadata')['items']),2)

    def test_whole_library_core_search_includes_unlinked_books_and_type_scope(self):
        folder=self.root/('source-'+uuid.uuid4().hex);(folder/'儒藏').mkdir(parents=True)
        phrase='未关联古籍独有词'+uuid.uuid4().hex[:8]
        (folder/'儒藏/全库样本.md').write_text('---\ntitle: 全库样本\n---\n'+phrase,encoding='utf-8')
        result=core.process_import(self.root,{'source_path':str(folder),'slug':uuid.uuid4().hex})
        self.assertEqual(result['books'],1)
        catalog=CatalogService(self.root)
        books=catalog.list(kind='ancient',source='core',include_core=True,query='全库样本')['items']
        self.assertTrue(books);self.assertTrue(books[0]['is_core'])
        query={'query':phrase,'mode':'keyword','source_kinds':['document'],'scope':{'include_core':True,'library_kind':'ancient'},'limit':10}
        self.assertTrue(search(self.root,query)['results'])
        self.assertFalse(search(self.root,{**query,'scope':{'include_core':True,'library_kind':'papers'}})['results'])
        self.assertFalse(search(self.root,{**query,'scope':{}})['results'])

    def test_verified_result_saved_to_project_once_with_citations_no_new_notes(self):
        run=self.queued()
        value=self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}]))
        self.assertTrue(value['verified'])
        with r.database.transaction(self.root) as db:notes=r.execute(db,'SELECT count(*) FROM kb_notes').scalar()
        body={'project_id':self.project,'kind':'research','run_id':str(run['id'])}
        result=self.client.post('/api/research-results',json=body)
        self.assertEqual(result.status_code,200,result.text)
        saved=result.json()
        duplicate=self.client.post('/api/research-results',json=body).json()
        self.assertTrue(duplicate['duplicate']);self.assertEqual(saved['artifact_id'],duplicate['artifact_id'])
        with service.connection() as db:
            artifact=db.execute('SELECT * FROM artifacts WHERE id=?',(saved['artifact_id'],)).fetchone()
            payload=json.loads(artifact['body']);self.assertIn('原文依据',payload['content']);self.assertTrue(payload['research_sources'])
            self.assertEqual(payload['research_run']['id'],str(run['id']))
        with r.database.transaction(self.root) as db:self.assertEqual(notes,r.execute(db,'SELECT count(*) FROM kb_notes').scalar())
        unfinished=self.queued()
        response=self.client.post('/api/research-results',json={**body,'run_id':str(unfinished['id'])})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.client.post('/api/research-results',json={**body,'run_id':'invalid'}).status_code,422)


del _Fixture
