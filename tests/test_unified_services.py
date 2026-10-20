"""Isolated checks for the v4.1 data and execution contracts."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_knowledge_postgres import PostgresFoundationIntegration
from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.catalog import CatalogService
from backend.knowledge.execution import WorkingInputService, migrate, digest
from backend.knowledge.ingestion import publish, process_index, process_import
from backend.knowledge.project_sources import ProjectSourceService
from backend.knowledge.retrieval import search, occurrence_count


@unittest.skipUnless(os.environ.get('SHIXUE_KB_INTEGRATION') == '1', '需要独立 PostgreSQL 集成测试')
class UnifiedServices(PostgresFoundationIntegration):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workspace = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        r.database.dispose()
        cls.workspace.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.root = Path(self.workspace.name)
        service.initialize(self.root)
        with service.connection() as local:
            migrate(local)
        self.environment = patch.dict(os.environ, {
            'SHIXUE_KB_DATABASE_URL': self.app_url.render_as_string(hide_password=False),
            'SHIXUE_KB_MODEL_CACHE': str(Path(os.environ['APPDATA']) / 'shixue-workbench/models')})
        self.environment.start()
        self.catalog = CatalogService(self.root)
        self.projects = ProjectSourceService(self.root, service.connection)
        self.inputs = WorkingInputService(self.root, service.connection, service.utc_now)

    def tearDown(self):
        r.database.dispose()
        self.environment.stop()

    def document(self, title):
        edition = self.catalog.create({'title': title, 'item_type': 'ancient', 'label': '甲本'})
        assets = []
        pages = []
        with r.database.transaction(self.root) as connection:
            for number in range(2):
                body = ('第一页范围外材料\n财政赋税𠀀的第二页\n第三页范围外资料' + str(edition['id']) + str(number)).encode()
                h, key = r.store_file(self.root, 'sample.txt', body)
                asset = r.uid()
                r.execute(connection, 'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:h,:k,:s)',
                          id=asset, h=h, k=key, s=len(body))
                r.execute(connection, 'INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind,ordinal) '
                          'VALUES(:e,:a,:f,\'page\',:o)', e=edition['id'], a=asset, f='分册.txt', o=number)
                assets.append(asset)
                pages.extend({'asset_id': asset, 'page_index': index, 'body': text, 'quality': {'review_required': False}}
                             for index, text in enumerate(['范围外财政赋税', '财政赋税𠀀原文', '其他史料'], 1))
            result = publish(connection, edition['id'], pages)
        return str(edition['id']), assets, result['revision_id']

    def test_catalog_import_deduplicates_and_keeps_version_identity(self):
        first = self.catalog.create({'title': '目录一', 'item_type': 'ancient'})
        second = self.catalog.create({'work_id': first['work_id'], 'title': '目录一', 'item_type': 'ancient', 'label': '乙本'})
        job = self.catalog.import_file(first['id'], '原件.txt', b'same original')
        duplicate = self.catalog.import_file(first['id'], '别名.txt', b'same original')
        other = self.catalog.import_file(second['id'], '原件.txt', b'same original')
        self.assertEqual(str(job['id']), str(duplicate['id']))
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(job['asset_id'], other['asset_id'])
        self.assertNotEqual(str(job['id']), str(other['id']))

    def test_version_edit_is_independent_and_conflicts_are_rejected(self):
        first = self.catalog.create({'title': '共享作品', 'item_type': 'ancient', 'label': '甲本'})
        second = self.catalog.create({'work_id': first['work_id'], 'title': '共享作品', 'item_type': 'ancient', 'label': '乙本'})
        self.catalog.update_edition(first['id'], {'label': '修订甲本'}, 1)
        with r.database.transaction(self.root) as connection:
            current = r.edition(connection, second['id'])
        self.assertEqual(current['title'], '共享作品')
        self.assertEqual(current['label'], '乙本')
        with self.assertRaises(HTTPException) as context:
            self.catalog.update_edition(first['id'], {'label': '过期写入'}, 1)
        self.assertEqual(context.exception.status_code, 409)

    def test_project_range_filters_all_search_modes_and_counts(self):
        edition, assets, revision = self.document('范围检索')
        payload = {'mode': 'ranges', 'ranges': [{'asset_id': assets[1], 'page_from': 2, 'page_to': 2}],
                   'roles': ['reference'], 'witness_label': '乙册', 'scope_note': '只用第二页'}
        self.projects.put('project-main', edition, payload)
        scope = self.projects.resolved_scope('project-main')
        # Scope may contain earlier independent fixtures, so constrain this edition too.
        scope['edition_ids'] = [edition]
        process_index(self.root, {'edition_id': edition, 'revision_id': revision}, lambda: None, lambda *args: None)
        for mode in ('exact', 'keyword', 'semantic', 'hybrid'):
            found = search(self.root, {'query': '财政', 'mode': mode, 'scope': scope})['results']
            self.assertTrue(found, mode)
            self.assertTrue(all(str(item['asset_id']) == assets[1] and item['page_index'] == 2 for item in found), mode)
        count = occurrence_count(self.root, '财政', scope)
        self.assertEqual(count['count'], 1)

    def test_input_is_immutable_and_does_not_change_with_new_revision(self):
        edition, assets, revision = self.document('冻结输入')
        ranges = [{'asset_id': assets[0], 'page_from': 2, 'page_to': 2},
                  {'asset_id': assets[1], 'page_from': 2, 'page_to': 2}]
        self.projects.put('project-main', edition, {'mode': 'edition', 'roles': ['base']})
        original = self.inputs.catalog('project-main', edition, ranges)
        self.assertEqual(original['body'], '财政赋税𠀀原文\n\n财政赋税𠀀原文')
        repeated = self.inputs.catalog('project-main', edition, list(reversed(ranges)))
        self.assertEqual(original['id'], repeated['id'])
        self.assertEqual({item['asset_id'] for item in original['positions']}, set(assets))
        for item in original['positions']:
            self.assertEqual(original['body'][item['snapshot_start']:item['snapshot_end']], '财政赋税𠀀原文')
        with r.database.transaction(self.root) as connection:
            publish(connection, edition, [{'asset_id': assets[0], 'page_index': 2, 'body': '新的修订'}])
        self.assertEqual(self.inputs.get(original['id'])['body_digest'], digest(original['body']))
        with service.connection() as local:
            with self.assertRaises(sqlite3.IntegrityError):
                local.execute('UPDATE working_inputs SET body=? WHERE id=?', ('覆盖', original['id']))
            self.assertIsNone(local.execute('SELECT entry_id FROM library_materials WHERE material_id=?', (original['material_id'],)).fetchone())

    def test_removed_relationship_blocks_new_inputs_but_keeps_frozen_work(self):
        edition, assets, _ = self.document('移出资料')
        link = self.projects.put('project-main', edition, {'mode': 'edition', 'roles': ['base']})
        original = self.inputs.catalog('project-main', edition)
        self.projects.remove('project-main', link['id'])
        self.assertEqual(self.inputs.get(original['id'], 'project-main')['body'], original['body'])
        with self.assertRaises(HTTPException):
            self.inputs.catalog('project-main', edition)

    def test_project_archive_restore_and_sync_events_preserve_roles(self):
        edition, assets, _ = self.document('归档项目')
        self.projects.put('project-main', edition, {'roles': ['witness'], 'witness_label': '参校甲本'})
        with service.connection() as local:
            local.execute('UPDATE projects SET archived=1 WHERE id=\'project-main\'')
        self.projects.sync_events()
        with self.assertRaises(HTTPException):
            self.projects.put('project-main', edition, {'roles': ['base']})
        with service.connection() as local:
            local.execute('UPDATE projects SET archived=0 WHERE id=\'project-main\'')
        # The SQLite project state takes effect even before the event is processed.
        self.assertTrue(self.projects.allows('project-main', edition, [{'asset_id': assets[0], 'page_from': 1, 'page_to': 1}]))
        foreign_asset=self.document('范围额外')[1][0]
        self.assertFalse(self.projects.allows('project-main', edition, [{'asset_id': foreign_asset, 'page_from': 1, 'page_to': 1}]))
        self.projects.sync_events()
        current = next(item for item in self.projects.list('project-main') if str(item['edition_id']) == edition)
        self.assertEqual(current['roles'], ['witness'])
        self.assertEqual(current['witness_label'], '参校甲本')

    def test_snapshot_and_task_can_publish_in_one_sqlite_transaction(self):
        with self.assertRaises(RuntimeError):
            with service.connection() as local:
                local.execute('BEGIN IMMEDIATE')
                snapshot = self.inputs.text('project-main', '回滚测试原文', db=local)
                local.execute('INSERT INTO tasks '
                              '(id,project_id,task_type,title,instructions,status,input_json,created_at,updated_at,working_input_id) '
                              'VALUES(?,?,?,?,?,?,?,?,?,?)', ('rollback-task', 'project-main', 'summary', '测试', '', 'queued', '{}',
                                                           service.utc_now(), service.utc_now(), snapshot['id']))
                raise RuntimeError('模拟提交前中断')
        with service.connection() as local:
            self.assertIsNone(local.execute('SELECT id FROM tasks WHERE id=\'rollback-task\'').fetchone())
            self.assertIsNone(local.execute('SELECT id FROM working_inputs WHERE id=?', (snapshot['id'],)).fetchone())

    def test_adding_an_attachment_keeps_existing_manual_text(self):
        edition, assets, _ = self.document('保留人工修订')
        with r.database.transaction(self.root) as connection:
            publish(connection, edition, [{'asset_id': assets[0], 'page_index': 1, 'body': '人工修订𠀀',
                                          'kind': 'commentary', 'quality': {'review_required': False}}])
        job = self.catalog.import_file(edition, '新增分册.txt', '新册原文'.encode())
        process_import(self.root, {'edition_id': edition, 'asset_id': job['asset_id']}, lambda: None, lambda *args: None)
        with r.database.transaction(self.root) as connection:
            current = r.edition(connection, edition)
            blocks = r.rows(connection, 'SELECT body,kind,quality FROM kb_blocks WHERE revision_id=:r', r=current['active_revision'])
        self.assertTrue(any(item['body'] == '人工修订𠀀' and item['kind'] == 'commentary' and not item['quality']['review_required'] for item in blocks))

    def test_queued_task_executes_frozen_body_instead_of_reinterpreting_request(self):
        with patch.object(service, 'get_settings', return_value={'configured': True}), \
             patch.object(service, 'task_worker', lambda identifier: None):
            task=service.create_task(service.TaskCreate(project_id='project-main',task_type='summary',source_text='入队时的原文𠀀'))
        with service.connection() as local:
            row=local.execute('SELECT working_input_id,input_json FROM tasks WHERE id=?',(task['id'],)).fetchone()
            self.assertTrue(row['working_input_id'])
            import json
            request=json.loads(row['input_json']);request['source_text']='排队之后改过的请求文本'
            local.execute('UPDATE tasks SET input_json=? WHERE id=?',(json.dumps(request),task['id']))
        with patch.object(service,'read_model_settings',return_value={'model':'isolated-test','provider_name':'test'}), \
             patch.object(service,'generate_text',side_effect=lambda task,body,settings:body) as model:
            service.execute_workflow(task['id'])
        self.assertEqual(model.call_args.args[1],'入队时的原文𠀀')
        with service.connection() as local:
            result=local.execute('SELECT status,result FROM tasks WHERE id=?',(task['id'],)).fetchone()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['result'],'入队时的原文𠀀')

    def test_unified_api_and_project_search_use_the_same_scoped_relationship(self):
        from fastapi.testclient import TestClient
        edition,assets,_=self.document('统一接口范围')
        service.service.token='unified-api-test'
        with service.connection() as local:
            local.execute('INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) '
                          'ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value',
                          ('catalog-activation','{"state":"activated"}'))
        client=TestClient(service.app,headers={'X-App-Token':'unified-api-test'})
        try:
            linked=client.put(f'/api/knowledge/projects/project-main/sources/{edition}',json={
                'mode':'ranges','ranges':[{'asset_id':assets[1],'page_from':2,'page_to':2}],
                'roles':['reference'],'witness_label':'只用乙册'})
            self.assertEqual(linked.status_code,200,linked.text)
            listed=client.get('/api/knowledge/catalog?project_id=project-main').json()['items']
            self.assertTrue(any(item['id']==edition for item in listed))
            found=client.post('/api/knowledge/search',json={'query':'财政','mode':'exact',
                              'scope':{'project_id':'project-main','edition_ids':[edition]}})
            self.assertEqual(found.status_code,200,found.text)
            self.assertTrue(found.json()['results'])
            self.assertTrue(all(item['asset_id']==assets[1] and item['page_index']==2 for item in found.json()['results']))
            snapshot=client.post('/api/knowledge/working-inputs',json={'project_id':'project-main','edition_id':edition,
                'ranges':[{'asset_id':assets[1],'page_from':2,'page_to':2}]})
            self.assertEqual(snapshot.status_code,200,snapshot.text)
            self.assertEqual(snapshot.json()['body'],'财政赋税𠀀原文')
        finally:
            client.close()
            with service.connection() as local:
                local.execute("DELETE FROM app_settings WHERE setting_key='catalog-activation'")

    def test_archived_project_cannot_create_a_task_from_an_existing_snapshot(self):
        frozen=self.inputs.text('project-main','归档前冻结的原文')
        with service.connection() as local:
            local.execute("UPDATE projects SET archived=1 WHERE id='project-main'")
        try:
            with patch.object(service,'get_settings',return_value={'configured':True}):
                with self.assertRaises(HTTPException) as error:
                    service.create_task(service.TaskCreate(project_id='project-main',task_type='summary',working_input_id=frozen['id']))
            self.assertEqual(error.exception.status_code,409)
            self.assertEqual(self.inputs.get(frozen['id'])['body'],'归档前冻结的原文')
        finally:
            with service.connection() as local:
                local.execute("UPDATE projects SET archived=0 WHERE id='project-main'")
