"""Standalone API boundaries, durable handoff, resumable files, and draft conflicts."""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as application
from backend.identity import ensure_identity, legacy_key, workspace_key
from backend.knowledge.worker import worker
from backend.worker import Supervisor


class StandaloneServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.token = 'isolated-service-token-' + 'a' * 32
        self.patches = [patch.object(application.service, 'mode', 'server'),
                        patch.object(application.service, 'token', self.token),
                        patch.object(application.service, 'embedded_workers', False),
                        patch.object(worker, 'enabled', False),
                        patch.object(application.keyring, 'get_password', return_value=None)]
        for item in self.patches:
            item.start()
        application.initialize(self.root, recover=False)
        self.client = TestClient(application.app, headers={'X-App-Token': self.token})

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_remote_auth_cookie_csrf_logout_and_management_boundary(self):
        with TestClient(application.app, client=('198.51.100.7', 4321)) as client:
            self.assertEqual(client.get('/api/projects').status_code, 401)
            self.assertFalse(client.get('/api/session').json()['authenticated'])
            self.assertEqual(client.post('/api/session', json={'token': 'wrong'}).status_code, 401)
            self.assertEqual(client.post('/api/session', json={'token': self.token}, headers={'Origin': 'https://untrusted.invalid'}).status_code, 403)
            login = client.post('/api/session', json={'token': self.token})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertIn('HttpOnly', login.headers['set-cookie'])
            self.assertIn('SameSite=strict', login.headers['set-cookie'])
            self.assertNotIn(self.token, login.headers['set-cookie'])
            self.assertEqual(client.get('/api/projects').status_code, 200)
            self.assertEqual(client.post('/api/projects', json={'name': '跨站修改'}, headers={'Origin': 'https://untrusted.invalid'}).status_code, 403)
            for endpoint in ['/api/service/exit', '/api/collation/shutdown', '/api/knowledge/document-imports/preview', '/api/knowledge/core/import']:
                self.assertEqual(client.post(endpoint, json={}).status_code, 403, endpoint)
            client.delete('/api/session')
            self.assertEqual(client.get('/api/projects').status_code, 401)
        self.assertEqual(self.client.get('/api/settings').json()['data_dir'], '')

    def test_expired_and_tampered_sessions_are_rejected(self):
        with TestClient(application.app) as client:
            client.post('/api/session', json={'token': self.token})
            with patch('backend.auth.time.time', return_value=10**12):
                self.assertEqual(client.get('/api/projects').status_code, 401)
            client.cookies.set('wenjin_session', '1.fake.bad-signature', path='/api')
            self.assertEqual(client.get('/api/projects').status_code, 401)

    def test_resumable_upload_preserves_paths_and_finalizes_immutably(self):
        data = '财政赋税𠀀'.encode()
        result = self.client.post('/api/uploads', json={'mode': 'directory', 'label': '研究目录', 'files': [{'relative_path': '卷一/原文.txt', 'size': len(data)}]})
        self.assertEqual(result.status_code, 200, result.text)
        identifier = result.json()['id']
        endpoint = f'/api/uploads/{identifier}/files/0'
        first = self.client.put(endpoint + '?offset=0', content=data[:4])
        self.assertEqual(first.json()['offset'], 4)
        with TestClient(application.app, headers={'Authorization': 'Bearer ' + self.token}) as reconnected:
            status = reconnected.get('/api/uploads/' + identifier).json()
            self.assertEqual(status['files'][0]['offset'], 4)
            self.assertEqual(reconnected.put(endpoint + '?offset=0', content=data).status_code, 409)
            self.assertEqual(reconnected.post(f'/api/uploads/{identifier}/preview').status_code, 409)
            self.assertEqual(reconnected.put(endpoint + '?offset=4', content=data[4:]).status_code, 200)
        preview = {'id': 'document-batch', 'mode': 'directory', 'source_label': 'files', 'files': 1, 'bytes': len(data), 'ignored': 0, 'reuse_existing': False, 'preview': [{'relative_path': '卷一/原文.txt', 'size': len(data)}]}
        with patch('backend.uploads.DocumentImports.preview', return_value=preview) as service:
            final = self.client.post(f'/api/uploads/{identifier}/preview')
            self.assertEqual(final.status_code, 200, final.text)
            self.assertEqual(final.json()['source_label'], '研究目录')
            selected = Path(service.call_args.args[1][0])
            self.assertEqual((selected / '卷一/原文.txt').read_bytes(), data)
            self.assertTrue(selected.is_relative_to(self.root))
            self.assertEqual(self.client.post(f'/api/uploads/{identifier}/preview').json(), final.json())
            self.assertEqual(service.call_count, 1)
        self.assertEqual(self.client.put(endpoint + f'?offset={len(data)}', content=b'x').status_code, 409)

    def test_upload_rejects_traversal_collisions_and_oversized_chunks(self):
        for paths in [['../escape.txt'], ['/absolute.txt'], ['C:/local.txt'], ['a.txt', 'A.txt'], ['a.txt', 'a.txt/b.txt'], ['CON.txt'], ['a/../b.txt']]:
            response = self.client.post('/api/uploads', json={'mode': 'directory', 'files': [{'relative_path': name, 'size': 1} for name in paths]})
            self.assertEqual(response.status_code, 400, paths)
        self.assertFalse((self.root.parent / 'escape.txt').exists())
        state = self.client.post('/api/uploads', json={'files': [{'relative_path': '原文.txt', 'size': 10}]}).json()
        endpoint = f"/api/uploads/{state['id']}/files/0?offset=0"
        self.assertEqual(self.client.put(endpoint, content=b'x' * (state['chunk_size'] + 1)).status_code, 413)
        self.assertEqual(self.client.get('/api/uploads/' + state['id']).json()['files'][0]['offset'], 0)

    def configure_model(self):
        with application.connection() as db:
            db.execute("INSERT INTO app_settings VALUES('base_url','http://127.0.0.1:1/v1')")
            db.execute("INSERT INTO app_settings VALUES('model','isolated-fixture')")

    def test_task_executes_after_client_disconnect_and_api_restart_preserves_queue(self):
        self.configure_model()
        with patch.object(application, 'request_completion', return_value={'text': '服务端研究结果'}) as model:
            result = self.client.post('/api/tasks', json={'task_type': 'summary', 'source_text': '独立执行的原文'})
            self.assertEqual(result.status_code, 200, result.text)
            identifier = result.json()['id']
            self.assertEqual(result.json()['status'], 'queued')
            model.assert_not_called()
            self.client.close()
            application.initialize(self.root, recover=False)
            with application.connection() as db:
                self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?', (identifier,)).fetchone()[0], 'queued')
            Supervisor(application).tick()
            self.assertEqual(model.call_count, 1)
        with TestClient(application.app, headers={'X-App-Token': self.token}) as client:
            result = client.get('/api/tasks/' + identifier).json()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['artifact']['content'], '服务端研究结果')
            exported = client.get('/api/artifacts/' + result['artifact']['id'] + '/export')
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertIn('服务端研究结果', exported.json()['content'])

    def test_api_reinitialize_does_not_pause_a_running_worker_task(self):
        self.configure_model()
        task = self.client.post('/api/tasks', json={'task_type': 'summary', 'source_text': '保持执行'}).json()
        with application.connection() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (task['id'],))
        application.initialize(self.root, recover=False)
        self.assertEqual(self.client.get('/api/tasks/' + task['id']).json()['status'], 'running')

    def test_stop_reaches_a_separate_runner_through_durable_state(self):
        self.configure_model()
        project = self.client.post('/api/projects', json={'name':'停止测试', 'project_type':'collation'}).json()['id']
        source = '卷五\n事类天地【丨丨之精】日月【明月】\n卷六\n格物总论【天地运行】\n'
        material = self.client.post('/api/materials/import', params={'filename':'底本.txt','project_id':project,'role':'base'}, content=source.encode()).json()
        prepared = self.client.post(f'/api/projects/{project}/preprocess/split', json={'material_id':material['id']}).json()
        batch = self.client.post(f'/api/projects/{project}/collation/batches', json={'run_id':prepared['id'],'request_id':'stop-separate-runner'}).json()
        started, cancelled = threading.Event(), threading.Event()
        def completion(_messages, model, _budget):
            started.set()
            if model['_cancel_event'].wait(6):
                cancelled.set()
            error = RuntimeError('模拟取消')
            error.cancelled = True
            raise error
        runner = application.collation.Runner(application.service, application.utc_now, completion, application.preprocess_bindings['create_units'])
        thread = threading.Thread(target=runner._worker, args=(self.root,))
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            result = self.client.post(f"/api/collation/batches/{batch['id']}/action", json={'action':'stop'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(cancelled.wait(4), 'The API runner has no access to the separate worker cancellation event')
        finally:
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.client.get('/api/collation/batches/' + batch['id']).json()['status'], 'stopped')

    def test_manuscript_export_uses_server_metadata_preserves_draft_and_checks_revision(self):
        material = self.client.post('/api/materials/import?filename=原文.txt&project_id=project-main', content='原始内容'.encode()).json()
        unit = self.client.post('/api/projects/project-main/units', json={'kind': 'reading', 'title': '研究稿', 'material_id': material['id'], 'page_from': 1, 'page_to': 1}).json()
        body = {'expected_revision': unit['revision'], 'content': '未保存的研究稿', 'notes': [], 'status': 'approved'}
        result = self.client.post(f"/api/units/{unit['id']}/export", json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('未保存的研究稿', result.json()['content'])
        self.assertIn('含未保存修改', result.json()['content'])
        self.assertIn('状态：待复核', result.json()['content'])
        self.assertEqual(self.client.get('/api/units/' + unit['id']).json()['content'], unit['content'])
        save = self.client.put('/api/units/' + unit['id'], json={**body, 'status': 'working'})
        self.assertEqual(save.status_code, 200, save.text)
        self.assertEqual(self.client.post(f"/api/units/{unit['id']}/export", json=body).status_code, 409)


class PortableIdentityTests(unittest.TestCase):
    def test_existing_key_remains_readable_after_directory_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'original'
            target = Path(folder) / 'moved'
            source.mkdir()
            expected = legacy_key(source)
            self.assertEqual(ensure_identity(source), expected)
            shutil.copytree(source, target)
            self.assertNotEqual(legacy_key(target), expected)
            self.assertEqual(ensure_identity(target), expected)
            self.assertEqual(workspace_key(target), expected)

    def test_corrupt_identity_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'workspace-identity.json').write_text(json.dumps({'workspace_key': 'bad'}))
            with self.assertRaises(ValueError):
                ensure_identity(root)
