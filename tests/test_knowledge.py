import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as service
from backend.knowledge.audit import audit_legacy
from backend.knowledge.config import DatabaseSettings, credential_account, read_settings
from backend.knowledge.database import database


class KnowledgeFoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / 'data'
        self.root.mkdir()
        service.initialize(self.root)
        service.service.token = 'knowledge-test'
        self.client = TestClient(service.app, headers={'X-App-Token': 'knowledge-test'})
        self.environment = patch.dict(os.environ)
        self.environment.start()
        os.environ.pop('SHIXUE_KB_DATABASE_URL', None)
        self.store = {}
        self.get = patch('backend.knowledge.config.keyring.get_password', side_effect=lambda s, a: self.store.get((s, a)))
        self.set = patch('backend.knowledge.config.keyring.set_password', side_effect=lambda s, a, p: self.store.__setitem__((s, a), p))
        self.get.start()
        self.set.start()

    def tearDown(self):
        database.dispose()
        self.client.close()
        self.get.stop()
        self.set.stop()
        self.environment.stop()
        self.directory.cleanup()

    def test_disabled_health_and_access_control_preserve_existing_app(self):
        response = self.client.get('/api/knowledge/health')
        self.assertEqual(response.json()['status'], 'disabled')
        self.assertFalse((self.root / 'knowledge.json').exists())
        self.assertEqual(self.client.get('/api/knowledge/settings', headers={'X-App-Token': 'invalid'}).status_code, 403)
        self.assertEqual(self.client.get('/api/bootstrap').status_code, 200)

    def test_password_never_enters_config_or_response_and_is_bound_to_connection(self):
        payload = DatabaseSettings(enabled=True).model_dump() | {'password': 'private-database-password'}
        result = self.client.put('/api/knowledge/settings', json=payload)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['has_password'])
        self.assertNotIn('private-database-password', result.text)
        self.assertNotIn('password', (self.root / 'knowledge.json').read_text())
        payload['password'] = ''
        self.assertTrue(self.client.put('/api/knowledge/settings', json=payload).json()['has_password'])
        payload['database'] = 'another_library'
        self.assertFalse(self.client.put('/api/knowledge/settings', json=payload).json()['has_password'])
        self.assertEqual(read_settings(self.root).database, 'another_library')

    def test_invalid_or_environment_configuration_cannot_overwrite_saved_settings(self):
        self.assertEqual(self.client.put('/api/knowledge/settings', json={'port': 0}).status_code, 422)
        invalid_password = 'secret-' * 200
        response = self.client.put('/api/knowledge/settings', json={'password': invalid_password})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret-', response.text)
        os.environ['SHIXUE_KB_DATABASE_URL'] = 'postgresql://user:secret@127.0.0.1/database'
        self.assertTrue(self.client.get('/api/knowledge/settings').json()['environment_override'])
        response = self.client.put('/api/knowledge/settings', json=DatabaseSettings().model_dump())
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.root / 'knowledge.json').exists())

    def test_connection_errors_are_redacted_and_never_trigger_schema_writes(self):
        with patch.object(database, 'engine', side_effect=RuntimeError('postgresql://user:private@host')):
            result = self.client.get('/api/knowledge/health')
        self.assertEqual(result.json()['status'], 'unavailable')
        self.assertNotIn('private', result.text)

    def test_credential_failure_does_not_save_new_config(self):
        payload = DatabaseSettings(enabled=True).model_dump() | {'password': 'private-password'}
        with patch('backend.knowledge.config.keyring.set_password', side_effect=RuntimeError('private-password')):
            result = self.client.put('/api/knowledge/settings', json=payload)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private-password', result.text)
        self.assertFalse((self.root / 'knowledge.json').exists())

    def test_file_publish_failure_restores_previous_credential_and_config(self):
        payload = DatabaseSettings(enabled=True).model_dump() | {'password': 'old-password'}
        self.assertEqual(self.client.put('/api/knowledge/settings', json=payload).status_code, 200)
        original = dict(self.store)
        payload['password'] = 'replacement-password'
        with patch('backend.knowledge.config.Path.replace', side_effect=OSError('cannot publish')):
            response = self.client.put('/api/knowledge/settings', json=payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store, original)
        self.assertTrue(read_settings(self.root).enabled)
        self.assertEqual(list(self.root.glob('knowledge-*.tmp')), [])

    def test_snapshot_restores_unicode_and_checks_missing_assets_without_disclosing_text(self):
        with service.connection() as connection:
            connection.execute("INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (
                'fixture', 'project-main', 'sensitive-name.pdf', '敏感标题', 'a'*64,
                str(self.root / 'assets' / 'missing.pdf'), 1, 1, service.utc_now()))
            connection.execute("INSERT INTO pages VALUES(?,?,?)", ('fixture', 1, '𠀀原文秘密'))
            connection.execute("INSERT INTO pages VALUES(?,?,?)", ('missing-material', 1, '孤立页面'))
        output = Path(self.directory.name) / 'audit'
        report = audit_legacy(self.root, output)
        self.assertTrue(report['restore_verified'])
        self.assertEqual(report['asset_files']['missing'], 1)
        self.assertEqual(report['orphan_counts']['pages.material_id'], 1)
        text = (output / 'audit.json').read_text(encoding='utf-8')
        self.assertNotIn('𠀀原文秘密', text)
        self.assertNotIn('sensitive-name', text)
        with closing(sqlite3.connect(output / 'restore-check.sqlite')) as connection:
            self.assertEqual(connection.execute("SELECT text FROM pages WHERE material_id='fixture'").fetchone()[0], '𠀀原文秘密')
        with self.assertRaises(ValueError):
            audit_legacy(self.root, self.root / 'unsafe')


if __name__ == '__main__':
    unittest.main()
