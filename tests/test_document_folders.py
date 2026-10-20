import os,json,uuid,unittest,tempfile,hashlib
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from tests.test_research_assistant import ResearchNative as _Fixture
from backend.knowledge import repository as r
from backend.knowledge.document_library import DocumentLibrary,freeze_scope
from backend.knowledge.document_imports import DocumentImports,process_batch
from backend.knowledge.ingestion import process_import as parse_import
from backend.knowledge.retrieval import search
from backend import app as service


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class DocumentFoldersNative(unittest.TestCase):
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    document=_Fixture.document

    def source_folder(self):
        path=self.root/('sources-'+uuid.uuid4().hex);path.mkdir();return path

    def run_import(self,paths,name='',mode='directory',kind='article'):
        imports=DocumentImports(self.root);preview=imports.preview(mode,[str(p) for p in paths])
        started=imports.start(preview['id'],name,kind)
        result=process_batch(self.root,{'batch_id':preview['id']},lambda:None,lambda *a:None)
        with r.database.transaction(self.root) as db:
            jobs=r.rows(db,"SELECT payload FROM kb_jobs WHERE kind='import' AND state='queued'")
        for job in jobs:
            parse_import(self.root,job['payload'],lambda:None,lambda *a:None)
            with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_jobs SET state='succeeded' WHERE kind='import' AND payload->>'edition_id'=:id",id=job['payload']['edition_id'])
        return started,result

    def test_directory_paths_file_scope_folder_scope_and_literal_prefix(self):
        source=self.source_folder()
        for name,body in [('卷一/原文.txt','财政赋税所选文件'),('卷一/另一篇.txt','财政赋税同目录'),('卷一续/原文.txt','财政赋税目录范围外')]:
            file=source/name;file.parent.mkdir(exist_ok=True);file.write_text(body,encoding='utf-8')
        batch,result=self.run_import([source],uuid.uuid4().hex)
        self.assertEqual(result['succeeded'],3)
        library=DocumentLibrary(self.root);scope={'document_group_id':str(batch['group_id']),'include_core':True}
        root=library.browse(scope)
        self.assertEqual({d['name'] for d in root['directories']},{'卷一','卷一续'})
        rows=library.browse(scope,'卷一')['files'];self.assertEqual(len(rows),2)
        selected=next(e for e in rows if e['relative_path']=='卷一/原文.txt')
        payload={'query':'财政赋税','mode':'exact','source_kinds':['document'],'limit':20,'scope':{**scope,'document_entry_ids':[str(selected['id'])]}}
        hits=search(self.root,payload)['results'];self.assertTrue(hits)
        self.assertTrue(all(str(h['asset_id'])==str(selected['asset_id']) for h in hits))
        folder=search(self.root,{**payload,'scope':{**scope,'document_path':'卷一'}})['results']
        self.assertEqual(len(folder),2);self.assertNotIn('目录范围外',str(folder))
        with r.database.transaction(self.root) as db:frozen=freeze_scope(db,payload['scope'])
        # Moving a file later does not change a research run's already frozen originals.
        library.move({'document_entry_ids':[str(selected['id'])]},'',library.groups()['revision'])
        self.assertEqual(len(search(self.root,{**payload,'scope':frozen})['results']),1)

    def test_multiple_files_default_uncategorized_reimport_and_edit_group(self):
        source=self.source_folder();a=source/'甲.txt';b=source/'乙.txt';a.write_text('未分类财政赋税',encoding='utf-8');b.write_text('未分类研究',encoding='utf-8')
        first,result=self.run_import([a,b],mode='files');self.assertIsNone(first['group_id']);self.assertEqual(result['succeeded'],2)
        before=DocumentLibrary(self.root).groups()['total']
        second,result=self.run_import([a,b],name='类书研究'+uuid.uuid4().hex,mode='files')
        self.assertEqual(result['reused'],2);library=DocumentLibrary(self.root);self.assertEqual(library.groups()['total'],before)
        group=next(g for g in library.groups()['items'] if str(g['id'])==str(second['group_id']))
        library.rename(group['id'],'编辑后'+uuid.uuid4().hex,group['version'])
        with self.assertRaises(HTTPException):library.delete(group['id'],group['version'])
        library.delete(group['id'],group['version']+1)
        files=library.browse({'document_uncategorized':True},query='甲.txt')['files'];self.assertEqual(len(files),1)

    def test_core_reuse_directory_no_copy_and_selected_file_frozen_input(self):
        from backend.knowledge import core_corpus as core
        source=self.source_folder();(source/'儒藏/样本').mkdir(parents=True)
        (source/'儒藏/样本.md').write_text('---\ntitle: 目录样本\n---\n本书总目。',encoding='utf-8')
        (source/'儒藏/样本/卷一.md').write_text('---\ntitle: 卷一\n---\n财政赋税只选卷一。',encoding='utf-8')
        (source/'儒藏/样本/卷二.md').write_text('---\ntitle: 卷二\n---\n范围外卷二。',encoding='utf-8')
        core.process_import(self.root,{'source_path':str(source),'slug':uuid.uuid4().hex})
        # An rc.9 classification matching the corpus's default group must not
        # create a second entry for each original during the first migration.
        from backend.knowledge.literature_categories import LiteratureCategories
        legacy=LiteratureCategories(self.root)
        category=legacy.save({'name':'殆知阁核心古籍库','revision':legacy.tree()['revision']})
        with r.database.transaction(self.root) as db:
            works=r.execute(db,'SELECT e.work_id FROM kb_core_books cb JOIN kb_editions e ON e.id=cb.edition_id').scalars().all()
        legacy.assign({'work_ids':works,'category_ids':[category['id']],'mode':'add','revision':legacy.tree()['revision']})
        library=DocumentLibrary(self.root)
        migrated=next(g for g in library.groups()['items'] if g['name']=='殆知阁核心古籍库')
        self.assertEqual(migrated['files'],3)
        self.assertEqual(len(library.browse({'document_group_id':str(migrated['id'])},query='儒藏/')['files']),3)
        with r.database.transaction(self.root) as db:assets=r.execute(db,'SELECT count(*) FROM kb_assets').scalar_one()
        batch,result=self.run_import([source],'daizhige'+uuid.uuid4().hex,kind='ancient')
        self.assertTrue(result['reused']);self.assertFalse(result['originals_copied'])
        with r.database.transaction(self.root) as db:self.assertEqual(assets,r.execute(db,'SELECT count(*) FROM kb_assets').scalar_one())
        rows=library.browse({'document_group_id':str(batch['group_id'])},'儒藏/样本')['files']
        one=next(e for e in rows if e['relative_path'].endswith('卷一.md'))
        response=self.client.post(f"/api/knowledge/document-entries/{one['id']}/working-input?project_id={self.project}")
        self.assertEqual(response.status_code,200,response.text)
        value=response.json();self.assertIn('只选卷一',value['body']);self.assertNotIn('范围外卷二',value['body']);self.assertNotIn('本书总目',value['body'])

    def test_preview_changed_file_partial_retry_and_no_escape(self):
        source=self.source_folder();file=source/'变化.txt';file.write_text('最初文件',encoding='utf-8')
        imports=DocumentImports(self.root);preview=imports.preview('files',[str(file)])
        imports.start(preview['id'],'','article');file.write_text('已经发生变化',encoding='utf-8')
        result=process_batch(self.root,{'batch_id':preview['id']},lambda:None,lambda *a:None)
        self.assertEqual(result['failed'],1);self.assertEqual(imports.get(preview['id'])['state'],'partial')
        with self.assertRaises(HTTPException):DocumentLibrary(self.root).browse({},'../外部')
        response=self.client.post('/api/knowledge/document-entries/move',json={'scope':{},'category_name':'错误','revision':0})
        self.assertEqual(response.status_code,422)

    def test_transactional_upgrade_does_not_create_automatic_backup(self):
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge.managed import ManagedPostgres
        # Separate cluster: no changes to the class fixture or any user data.
        with tempfile.TemporaryDirectory(dir=self.root.parent) as name:
            root=Path(name)/'data';product=ProductDatabase(root,'internal')
            try:product.start();self.assertFalse(product.startup_error);service.initialize(root)
            finally:product.stop()
            manager=ManagedPostgres(root)
            try:
                manager.start()
                with manager.connect() as db:
                    db.execute('DROP TABLE kb_document_imports,kb_document_entries,kb_document_groups,kb_document_library_state')
                    db.execute("UPDATE kb_schema_version SET version_num='kb0011'")
            finally:manager.stop()
            frozen=hashlib.sha256((root/'library.sqlite').read_bytes()).hexdigest()
            product=ProductDatabase(root,'internal')
            with patch('backend.knowledge.activation.upgrade_backup',side_effect=AssertionError('不应自动备份')):
                try:
                    product.start();self.assertFalse(product.startup_error)
                    self.assertEqual(r.database.health(root)['schema_revision'],'kb0012')
                    self.assertEqual(hashlib.sha256((root/'library.sqlite').read_bytes()).hexdigest(),frozen)
                    self.assertFalse(list((root/'backups').glob('*.zip')))
                finally:product.stop()


del _Fixture
