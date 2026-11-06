"""A+B 模型池后端契约。

覆盖：门户 models.json v2 规范化/兼容旧格式、默认模型解析与镜像、id 生成去重、
工作区同步（公开字段不含服务地址）、项目级模型解析与 projects.model_id 迁移。
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as service
from backend import portal_workspace_host
from backend.portal_fleet import WorkspaceFleet, normalize_model_pool, public_model_pool

LEGACY = {'base_url': 'https://api.deepseek.com/v1/', 'model': 'deepseek-chat',
          'provider_name': 'DeepSeek', 'context_tokens': 128000, 'max_output_tokens': 8192}

MODEL_ID = re.compile(r'm_[0-9a-f]{8}')


def pool_model(identifier, name='模型一', **values):
    model = {'id': identifier, 'name': name, 'provider_name': '服务商',
             'base_url': 'https://pool.example.invalid/v1', 'model': 'model-' + name,
             'context_tokens': 32000, 'max_output_tokens': 4096,
             'price_input_per_million': 2, 'price_output_per_million': 8,
             'price_cached_per_million': None, 'currency': 'CNY',
             'price_date': '2026-11-05', 'enabled': True, 'selectable': True}
    model.update(values)
    return model


class NormalizeTests(unittest.TestCase):
    def test_legacy_single_model_becomes_v2_with_default_id(self):
        value = normalize_model_pool(LEGACY)
        self.assertEqual(value['version'], 2)
        self.assertEqual(value['default_model_id'], 'default')
        self.assertEqual(len(value['models']), 1)
        model = value['models'][0]
        self.assertEqual(model['id'], 'default')
        self.assertEqual(model['name'], 'DeepSeek')
        self.assertEqual(model['base_url'], 'https://api.deepseek.com/v1')
        self.assertTrue(model['enabled'])
        self.assertTrue(model['selectable'])
        for key in ('base_url', 'model', 'provider_name', 'context_tokens', 'max_output_tokens'):
            expected = LEGACY[key].rstrip('/') if key == 'base_url' else LEGACY[key]
            self.assertEqual(value[key], expected, key)

    def test_empty_pool_is_normalized_for_read(self):
        value = normalize_model_pool({})
        self.assertEqual(value['version'], 2)
        self.assertEqual(value['default_model_id'], '')
        self.assertEqual(value['models'], [])
        self.assertEqual(value['provider_name'], '管理员配置的模型')

    def test_new_format_resolves_default_and_mirrors_it(self):
        first, second = pool_model('m_aaaaaaaa'), pool_model('m_bbbbbbbb', '模型二')
        value = normalize_model_pool({'models': [first, second], 'default_model_id': 'm_bbbbbbbb'})
        self.assertEqual(value['default_model_id'], 'm_bbbbbbbb')
        self.assertEqual([item['id'] for item in value['models']], ['m_aaaaaaaa', 'm_bbbbbbbb'])
        self.assertEqual(value['base_url'], second['base_url'])
        self.assertEqual(value['model'], second['model'])
        self.assertEqual(value['context_tokens'], second['context_tokens'])
        # Only the documented mirror fields sit at the top level.
        self.assertNotIn('price_input_per_million', value)

    def test_missing_ids_are_generated_and_duplicates_are_separated(self):
        repeated = 'm_cccccccc'
        value = normalize_model_pool({'models': [{'name': '甲'}, pool_model(repeated), pool_model(repeated)], 'default_model_id': ''})
        ids = [item['id'] for item in value['models']]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[1], repeated)
        self.assertTrue(all(MODEL_ID.fullmatch(identifier) for identifier in ids))

    def test_default_falls_back_to_first_enabled_model_on_read(self):
        disabled, enabled = pool_model('m_aaaaaaaa', enabled=False), pool_model('m_bbbbbbbb', enabled=True)
        value = normalize_model_pool({'models': [disabled, enabled], 'default_model_id': 'm_zzzzzzzz'})
        self.assertEqual(value['default_model_id'], enabled['id'])

    def test_public_pool_omits_service_address(self):
        value = public_model_pool({'models': [pool_model('m_aaaaaaaa')], 'default_model_id': 'm_aaaaaaaa'})
        self.assertEqual(value['default_model_id'], 'm_aaaaaaaa')
        model = value['models'][0]
        self.assertNotIn('base_url', model)
        self.assertEqual(model['price_input_per_million'], 2)
        self.assertTrue(model['selectable'])

    def test_strict_validation_rejects_invalid_pools(self):
        for payload in ({'models': []},
                        {'models': [pool_model('m_aaaaaaaa')], 'default_model_id': 'm_zzzzzzzz'},
                        {'models': [pool_model('m_aaaaaaaa', enabled=False)], 'default_model_id': 'm_aaaaaaaa'},
                        {'models': [pool_model('m_aaaaaaaa', base_url='')]},
                        {'models': [pool_model('m_aaaaaaaa', model='')]},
                        {'base_url': '', 'model': ''}):
            with self.assertRaises(ValueError, msg=payload):
                normalize_model_pool(payload, strict=True)

    def test_strict_legacy_payload_still_saves_as_one_model(self):
        value = normalize_model_pool(LEGACY, strict=True)
        self.assertEqual(value['default_model_id'], 'default')
        self.assertEqual(value['models'][0]['id'], 'default')


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.fleet = WorkspaceFleet(self.root)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.fleet.close)

    def test_legacy_save_writes_v2_and_keeps_mirror(self):
        self.fleet.save_settings(LEGACY)
        stored = json.loads(self.fleet.models.read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 2)
        self.assertEqual(stored['default_model_id'], 'default')
        self.assertEqual(stored['models'][0]['id'], 'default')
        self.assertEqual(self.fleet.settings()['model'], 'deepseek-chat')

    def test_save_and_load_new_format(self):
        value = {'models': [pool_model('m_aaaaaaaa'), pool_model('m_bbbbbbbb', '模型二')],
                 'default_model_id': 'm_bbbbbbbb'}
        self.fleet.save_settings(value)
        loaded = self.fleet.settings()
        self.assertEqual(loaded['default_model_id'], 'm_bbbbbbbb')
        self.assertEqual([item['name'] for item in loaded['models']], ['模型一', '模型二'])

    def test_reading_old_file_repairs_it_in_place(self):
        path = self.root / 'private' / 'models.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(LEGACY), encoding='utf-8')
        loaded = self.fleet.settings()
        self.assertEqual(loaded['version'], 2)
        stored = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 2)
        self.assertEqual(stored['default_model_id'], 'default')

    def test_corrupt_file_degrades_to_empty_pool(self):
        path = self.root / 'private' / 'models.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not json', encoding='utf-8')
        self.assertEqual(self.fleet.settings()['models'], [])

    def test_invalid_save_does_not_touch_previous_configuration(self):
        self.fleet.save_settings(LEGACY)
        with self.assertRaises(ValueError):
            self.fleet.save_settings({'models': []})
        self.assertEqual(self.fleet.settings()['default_model_id'], 'default')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        service.initialize(self.root)
        service.service.token = 'model-pool-test'
        self.client = TestClient(service.app, headers={'X-App-Token': 'model-pool-test'})
        self.secret = patch.object(service.keyring, 'get_password', return_value=None)
        self.secret.start()
        response = self.client.put('/api/settings', json={
            'base_url': 'http://127.0.0.1:9999/v1', 'model': 'default-model',
            'provider_name': '默认服务商', 'context_tokens': 16000, 'max_output_tokens': 2048,
            'price_input_per_million': 1, 'price_output_per_million': 2,
            'currency': 'CNY', 'price_date': '2026-11-04'})
        self.assertEqual(response.status_code, 200, response.text)
        self.pool = {'default_model_id': 'm_aaaaaaaa',
                     'models': [pool_model('m_aaaaaaaa', base_url='http://127.0.0.1:9998/v1'),
                                pool_model('m_bbbbbbbb', '模型二', enabled=False, base_url='http://127.0.0.1:9998/v1')]}
        service.save_model_pool(self.pool)

    def tearDown(self):
        for thread in list(service.service.tasks):
            thread.join(timeout=5)
        service.service.tasks.clear()
        self.secret.stop()
        self.client.close()
        self.directory.cleanup()

    def project(self, model_id='', project_type='general'):
        response = self.client.post('/api/projects', json={'name': '模型项目', 'model_id': model_id, 'project_type': project_type})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_project_model_overrides_default_settings(self):
        project = self.project('m_aaaaaaaa')
        self.assertEqual(project['model_id'], 'm_aaaaaaaa')
        settings = service.read_model_settings(project['id'])
        self.assertEqual(settings['id'], 'm_aaaaaaaa')
        self.assertEqual(settings['name'], '模型一')
        # The selected model carries its own service address (multi-provider pool).
        self.assertEqual(settings['base_url'], 'http://127.0.0.1:9998/v1')
        self.assertEqual(settings['model'], 'model-模型一')
        self.assertEqual(settings['price_input_per_million'], 2)
        self.assertEqual(settings['price_cached_per_million'], None)
        # No project id keeps the exact pre-pool behaviour (default app_settings).
        default = service.read_model_settings()
        self.assertEqual(default['model'], 'default-model')
        self.assertNotIn('id', default)

    def test_disabled_or_unknown_project_model_falls_back_to_default(self):
        for model_id in ('m_bbbbbbbb', 'm_zzzzzzzz', ''):
            project = self.project(model_id)
            settings = service.read_model_settings(project['id'])
            self.assertEqual(settings['model'], 'default-model')
            self.assertNotIn('id', settings)
        # A task-like caller may name a project that no longer exists.
        self.assertEqual(service.read_model_settings('missing-project')['model'], 'default-model')

    def test_get_settings_exposes_resolved_project_model(self):
        project = self.project('m_aaaaaaaa')
        default = self.client.get('/api/settings').json()
        self.assertEqual(default['model'], 'default-model')
        self.assertEqual(default['model_id'], '')
        self.assertEqual(default['model_name'], 'default-model')
        value = self.client.get('/api/settings', params={'project_id': project['id']}).json()
        self.assertEqual(value['model'], 'model-模型一')
        self.assertEqual(value['model_id'], 'm_aaaaaaaa')
        self.assertEqual(value['model_name'], '模型一')
        self.assertTrue(value['configured'])
        fallback = self.project('m_bbbbbbbb')
        value = self.client.get('/api/settings', params={'project_id': fallback['id']}).json()
        self.assertEqual(value['model'], 'default-model')
        self.assertEqual(value['model_id'], '')
        self.assertEqual(value['model_name'], 'default-model')

    def test_bootstrap_carries_public_model_pool(self):
        value = self.client.get('/api/bootstrap').json()
        self.assertEqual(value['model_pool']['default_model_id'], 'm_aaaaaaaa')
        self.assertEqual([item['id'] for item in value['model_pool']['models']], ['m_aaaaaaaa', 'm_bbbbbbbb'])
        self.assertNotIn('base_url', value['model_pool']['models'][0])
        self.assertEqual(value['settings']['model'], 'default-model')

    def test_project_update_and_default_clear_model_id(self):
        project = self.project('m_aaaaaaaa')
        body = {'name': '改后的项目', 'model_id': 'm_bbbbbbbb'}
        response = self.client.put('/api/projects/' + project['id'], json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['model_id'], 'm_bbbbbbbb')
        response = self.client.put('/api/projects/' + project['id'], json={'name': '老客户端'})
        self.assertEqual(response.json()['model_id'], '')

    def test_batch_model_snapshot_uses_project_pool_model(self):
        project = self.project('m_aaaaaaaa', project_type='collation')
        material = self.client.post('/api/materials/import', params={'filename': '底本.txt', 'project_id': project['id'], 'role': 'base'},
                                    content='卷五\n天地玄黄\n'.encode(), headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(material.status_code, 200, material.text)
        run = self.client.post(f"/api/projects/{project['id']}/preprocess/split", json={'material_id': material.json()['id']}).json()
        with patch.object(service.batch_runner, 'kick'):
            batch = self.client.post(f"/api/projects/{project['id']}/collation/batches",
                                     json={'run_id': run['id'], 'request_id': 'model-pool-snapshot'})
        self.assertEqual(batch.status_code, 200, batch.text)
        snapshot = batch.json()['model']
        self.assertEqual(snapshot['id'], 'm_aaaaaaaa')
        self.assertEqual(snapshot['name'], '模型一')
        self.assertEqual(snapshot['model'], 'model-模型一')
        self.assertEqual(snapshot['price_input_per_million'], 2)
        self.assertEqual(snapshot['base_url'], 'http://127.0.0.1:9998/v1')

class SyncTests(unittest.TestCase):
    """portal_workspace_host.model_settings keeps the legacy settings contract plus the pool."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        service.initialize(self.root)
        self.secret = patch.object(service.keyring, 'get_password', return_value=None)
        self.secret.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.secret.stop)

    def test_legacy_models_file_syncs_default_and_public_pool(self):
        models = self.root / 'models.json'
        models.write_text(json.dumps(LEGACY), encoding='utf-8')
        portal_workspace_host.model_settings(service, models)
        settings = service.read_model_settings()
        self.assertEqual(settings['base_url'], 'https://api.deepseek.com/v1')
        self.assertEqual(settings['model'], 'deepseek-chat')
        self.assertEqual(settings['provider_name'], 'DeepSeek')
        self.assertEqual(settings['context_tokens'], 128000)
        pool = service.read_model_pool()
        self.assertEqual(pool['default_model_id'], 'default')
        self.assertEqual([item['id'] for item in pool['models']], ['default'])
        self.assertNotIn('base_url', pool['models'][0])
        self.assertTrue(pool['models'][0]['selectable'])

    def test_v2_models_file_syncs_selected_default_and_prices(self):
        second = pool_model('m_bbbbbbbb', '模型二')
        models = self.root / 'models.json'
        models.write_text(json.dumps({'version': 2, 'default_model_id': 'm_bbbbbbbb',
                                      'models': [pool_model('m_aaaaaaaa'), second]}), encoding='utf-8')
        portal_workspace_host.model_settings(service, models)
        settings = service.read_model_settings()
        self.assertEqual(settings['model'], second['model'])
        self.assertEqual(settings['price_output_per_million'], second['price_output_per_million'])
        self.assertEqual(settings['currency'], 'CNY')
        pool = service.read_model_pool()
        self.assertEqual(pool['default_model_id'], 'm_bbbbbbbb')
        self.assertEqual(len(pool['models']), 2)
        # Internal storage keeps each model's service address for project-level routing;
        # the public bootstrap projection strips it.
        internal = service.read_model_pool_internal()
        self.assertEqual(internal['models'][1]['base_url'], second['base_url'])
        self.assertNotIn('base_url', pool['models'][1])

    def test_out_of_range_default_metadata_is_dropped_instead_of_crashing(self):
        models = self.root / 'models.json'
        low = pool_model('m_aaaaaaaa', context_tokens=1024, max_output_tokens=64)
        models.write_text(json.dumps({'version': 2, 'default_model_id': 'm_aaaaaaaa', 'models': [low]}), encoding='utf-8')
        portal_workspace_host.model_settings(service, models)
        settings = service.read_model_settings()
        self.assertEqual(settings['model'], low['model'])
        self.assertIsNone(settings.get('context_tokens'))
        self.assertIsNone(settings.get('max_output_tokens'))

    def test_missing_models_file_clears_stale_settings_without_error(self):
        service.save_model_pool({'default_model_id': 'm_aaaaaaaa', 'models': [pool_model('m_aaaaaaaa')]})
        portal_workspace_host.model_settings(service, self.root / 'absent.json')
        self.assertEqual(service.read_model_settings()['model'], '')
        self.assertEqual(service.read_model_pool(), {'default_model_id': '', 'models': []})


class ProjectMigrationTests(unittest.TestCase):
    def test_old_projects_table_gains_model_id_column(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with closing(sqlite3.connect(root / 'library.sqlite')) as db:
                db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
                  CREATE TABLE materials(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,filename TEXT NOT NULL,title TEXT NOT NULL,sha256 TEXT NOT NULL,asset_path TEXT NOT NULL,page_count INTEGER NOT NULL,extraction_coverage REAL NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,sha256));''')
                db.execute("INSERT INTO projects VALUES('old-project','旧项目','',?)", (service.utc_now(),))
                db.commit()
            service.initialize(root)
            with service.connection() as db:
                self.assertIn('model_id', {row['name'] for row in db.execute('PRAGMA table_info(projects)')})
                self.assertEqual(db.execute("SELECT model_id FROM projects WHERE id='old-project'").fetchone()[0], '')


class PortalAdminTests(unittest.TestCase):
    """Admin API accepts the v2 pool payload and the pre-v2 single-model payload."""

    PASSWORD = 'model-pool-portal-password'

    def setUp(self):
        import os
        from backend.portal import ADMIN_COOKIE, create_app
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {'SHIXUE_MODEL_API_KEY': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.fleet = WorkspaceFleet(self.root)
        self.application = create_app(self.root, fleet=self.fleet)
        self.application.state.accounts.create('admin', '管理员', self.PASSWORD, 'admin', require_change=False)
        self.client = TestClient(self.application)
        self.client.__enter__()
        self.client.headers['X-Wenjin-Portal'] = 'admin'
        response = self.client.post('/api/session', json={'username': 'admin', 'password': self.PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.cookie = ADMIN_COOKIE

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.fleet.close()
        self.directory.cleanup()

    def test_legacy_single_model_put_is_still_accepted(self):
        response = self.client.put('/api/admin/settings', json={
            'base_url': 'http://127.0.0.1:9999/v1', 'model': 'portal-fixture', 'provider_name': '隔离模拟模型'})
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value['version'], 2)
        self.assertEqual(value['default_model_id'], 'default')
        self.assertEqual(value['models'][0]['model'], 'portal-fixture')
        self.assertEqual(value['base_url'], 'http://127.0.0.1:9999/v1')
        self.assertEqual(value['model'], 'portal-fixture')
        self.assertEqual(value['provider_name'], '隔离模拟模型')
        self.assertEqual(value['has_api_key'], False)
        self.assertTrue(value['api_key_managed'])
        self.assertEqual(value['database_mode'], 'shared-library-private-research')
        self.assertNotIn('api_key', value)
        stored = json.loads(self.fleet.models.read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 2)
        self.assertEqual(stored['default_model_id'], 'default')

    def test_v2_pool_put_and_get_round_trip(self):
        first = pool_model('m_aaaaaaaa', '深度求索')
        second = pool_model('m_bbbbbbbb', '备用模型', price_input_per_million=3,
                            price_output_per_million=9, price_cached_per_million=1)
        response = self.client.put('/api/admin/settings',
                                   json={'models': [first, second], 'default_model_id': 'm_bbbbbbbb'})
        self.assertEqual(response.status_code, 200, response.text)
        value = self.client.get('/api/admin/settings').json()
        self.assertEqual(value['version'], 2)
        self.assertEqual(value['default_model_id'], 'm_bbbbbbbb')
        self.assertEqual([item['id'] for item in value['models']], ['m_aaaaaaaa', 'm_bbbbbbbb'])
        self.assertEqual(value['model'], 'model-备用模型')
        self.assertEqual(value['models'][1]['price_cached_per_million'], 1)
        self.assertEqual(value['models'][1]['selectable'], True)
        # A second portal instance reads the same normalized file.
        from backend.portal_fleet import WorkspaceFleet as Fleet
        reopened = Fleet(self.root)
        try:
            self.assertEqual(reopened.settings()['default_model_id'], 'm_bbbbbbbb')
        finally:
            reopened.close()

    def test_invalid_pools_are_rejected_with_400(self):
        self.assertEqual(self.client.put('/api/admin/settings', json={'models': []}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/settings',
                                         json={'models': [pool_model('m_aaaaaaaa')],
                                               'default_model_id': 'm_zzzzzzzz'}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/settings',
                                         json={'models': [pool_model('m_aaaaaaaa', base_url='')],
                                               'default_model_id': 'm_aaaaaaaa'}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/settings',
                                         json={'base_url': 'http://127.0.0.1:9999/v1', 'model': ''}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
