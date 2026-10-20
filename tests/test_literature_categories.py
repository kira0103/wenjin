import os
import uuid
import unittest
from fastapi import HTTPException
from tests.test_research_assistant import ResearchNative as _Fixture
from backend.knowledge import repository as r
from backend.knowledge.catalog import CatalogService
from backend.knowledge.literature_categories import LiteratureCategories
from backend.knowledge.project_sources import ProjectSourceService
from backend.knowledge.retrieval import search
from backend.knowledge.research_assistant import inventory
from backend import app as service


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class LiteratureCategoriesNative(unittest.TestCase):
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    document=_Fixture.document

    def manager(self):return LiteratureCategories(self.root)

    def new(self,name=None,parent=None):
        return self.manager().save({'name':name or uuid.uuid4().hex,'parent_id':parent,'revision':self.manager().tree()['revision']})

    def assign(self,works,categories,mode='add'):
        return self.manager().assign({'work_ids':works,'category_ids':categories,'mode':mode,'revision':self.manager().tree()['revision']})

    def test_shared_tree_multiple_memberships_versions_and_deduplicated_parent_counts(self):
        parent=self.new();a=self.new(parent=parent['id']);b=self.new(parent=parent['id'])
        catalog=CatalogService(self.root)
        book=catalog.create({'title':'同名文献','item_type':'ancient','category_ids':[a['id'],b['id']]})
        other_version=catalog.create({'title':'同名文献','work_id':str(book['work_id']),'label':'第二版'})
        paper=catalog.create({'title':'同名文献','item_type':'article','category_ids':[b['id']]})
        counts=next(c for c in self.manager().tree()['items'] if str(c['id'])==parent['id'])
        self.assertEqual((counts['ancient'],counts['papers']),(1,1))
        listed=catalog.list(category_id=parent['id'],include_core=True)['items']
        self.assertEqual({str(v['id']) for v in listed},{str(book['id']),str(other_version['id']),str(paper['id'])})
        self.assertTrue(all(v['category_ids'] for v in listed))
        self.assertEqual(len(catalog.list(category_id=a['id'],kind='papers')['items']),0)
        self.assign([str(book['work_id'])],[a['id']],'remove')
        self.assertEqual(len(catalog.list(category_id=b['id'],kind='ancient')['items']),2)
        self.assign([str(book['work_id'])],[],'replace')
        self.assertEqual(len(catalog.list(category_id=parent['id'])['items']),1)
        self.assertIn(str(book['id']),{str(v['id']) for v in catalog.list(uncategorized=True,include_core=True)['items']})

    def test_move_depth_cycles_conflicts_duplicates_and_delete_preserves_documents(self):
        root=self.new();child=self.new(parent=root['id']);leaf=self.new(parent=child['id']);other=self.new()
        with self.assertRaises(HTTPException):self.new(parent=leaf['id'])
        version=self.manager().tree()['revision']
        for patch in [dict(root,parent_id=leaf['id']),dict(other,name=root['name'])]:
            with self.assertRaises(HTTPException):self.manager().save({**patch,'revision':version},patch['id'])
        moved=self.manager().save({**child,'parent_id':other['id'],'revision':version},child['id'])
        self.assertEqual(moved['parent_id'],other['id'])
        with self.assertRaises(HTTPException) as conflict:self.manager().save({**root,'revision':version},root['id'])
        self.assertEqual(conflict.exception.status_code,409)
        catalog=CatalogService(self.root);book=catalog.create({'title':'保留原件与版本','item_type':'ancient','category_ids':[leaf['id'],root['id']]})
        with self.assertRaises(HTTPException):self.manager().delete(other['id'],self.manager().tree()['revision'])
        self.manager().delete(other['id'],self.manager().tree()['revision'],True)
        self.assertEqual(len(catalog.list(category_id=root['id'])['items']),1)
        with r.database.transaction(self.root) as db:self.assertFalse(r.edition(db,book['id'])['deleted'])

    def test_search_parent_context_inventory_and_project_ranges_intersect_category(self):
        edition=self.edition
        with r.database.transaction(self.root) as db:work=str(r.edition(db,edition)['work_id'])
        parent=self.new();child=self.new(parent=parent['id']);empty=self.new()
        self.assign([work],[child['id']])
        ProjectSourceService(self.root,service.connection).put(self.project,self.edition,{'mode':'ranges','roles':['reference'],'ranges':[{'asset_id':self.asset,'page_from':2,'page_to':2}]})
        scoped={'include_core':True,'category_id':parent['id'],'_project_sources':[{'edition_id':edition,'mode':'ranges','ranges':[{'asset_id':self.asset,'page_from':2,'page_to':2}]}]}
        payload={'query':'财政赋税','mode':'exact','source_kinds':['document'],'scope':scoped,'limit':20}
        found=search(self.root,payload)['results']
        self.assertTrue(found);self.assertTrue(all(str(v['edition_id'])==edition and v['page_index']==2 for v in found))
        self.assertNotIn('范围外秘密',str(found))
        self.assertFalse(search(self.root,{**payload,'scope':{**scoped,'category_id':empty['id']}})['results'])
        self.assertFalse(search(self.root,{**payload,'scope':{**scoped,'category_id':None,'uncategorized':True}})['results'])
        with r.database.transaction(self.root) as db:
            self.assertEqual({str(v['id']) for v in inventory(db,scoped)},{edition})
        # API resolves project selected page ranges and retains the classification constraint.
        response=self.client.post('/api/knowledge/search',json={**payload,'scope':{'include_core':True,'category_id':parent['id'],'project_id':self.project}})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['results']);self.assertNotIn('范围外秘密',str(response.json()['results']))

    def test_api_atomic_import_and_workspace_isolation(self):
        category=self.new('导入分类'+uuid.uuid4().hex)
        response=self.client.post('/api/knowledge/catalog',json={'title':'导入即归类','item_type':'article','category_ids':[category['id']]})
        self.assertEqual(response.status_code,200,response.text)
        work=response.json()['work_id']
        self.assertEqual(len(self.manager().memberships([work])['items']),1)
        bad=self.client.post('/api/knowledge/catalog',json={'title':'不应留下半份书目','category_ids':[str(uuid.uuid4())]})
        self.assertEqual(bad.status_code,404,bad.text)
        self.assertFalse(CatalogService(self.root).list(query='不应留下半份书目')['items'])
        foreign=str(uuid.uuid4())
        with r.database.transaction(self.root) as db:
            r.execute(db,"INSERT INTO kb_literature_categories(id,workspace_key,name) VALUES(:id,'foreign-workspace','其他工作区分类')",id=foreign)
        self.assertFalse(CatalogService(self.root).list(category_id=foreign)['items'])
        with self.assertRaises(HTTPException):self.assign([work],[foreign])
        current=self.manager().tree()['revision']
        body={'work_ids':[work],'category_ids':[],'mode':'replace','revision':current}
        self.assertEqual(self.client.post('/api/knowledge/literature-memberships',json=body).status_code,200)
        self.assertEqual(self.client.post('/api/knowledge/literature-memberships',json=body).status_code,409)
        self.assertEqual(self.client.post('/api/knowledge/search',json={'query':'财政','scope':{'category_id':category['id'],'uncategorized':True}}).status_code,422)

    def test_core_original_search_follows_work_category_without_reindex(self):
        from backend.knowledge import core_corpus as core
        folder=self.root/('core-'+uuid.uuid4().hex);(folder/'儒藏').mkdir(parents=True)
        name='分类原文样本'+uuid.uuid4().hex
        (folder/'儒藏/样本.md').write_text('---\ntitle: '+name+'\n---\n學而時習之，不亦說乎。',encoding='utf-8')
        core.process_import(self.root,{'source_path':str(folder),'slug':uuid.uuid4().hex})
        catalog=CatalogService(self.root);book=catalog.list(query=name,include_core=True)['items'][0]
        category=self.new();self.assign([str(book['work_id'])],[category['id']])
        payload={'query':'學而時習之','mode':'keyword','source_kinds':['document'],'scope':{'include_core':True,'category_id':category['id']},'limit':10}
        self.assertTrue(search(self.root,payload)['results'])
        self.assertFalse(search(self.root,{**payload,'scope':{'include_core':True,'uncategorized':True}})['results'])
        self.assign([str(book['work_id'])],[category['id']],'remove')
        self.assertFalse(search(self.root,payload)['results'])
        self.assertTrue(search(self.root,{**payload,'scope':{'include_core':True,'uncategorized':True}})['results'])


del _Fixture
