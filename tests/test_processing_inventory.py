"""Table filters and cross-page actions must select the same documents."""
import os,unittest
from unittest.mock import patch
from backend.knowledge import repository as r,bulk_processing as bulk

@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class InventoryNative(unittest.TestCase):
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

    def documents(self,prefix,count=125):
        items=self.seed(prefix,count)
        with r.database.transaction(self.root) as db:
            for index,item in enumerate(items):
                large=index<75
                job=r.queue(db,'index',{'edition_id':item['id'],'revision_id':item['revision_id'],'config':{'mode':'parent_child' if large else 'auto','size':300,'overlap':30},'embedding':{'provider':'local','model':'BAAI/bge-small-zh-v1.5' if large else 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','dimensions':512 if large else 384}})
                r.execute(db,"UPDATE kb_jobs SET state='failed',stage='embedding',result=CAST(:result AS jsonb),updated_at=now()+:seconds*interval '1 second' WHERE id=:id",id=job['id'],result=r.j({} if large else {'analysis':{'selected_mode':'recursive'}}),seconds=index)
        return items

    def test_filters_facets_and_sort_cover_all_pages(self):
        self.documents('字段筛选跨页')
        response=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'字段筛选跨页','state':'failed','chunk_mode':'parent_child','dimensions':512,'model':'BAAI/bge-small-zh-v1.5','limit':20,'sort':'updated_desc'}))
        self.assertEqual(response['total'],75);self.assertEqual(len(response['items']),20)
        self.assertEqual(response['facets']['dimensions'],[384,512])
        self.assertEqual(sum(v['total'] for v in response['facets']['models']),125)
        self.assertTrue(all(v['embedding_dimensions']==512 and v['chunk_mode']=='parent_child' for v in response['items']))
        self.assertTrue(response['items'][0]['title'].endswith('0074'))
        second=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'字段筛选跨页','chunk_mode':'parent_child','offset':60,'limit':20}))
        self.assertEqual(len(second['items']),15)
        recursive=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'字段筛选跨页','chunk_mode':'recursive'}))
        self.assertEqual(recursive['total'],50,'自动策略已记录的实际选择可被筛选')
        for params in ({'dimensions':0},{'chunk_mode':'invented'},{'source':'private'},{'sort':'DROP TABLE'}):
            self.assertEqual(self.client.get('/api/knowledge/rag-documents',params=params).status_code,422)

    def test_cross_page_bulk_freezes_the_exact_filtered_set(self):
        items=self.documents('字段筛选批量')
        criteria={'chunk_mode':'parent_child','dimensions':512,'model':'BAAI/bge-small-zh-v1.5','text_state':'present','source':'regular','item_type':'article'}
        listing=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'字段筛选批量',**criteria}))
        self.assertEqual(listing['total'],75)
        with patch.object(bulk.dispatcher,'start'):
            batch=self.checked(self.client.post('/api/knowledge/rag-process-all',json={'query':'字段筛选批量','state':'failed','filters':criteria,'exclude_ids':[items[0]['id']]}))
        self.assertEqual(batch['total'],74)
        with r.database.transaction(self.root) as db:
            payload=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=batch['id'])['payload']
            self.assertEqual({v['id'] for v in payload['targets']},{v['id'] for v in items[1:75]})
            self.assertEqual(payload['filters']['dimensions'],512)
        owner=self.own(batch['id']);bulk.dispatch_slice(self.root,batch['id'],owner);bulk.dispatch_slice(self.root,batch['id'],owner)
        with r.database.transaction(self.root) as db:
            actual=r.rows(db,"SELECT payload FROM kb_jobs WHERE payload->>'processing_batch'=:batch",batch=batch['id'])
            self.assertEqual(len(actual),74)
            self.assertEqual({v['payload']['edition_id'] for v in actual},{v['id'] for v in items[1:75]})

    def test_whole_library_explicitly_ignores_inventory_filters(self):
        self.documents('整库忽略字段',3)
        with patch.object(bulk.dispatcher,'start'):
            batch=self.checked(self.client.post('/api/knowledge/rag-process-all',json={'scope':'library','query':'没有的题名','filters':{'dimensions':1,'model':'nonexistent','source':'core'}}))
        self.assertGreaterEqual(batch['total'],3)
        with r.database.transaction(self.root) as db:
            payload=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=batch['id'])['payload']
            self.assertEqual(payload['filters']['dimensions'],None);self.assertEqual(payload['filters']['source'],'all')

    def test_missing_text_and_model_are_explicit_filter_values(self):
        with r.database.transaction(self.root) as db:r.create_edition(db,{'title':'空原文筛选夹具','item_type':'book'})
        result=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'空原文筛选夹具','text_state':'missing','model':'__unrecorded__','item_type':'book'}))
        self.assertEqual(result['total'],1);self.assertIsNone(result['items'][0]['embedding_model']);self.assertIsNone(result['items'][0]['chunks'])
        self.assertEqual(self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'空原文筛选夹具','text_state':'present'}))['total'],0)

    def test_cancelled_latest_task_is_separate_from_index_readiness(self):
        items=self.documents('独立任务状态',2)
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='cancelled' WHERE payload->>'edition_id'=:id",id=items[0]['id'])
        result=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'独立任务状态','job_state':'cancelled'}))
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['id'],items[0]['id']);self.assertEqual(result['items'][0]['job']['state'],'cancelled')
        failed=self.checked(self.client.get('/api/knowledge/rag-documents',params={'query':'独立任务状态','job_state':'failed'}))
        self.assertEqual(failed['total'],1);self.assertEqual(failed['items'][0]['id'],items[1]['id'])

if __name__=='__main__':unittest.main()
