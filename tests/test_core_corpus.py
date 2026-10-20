import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException
from backend.knowledge import core_corpus as core, repository as r


class CoreRules(unittest.TestCase):
    def test_visible_html_keeps_annotations_and_drops_formatting(self):
        original='<a id="p1"></a><span>學而</span><span>時習之</span><sub>注文𠀀</sub>\n<p>正文&amp;一</p><p>正文二</p><script>垃圾()</script>'
        value=core.readable_body(original)
        self.assertIn('學而時習之注文𠀀\n',value)
        self.assertIn('正文&一\n正文二',value)
        self.assertNotIn('垃圾',value)
        self.assertEqual(core.readable_body('古文 < 另說'),'古文 < 另說')

    def test_numbered_parts_need_matching_source_work_id(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder);parent=source/'子藏/类书/佩文韵府';parent.mkdir(parents=True)
            for number in ('000','001_01'):
                (parent/(number+'.md')).write_text("---\ntitle: 佩文韻府分卷\nadditional_info:\n  - 'ID: KR3k0059'\n---\n原文",encoding='utf-8')
            books,_=core.inventory(source)
            self.assertEqual(len(books),1)
            self.assertEqual(len(next(iter(books.values()))),2)
            (parent/'001_01.md').write_text('---\ntitle: 另一部书\n---\n原文',encoding='utf-8')
            self.assertEqual(len(core.inventory(source)[0]),2)

    def test_markdown_preserves_original_and_unicode_offsets(self):
        raw='\ufeff---\r\ntitle:\r\n  zh-hant: 測試書\r\nauthors: []\r\n---\r\n\r\n原文𠀀　標點。\r\n'.encode('utf-8')
        title,metadata,body,offset=core.parse_markdown(raw,'fallback')
        self.assertEqual(title,'測試書')
        self.assertEqual(raw.decode('utf-8-sig')[offset:],body)
        self.assertEqual(body,'\r\n原文𠀀　標點。\r\n')
        self.assertEqual(len(core.canonical(body)),len(body))
        self.assertIn('原文𠀀',core.canonical(body))

    def test_candidates_are_deterministic_and_contain_every_substring(self):
        text='孔子𠀀曰：學而時習之。100%_\\'
        whole=set(core.terms(text))
        for length in (1,2,3,6):
            for start in range(len(text)-length+1):
                self.assertTrue(set(core.terms(text[start:start+length]))<=whole)
        self.assertEqual(core.terms(text),core.terms(text))


@unittest.skipUnless(os.environ.get('SHIXUE_MANAGED_TESTS')=='1','needs native PostgreSQL')
class CoreNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.knowledge.product_runtime import ProductDatabase
        parent=Path('.local/core-corpus-tests');parent.mkdir(parents=True,exist_ok=True)
        cls.folder=tempfile.TemporaryDirectory(dir=parent.resolve())
        cls.base=Path(cls.folder.name);cls.root=cls.base/'data'
        cls.product=ProductDatabase(cls.root,'internal');cls.product.start()
        if cls.product.startup_error:raise RuntimeError(cls.product.startup_error)

    @classmethod
    def tearDownClass(cls):
        cls.product.stop();cls.folder.cleanup()

    def fixture(self,name,body,root_body='目錄：卷一',second=''):
        source=self.base/name
        book=source/'史藏/正史/測試書.md';book.parent.mkdir(parents=True,exist_ok=True)
        book.write_text('---\ntitle:\n  zh-hant: 測試書\nauthors:\n  - name: 某人\n    dynasty: 宋\n---\n'+root_body,encoding='utf-8')
        part=book.with_suffix('')/'卷001.md';part.parent.mkdir()
        part.write_text('---\ntitle: 測試書 卷一\n---\n'+body,encoding='utf-8')
        if second:
            other=source/'儒藏/另一書.md';other.parent.mkdir();other.write_text('---\ntitle: 另一書\n---\n'+second,encoding='utf-8')
        (source/'.sources').mkdir();(source/'.sources/不應導入.md').write_text('備份',encoding='utf-8')
        return source,part

    def test_import_resume_exact_pagination_boundary_and_anchors(self):
        # The query spans an artificial passage cut and uses an astral character.
        phrase='學而時習𠀀之'
        body='甲'*(core.PRIMARY-3)+phrase+'。'+phrase+phrase+'100%_\\'
        source,part=self.fixture('boundary',body,second='別部'+phrase)
        original=hashlib.sha256(part.read_bytes()).hexdigest()
        result=core.process_import(self.root,{'source_path':str(source),'slug':'boundary'})
        self.assertEqual((result['books'],result['files']),(2,3))
        self.assertEqual(result['failed_books'],0)
        book=core.books(self.root,'測試書',corpus_id=result['corpus_id'])['items'][0]
        payload={'query':phrase,'mode':'literal','edition_id':book['edition_id'],'limit':1}
        found=[];cursor=None
        while True:
            response=core.search(self.root,**payload,cursor=cursor)
            found+=response['items'];cursor=response['next_cursor']
            if not cursor:break
        self.assertEqual(len(found),3)
        self.assertEqual(len({(x['source_path'],x['source_start']+x['start_offset']) for x in found}),3)
        self.assertEqual(core.search(self.root,query=core.canonical(phrase),mode='exact',edition_id=book['edition_id'])['items'][0]['quote'],phrase)
        self.assertFalse(core.search(self.root,query=core.canonical(phrase),mode='literal',edition_id=book['edition_id'])['items'])
        self.assertEqual(len(core.search(self.root,query='100%_\\',mode='literal',edition_id=book['edition_id'])['items']),1)
        self.assertFalse(core.search(self.root,query=phrase,categories=['医藏'])['items'])
        with r.database.transaction(self.root) as c:
            first=found[0];anchor=r.anchor(c,first['block_id'],first['start_offset'],first['end_offset'])
            self.assertEqual(anchor['quote'],phrase)
            old=r.execute(c,'SELECT count(*) FROM kb_revisions').scalar()
        repeated=core.process_import(self.root,{'source_path':str(source),'slug':'boundary'})
        self.assertEqual(repeated['skipped'],2)
        with r.database.transaction(self.root) as c:self.assertEqual(old,r.execute(c,'SELECT count(*) FROM kb_revisions').scalar())
        self.assertEqual(original,hashlib.sha256(part.read_bytes()).hexdigest())

    def test_failed_book_is_atomic_and_update_keeps_old_citations(self):
        source,part=self.fixture('updates','甲乙舊文')
        core.process_import(self.root,{'source_path':str(source),'slug':'updates'})
        with r.database.transaction(self.root) as c:
            book=r.one(c,"SELECT cb.* FROM kb_core_books cb JOIN kb_corpora cp ON cp.id=cb.corpus_id WHERE cp.slug='updates'")
        found=core.search(self.root,query='甲乙舊文',edition_id=book['edition_id'])['items'][0]
        with r.database.transaction(self.root) as c:anchor=r.anchor(c,found['block_id'],found['start_offset'],found['end_offset'])
        part.write_text('---\ntitle: 新卷\n---\n新文甲乙',encoding='utf-8')
        core.process_import(self.root,{'source_path':str(source),'slug':'updates'})
        self.assertFalse(core.search(self.root,query='甲乙舊文',edition_id=book['edition_id'])['items'])
        with r.database.transaction(self.root) as c:self.assertEqual(r.one(c,'SELECT quote FROM kb_anchors WHERE id=:id',id=anchor['id'])['quote'],'甲乙舊文')
        part.write_bytes(b'---\ntitle: [broken\n---\ntext')
        result=core.process_import(self.root,{'source_path':str(source),'slug':'updates'})
        self.assertEqual(result['failed_books'],1)
        self.assertEqual(len(core.search(self.root,query='新文甲乙',edition_id=book['edition_id'])['items']),1)

    def test_false_candidates_do_not_truncate_recall_or_rag_diversity(self):
        from backend.knowledge.retrieval import search
        phrase='甲乙丙'
        body=phrase*200+'甲'*(core.PRIMARY*130-len(phrase)*200)+phrase+'末尾罕見句𪚥'
        source,_=self.fixture('candidates',body)
        result=core.process_import(self.root,{'source_path':str(source),'slug':'candidates'})
        book=core.books(self.root,corpus_id=result['corpus_id'])['items'][0]
        with r.database.transaction(self.root) as c:
            # Simulate coarse-index collisions before the actual last hit.
            r.execute(c,'UPDATE kb_core_passages SET terms=terms||CAST(:terms AS integer[]) WHERE edition_id=:e',
                      e=book['edition_id'],terms=core.terms('末尾罕見句'))
        found=core.search(self.root,query='末尾罕見句',edition_id=book['edition_id'],limit=5)
        self.assertEqual(len(found['items']),1)
        self.assertFalse(found['next_cursor'])
        self.assertEqual(len(core.search(self.root,query='𪚥',edition_id=book['edition_id'],limit=5)['items']),1)
        rag=search(self.root,{'query':phrase,'mode':'exact','scope':{'edition_ids':[str(book['edition_id'])]},'source_kinds':['document'],'limit':5})
        self.assertGreaterEqual(len(rag['results']),2)

    def test_core_routes_project_rag_and_fixed_input_use_one_authority(self):
        from backend import app as service
        from backend.knowledge.activation import CatalogActivation
        from backend.knowledge.worker import worker
        from backend.knowledge.retrieval import search
        from backend.knowledge.execution import WorkingInputService
        from backend.knowledge.project_sources import ProjectSourceService
        from fastapi.testclient import TestClient
        import uuid
        worker.stop();service.initialize(self.root);service.service.token='core-test'
        CatalogActivation(self.root,service.connection).migrate()
        body='跨段原文𠀀'+('原字甲乙'*2000)+'尾部測試'
        source,part=self.fixture('project',body)
        result=core.process_import(self.root,{'source_path':str(source),'slug':'project'})
        book=core.books(self.root,corpus_id=result['corpus_id'])['items'][0]
        with TestClient(service.app,headers={'X-App-Token':'core-test'}) as client:
            manifest=client.get('/api/knowledge/core/editions/'+str(book['edition_id']))
            self.assertEqual(manifest.status_code,200,manifest.text)
            file=next(x for x in manifest.json()['files'] if x['source_path'].endswith('卷001.md'))
            self.assertTrue(client.get('/api/knowledge/editions/'+str(book['edition_id'])).json()['core'])
            self.assertEqual(client.post('/api/knowledge/core/search',json={'query':'跨段原文𠀀','edition_id':str(book['edition_id'])}).status_code,200)
            self.assertFalse(any(x['id']==str(book['edition_id']) for x in client.get('/api/knowledge/catalog').json()['items']))
            hit=search(self.root,{'query':'跨段原文𠀀','mode':'exact','scope':{'edition_ids':[str(book['edition_id'])]},'limit':5,'source_kinds':['document']})
            self.assertTrue(hit['results'])
            hit=search(self.root,{'query':'跨段原文𠀀','mode':'hybrid','scope':{'edition_ids':[str(book['edition_id'])]},'limit':5,'source_kinds':['document']})
            self.assertTrue(hit['results'])
            project='core-'+uuid.uuid4().hex
            with service.connection() as c:c.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(project,'古籍测试',service.utc_now()))
            ProjectSourceService(self.root,service.connection).put(project,str(book['edition_id']),{'mode':'ranges','roles':['reference'],'ranges':[{'asset_id':file['asset_id'],'page_from':1,'page_to':file['pages']}]})
            value=WorkingInputService(self.root,service.connection,service.utc_now).catalog(project,str(book['edition_id']),[{'asset_id':file['asset_id'],'page_from':1,'page_to':file['pages']}])
            self.assertEqual(value['body'],body)
            bootstrap=client.get('/api/bootstrap?project_id='+project)
            self.assertEqual(bootstrap.status_code,200,bootstrap.text)
            self.assertTrue(bootstrap.json()['materials'])
