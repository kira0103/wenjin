import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_knowledge_postgres import PostgresFoundationIntegration
from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.activation import CatalogActivation
from backend.knowledge.compat import alias
from backend.knowledge.worker import worker


@unittest.skipUnless(os.environ.get('SHIXUE_KB_INTEGRATION')=='1','需要独立 PostgreSQL 集成测试')
class CatalogActivationTests(PostgresFoundationIntegration):
    @classmethod
    def setUpClass(cls):pass
    @classmethod
    def tearDownClass(cls):pass

    def setUp(self):
        PostgresFoundationIntegration.setUpClass.__func__(type(self))
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        service.initialize(self.root);service.service.token='activation-test'
        self.env=patch.dict(os.environ,{'SHIXUE_KB_DATABASE_URL':self.app_url.render_as_string(hide_password=False),
            'SHIXUE_KB_MODEL_CACHE':str(Path(os.environ['APPDATA'])/'shixue-workbench/models')})
        self.env.start();self.client=TestClient(service.app,headers={'X-App-Token':'activation-test'})

    def tearDown(self):
        worker.stop()
        if worker.thread:worker.thread.join(30)
        self.client.close();r.database.dispose();self.env.stop()
        PostgresFoundationIntegration.tearDownClass.__func__(type(self));self.temp.cleanup()

    def legacy(self,missing=False,bad_hash=False):
        data='卷一\n财政赋税𠀀\n卷二\n山川地理'.encode();path=self.root/'assets/old.txt'
        if not missing:path.write_bytes(data)
        h='0'*64 if bad_hash else hashlib.sha256(data).hexdigest()
        with service.connection() as db:
            db.execute('INSERT INTO bibliography(id,title,authors,year,version_label,genre,created_at) VALUES(?,?,?,?,?,?,?)',
                       ('old-entry','古籍甲本','作者甲','明代','甲本','史料',service.utc_now()))
            db.execute('INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       ('old-material','project-main','old.txt','旧题名',h,str(path),2,1,service.utc_now()))
            db.execute('INSERT INTO library_materials(material_id,entry_id,file_format,position_kind) VALUES(?,?,?,?)',('old-material','old-entry','txt','section'))
            db.executemany('INSERT INTO pages(material_id,page_number,text) VALUES(?,?,?)',
                           [('old-material',1,'卷一\n财政赋税𠀀'),('old-material',2,'卷二\n山川地理')])
            db.execute('INSERT INTO project_materials(project_id,material_id,roles_json,witness_label,scope_note) VALUES(?,?,?,?,?)',
                       ('project-main','old-material','["base"]','甲本简称','只用本册'))

    def test_missing_original_has_visible_bibliography_and_legacy_text(self):
        self.legacy(missing=True)
        result=CatalogActivation(self.root,service.connection).migrate()
        self.assertTrue(result['active'],result)
        self.assertEqual(result['items'][0]['state'],'missing_original')
        books=self.client.get('/api/library-items').json();self.assertEqual(books[0]['title'],'古籍甲本')
        material=self.client.get('/api/materials/old-material').json()
        self.assertIn('财政',material['pages'][0]['text'])
        sources=self.client.get('/api/knowledge/projects/project-main/sources').json()
        self.assertEqual(sources[0]['witness_label'],'甲本简称')
        self.assertEqual(sources[0]['scope_note'],'只用本册')
        with service.connection() as db:
            with self.assertRaises(Exception):db.execute("UPDATE bibliography SET title='覆盖' WHERE id='old-entry'")

    def test_hash_conflict_prevents_global_activation_and_keeps_report(self):
        self.legacy(bad_hash=True)
        result=CatalogActivation(self.root,service.connection).migrate()
        self.assertFalse(result['active'])
        self.assertEqual(result['items'][0]['state'],'conflict')
        with service.connection() as db:
            self.assertEqual(db.execute("SELECT kind FROM materials WHERE id='old-material'").fetchone()[0],'catalog')

    def test_new_preparation_and_units_continue_from_frozen_input_after_unlink(self):
        self.legacy()
        result=CatalogActivation(self.root,service.connection).migrate();self.assertTrue(result['active'],result)
        material=self.client.get('/api/materials?project_id=project-main').json()[0]
        prepared=self.client.post('/api/projects/project-main/preprocess/split',json={'material_id':material['id']})
        self.assertEqual(prepared.status_code,200,prepared.text)
        run=prepared.json()
        reloaded=self.client.get('/api/projects/project-main/preprocess/runs',params={'material_id':material['id']}).json()
        self.assertEqual(reloaded[0]['id'],run['id'])
        sources=self.client.get('/api/knowledge/projects/project-main/sources').json()
        self.client.delete('/api/knowledge/projects/project-main/sources/'+sources[0]['id'])
        texts=self.client.get('/api/preprocess/runs/'+run['id']+'/texts').json()['items']
        unit=self.client.post('/api/preprocess/runs/'+run['id']+'/units',json={'text_ids':[texts[0]['id']]})
        self.assertEqual(unit.status_code,200,unit.text)
        self.assertTrue(unit.json())
        denied=self.client.post('/api/projects/project-main/preprocess/split',json={'material_id':material['id']})
        self.assertNotEqual(denied.status_code,200)

    def test_legacy_task_reads_archived_pages_after_current_revision_changes(self):
        from backend.knowledge.ingestion import publish
        self.legacy();result=CatalogActivation(self.root,service.connection).migrate();self.assertTrue(result['active'])
        with r.database.transaction(self.root) as pg:
            link=r.one(pg,"SELECT * FROM kb_legacy_links WHERE material_id='old-material'")
            publish(pg,link['edition_id'],[{'asset_id':str(link['asset_id']),'page_index':1,'body':'新修订不应进入旧任务'}],'manual')
        payload=service.TaskCreate(project_id='project-main',material_id='old-material',task_type='summary',page_from=1,page_to=2)
        with service.connection() as db:
            body,_=service.collect_source(db,payload,validate_unit_selection=False)
        self.assertIn('财政赋税𠀀',body);self.assertNotIn('新修订',body)

    def test_existing_project_links_and_forward_reference_survive_activation(self):
        self.legacy()
        with service.connection() as db:
            db.execute("INSERT INTO bibliography(id,title,created_at) VALUES('later','后续引用书目',?)",(service.utc_now(),))
            db.execute("INSERT INTO references_found(id,material_id,page_number,ordinal,raw_text,title,authors,year,match_id,match_status,created_at) VALUES('reference','old-material',1,1,'后续引用原文','后续引用书目','','','later','版本待核',?)",(service.utc_now(),))
        with r.database.transaction(self.root) as pg:
            doc=r.create_edition(pg,{'title':'0.4 既有 PostgreSQL 资料'})
            r.execute(pg,"INSERT INTO kb_project_editions(project_id,edition_id,roles) VALUES('project-main',:id,'[\"citation\"]')",id=doc['id'])
        result=CatalogActivation(self.root,service.connection).migrate();self.assertTrue(result['active'],result)
        with r.database.transaction(self.root) as pg:
            self.assertTrue(r.execute(pg,"SELECT matched_edition_id FROM kb_reference_candidates WHERE id='reference'").scalar())
            self.assertEqual(r.execute(pg,'SELECT roles FROM kb_project_sources WHERE edition_id=:id',id=doc['id']).scalar(),['citation'])

    def test_project_range_update_is_atomic_and_rejects_stale_version(self):
        self.legacy();self.assertTrue(CatalogActivation(self.root,service.connection).migrate()['active'])
        source=self.client.get('/api/knowledge/projects/project-main/sources').json()[0]
        path='/api/knowledge/projects/project-main/sources/'+source['id']
        payload={key:source[key] for key in ('version','mode','ranges','roles','witness_label','scope_note')}
        payload['ranges'][0]['page_to']=1
        changed=self.client.patch(path,json=payload);self.assertEqual(changed.status_code,200,changed.text)
        stale=self.client.patch(path,json=payload);self.assertEqual(stale.status_code,409)
        self.assertEqual(self.client.get('/api/knowledge/projects/project-main/sources').json()[0]['ranges'][0]['page_to'],1)
