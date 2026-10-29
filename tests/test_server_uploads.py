"""Opt-in upload-to-library checks using a disposable native PostgreSQL cluster."""
import os
import shutil
import unittest
import uuid
from unittest.mock import patch

from backend import app as application
from backend.knowledge import repository as r
from backend.knowledge.document_imports import process_batch
from backend.knowledge.ingestion import process_import
from backend.knowledge.worker import worker
from tests.test_research_assistant import ResearchNative as _Fixture


@unittest.skipUnless(os.name == 'nt' and os.environ.get('SHIXUE_MANAGED_TESTS') == '1', '需要独立内置 PostgreSQL')
class UploadedLibraryNative(unittest.TestCase):
    setUpClass = classmethod(_Fixture.setUpClass.__func__)
    tearDownClass = classmethod(_Fixture.tearDownClass.__func__)
    document = _Fixture.document
    fixture_setup = _Fixture.setUp

    def setUp(self):
        self.fixture_setup()
        self.mode = patch.object(application.service, 'mode', 'server')
        self.embedded = patch.object(application.service, 'embedded_workers', False)
        self.enabled = patch.object(worker, 'enabled', False)
        self.mode.start(); self.embedded.start(); self.enabled.start()

    def tearDown(self):
        self.client.close()
        self.enabled.stop(); self.embedded.stop(); self.mode.stop()

    def test_uploaded_directory_becomes_readable_library_with_authoritative_quotes(self):
        files = [('卷一/甲.txt', '财政赋税原文甲'), ('卷二/乙.txt', '财政赋税原文乙')]
        manifest = [{'relative_path': path, 'size': len(body.encode())} for path, body in files]
        upload = self.client.post('/api/uploads', json={'mode': 'directory', 'label': '客户端目录', 'files': manifest}).json()
        for index, (_, body) in enumerate(files):
            response = self.client.put(f"/api/uploads/{upload['id']}/files/{index}?offset=0", content=body.encode())
            self.assertEqual(response.status_code, 200, response.text)
        preview = self.client.post(f"/api/uploads/{upload['id']}/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual({item['relative_path'] for item in preview.json()['preview']}, {path for path, _ in files})
        category = '远程上传-' + uuid.uuid4().hex
        start = self.client.post(f"/api/knowledge/document-imports/{preview.json()['id']}/start", json={'category_name': category, 'item_type': 'article'})
        self.assertEqual(start.status_code, 200, start.text)
        self.assertFalse(worker.thread and worker.thread.is_alive(), 'API must not launch an embedded worker')
        result = process_batch(self.root, {'batch_id': preview.json()['id']}, lambda: None, lambda *_: None)
        self.assertEqual(result['succeeded'], 2)
        with r.database.transaction(self.root) as db:
            imports = r.rows(db, "SELECT payload FROM kb_jobs WHERE kind='import' AND state='queued'")
        for job in imports:
            process_import(self.root, job['payload'], lambda: None, lambda *_: None)
        group = start.json()['group_id']
        listing = self.client.get('/api/knowledge/document-tree', params={'group_id': group, 'path': '卷一'}).json()
        self.assertEqual(len(listing['files']), 1)
        document = self.client.get('/api/knowledge/document-entries/' + listing['files'][0]['id']).json()
        self.assertGreater(document['pages'], 0)
        with r.database.transaction(self.root) as db:
            block = r.one(db, 'SELECT id FROM kb_blocks WHERE revision_id=(SELECT active_revision FROM kb_editions WHERE id=:edition) AND asset_id=:asset ORDER BY page_index LIMIT 1', edition=document['edition_id'], asset=document['asset_id'])
            anchor = r.anchor(db, block['id'], 0, 4)
        exported = self.client.post('/api/knowledge/quotes/export', json={'anchor_ids': [str(anchor['id'])]})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn('财政赋税', exported.json()['content'])
        self.assertIn('出处：', exported.json()['content'])

    def test_portable_identity_keeps_postgres_workspace_rows_visible(self):
        from backend.identity import workspace_key
        moved = self.root.parent / ('moved-' + uuid.uuid4().hex)
        moved.mkdir()
        shutil.copyfile(self.root / 'workspace-identity.json', moved / 'workspace-identity.json')
        with r.database.transaction(self.root) as db:
            from backend.knowledge.assistant_registry import ensure_defaults
            ensure_defaults(db)
            expected = r.execute(db, "SELECT count(*) FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace')").scalar_one()
        # Both paths address the same isolated cluster; only the workspace path changes.
        url = self.product.manager.url.render_as_string(hide_password=False)
        with patch.dict(os.environ, {'SHIXUE_KB_DATABASE_URL': url}):
            with r.database.transaction(moved) as db:
                actual = r.execute(db, "SELECT count(*) FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace')").scalar_one()
        self.assertGreater(expected, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(workspace_key(moved), workspace_key(self.root))


del _Fixture
