import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from backend import app as service


class WorkbenchRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        service.initialize(self.root)
        service.service.token = 'test-token'
        self.client = TestClient(service.app, headers={'X-App-Token': 'test-token'})
        self.credentials = patch.object(service.keyring, 'get_password', return_value=None)
        self.credentials.start()
        with service.connection() as db:
            db.execute("INSERT INTO projects(id,name,description,created_at) VALUES('project-b','另一项目','',?)", (service.utc_now(),))
            db.execute("INSERT INTO app_settings VALUES('base_url','http://127.0.0.1:9999/v1')")
            db.execute("INSERT INTO app_settings VALUES('model','test-model')")
        self.add_material('material-a', 'project-main')
        self.add_material('material-b', 'project-b')

    def tearDown(self):
        for thread in list(service.service.tasks):
            thread.join(timeout=5)
        service.service.tasks.clear()
        self.credentials.stop()
        self.client.close()
        self.directory.cleanup()

    def add_material(self, material_id, project_id, texts=('原文内容', '', '另一页的原文')):
        with service.connection() as db:
            db.execute('INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (material_id, project_id, material_id + '.pdf', material_id, material_id, str(self.root / 'assets' / (material_id + '.pdf')), len(texts), .66, service.utc_now()))
            db.execute('INSERT INTO project_materials(project_id,material_id) VALUES(?,?)', (project_id,material_id))
            db.executemany('INSERT INTO pages VALUES(?,?,?)', [(material_id, i + 1, text) for i, text in enumerate(texts)])

    def payload(self, **values):
        return {'project_id': 'project-main', 'material_id': 'material-a', 'task_type': 'summary', 'page_from': 1, 'page_to': 3, **values}

    def test_scoped_bootstrap_has_all_materials_and_no_foreign_tasks(self):
        for i in range(48):
            self.add_material('extra-' + str(i), 'project-b')
        with service.connection() as db:
            for project_id, material_id in [('project-main', 'material-a'), ('project-b', 'material-b')]:
                p = self.payload(project_id=project_id, material_id=material_id)
                db.execute('INSERT INTO tasks(id,project_id,material_id,task_type,title,instructions,status,input_json,result,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (project_id + '-task', project_id, material_id, 'summary', '总结', '', 'completed', json.dumps(p), '', '', service.utc_now(), service.utc_now()))
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (project_id + '-artifact', project_id, project_id + '-task', material_id, '总结', 'summary', json.dumps({'content': '全文'}), service.utc_now()))
        a = self.client.get('/api/bootstrap?project_id=project-main').json()
        b = self.client.get('/api/bootstrap?project_id=project-b').json()
        self.assertEqual([m['id'] for m in a['materials']], ['material-a'])
        self.assertEqual(len(b['materials']), 49)
        self.assertTrue(all(t['project_id'] == 'project-b' for t in b['tasks']))
        self.assertTrue(all(t['project_id'] == 'project-b' for t in b['artifacts']))
        self.assertEqual(a['settings']['data_dir'], str(self.root))

    def test_foreign_material_is_rejected_for_pdf_and_pasted_sources(self):
        for source in ('', '用户粘贴的原文'):
            response = self.client.post('/api/tasks', json=self.payload(material_id='material-b', source_text=source))
            self.assertEqual(response.status_code, 400)
            self.assertIn('不属于', response.json()['detail'])
        self.assertEqual(self.client.get('/api/bootstrap').json()['tasks'], [])

    def test_ranges_and_long_source_fail_before_task_creation(self):
        for values in ({'page_from': 3, 'page_to': 1}, {'page_to': 6}):
            self.assertEqual(self.client.post('/api/tasks', json=self.payload(**values)).status_code, 400)
        with service.connection() as db:
            db.execute("UPDATE pages SET text=? WHERE material_id='material-a' AND page_number=1", ('字' * 42001,))
        response = self.client.post('/api/tasks', json=self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn('42,000', response.json()['detail'])
        self.assertEqual(self.client.get('/api/bootstrap').json()['tasks'], [])

    def test_task_survives_navigation_with_precise_source_metadata(self):
        with patch.object(service, 'generate_text', return_value='完整成果正文'):
            response = self.client.post('/api/tasks', json=self.payload())
            self.assertEqual(response.status_code, 200)
            task_id = response.json()['id']
            for _ in range(100):
                task = self.client.get('/api/tasks/' + task_id).json()
                if task['status'] in ('completed', 'failed'):
                    break
                time.sleep(.02)
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(task['project_id'], 'project-main')
        self.assertEqual(task['material_id'], 'material-a')
        context = task['artifact']['source_context']
        self.assertEqual(context['missing_pages'], [2])
        self.assertEqual(context['model'], 'test-model')
        self.assertEqual(context['page_from'], 1)
        artifacts = self.client.get('/api/artifacts?project_id=project-main').json()
        self.assertEqual(json.loads(artifacts[0]['body'])['content'], '完整成果正文')
        self.assertEqual(self.client.get('/api/artifacts?project_id=project-b').json(), [])

    def test_pasted_text_preserves_material_and_page_context(self):
        with service.connection() as db:
            source, context = service.collect_source(db, service.TaskCreate(**self.payload(source_text='原文', page_from=2, page_to=2)))
        self.assertEqual(source, '原文')
        self.assertEqual(context['material_id'], 'material-a')
        self.assertTrue(context['manual_source'])
        self.assertEqual(context['page_from'], 2)

    def test_book_edit_invalidates_version_confirmation_and_retains_original_citation(self):
        with service.connection() as db:
            service.create_reference_rows(db, 'material-a', [{'title': '书目', 'authors': '作者', 'year': '2020', 'raw_text': '原始论文引文：作者，书目，2020', 'page_number': 3}])
            reference_id = db.execute('SELECT id FROM references_found').fetchone()['id']
        book = self.client.post('/api/references/' + reference_id + '/add-to-library').json()
        self.client.post('/api/references/' + reference_id + '/confirm-version')
        response = self.client.put('/api/library/' + book['id'], json={'title': '修订题名', 'authors': '作者', 'year': '2021', 'raw_reference': '第二版，2021'})
        self.assertEqual(response.status_code, 200)
        reference = self.client.get('/api/materials/material-a').json()['references'][0]
        self.assertIn('版本待核', reference['match_status'])
        self.assertEqual(reference['raw_text'], '原始论文引文：作者，书目，2020')
        self.assertEqual(reference['matched_reference'], '第二版，2021')

    def test_unconfigured_model_blocks_task_before_enqueue(self):
        with service.connection() as db:
            db.execute('DELETE FROM app_settings')
        self.assertEqual(self.client.post('/api/tasks', json=self.payload()).status_code, 400)
        self.assertEqual(self.client.get('/api/bootstrap').json()['tasks'], [])

    def test_connection_test_sends_no_material_and_does_not_create_tasks(self):
        response = httpx.Response(200, json={'choices': [{'message': {'content': '连接成功'}}]})
        with patch.object(service.httpx, 'post', return_value=response) as model:
            self.assertEqual(self.client.post('/api/settings/test').status_code, 200)
        user_message = model.call_args.kwargs['json']['messages'][1]['content']
        self.assertIn('Connection test.', user_message)
        self.assertNotIn('原文内容', user_message)
        self.assertEqual(self.client.get('/api/bootstrap').json()['tasks'], [])

    def test_blank_project_name_is_rejected(self):
        self.assertEqual(self.client.post('/api/projects', json={'name': '   '}).status_code, 400)

    def test_restart_marks_queued_tasks_recoverable(self):
        with service.connection() as db:
            db.execute('INSERT INTO tasks(id,project_id,material_id,task_type,title,instructions,status,input_json,result,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('interrupted', 'project-main', 'material-a', 'summary', '总结', '', 'queued', json.dumps(self.payload()), '', '', service.utc_now(), service.utc_now()))
        service.initialize(self.root)
        task = self.client.get('/api/tasks/interrupted').json()
        self.assertEqual(task['status'], 'paused')


if __name__ == '__main__':
    unittest.main()
