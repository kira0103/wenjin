import io
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as service


def word_fixture():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>类书卷一</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格中的底本原文</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>其后正文</w:t></w:r></w:p></w:body></w:document>''')
        archive.writestr('word/footnotes.xml', '''<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>脚注中的版本说明</w:t></w:r></w:p></w:footnote></w:footnotes>''')
    return output.getvalue()


class ResearchFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        service.initialize(self.root)
        service.service.token = 'test-token'
        self.client = TestClient(service.app, headers={'X-App-Token': 'test-token'})
        self.secret = patch.object(service.keyring, 'get_password', return_value=None)
        self.secret.start()
        self.project = self.client.post('/api/projects', json={'name': '类书点校', 'project_type': 'collation'}).json()
        self.other = self.client.post('/api/projects', json={'name': '翻译研究', 'project_type': 'translation'}).json()

    def tearDown(self):
        for thread in list(service.service.tasks):
            thread.join(5)
        service.service.tasks.clear()
        self.secret.stop()
        self.client.close()
        self.directory.cleanup()

    def upload(self, text='天地玄黄，宇宙洪荒。', project=None, filename='底本.txt', data=None, entry=None):
        params = {'filename': filename}
        if project:
            params.update(project_id=project, role='base')
        if entry:
            params['entry_id'] = entry
        result = self.client.post('/api/materials/import', params=params, content=data if data is not None else text.encode('utf-8'), headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def unit(self, material, **kwargs):
        result = self.client.post(f"/api/projects/{self.project['id']}/units", json={'title': '天部第一条', 'volume': '卷一', 'category': '天部', 'material_id': material['id'], 'page_from': 1, 'page_to': 1, **kwargs})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def save(self, unit, **kwargs):
        return self.client.put('/api/units/' + unit['id'], json={'expected_revision': unit['revision'], 'content': unit['content'], 'notes': unit['notes'], 'status': unit['status'], **kwargs})

    def test_global_import_requires_no_project_and_preserves_bytes(self):
        material = self.upload()
        self.assertEqual(material['project_ids'], [])
        self.assertEqual(self.client.get('/api/materials').json(), [])
        self.assertEqual(self.client.get('/api/global-materials').json()[0]['id'], material['id'])
        self.assertEqual(self.client.get(f"/api/materials/{material['id']}/file").content, '天地玄黄，宇宙洪荒。'.encode())
        entry = self.client.get('/api/library-items').json()[0]
        self.assertEqual(entry['material_ids'], [material['id']])

    def test_duplicate_bytes_in_another_entry_produce_recoverable_conflict(self):
        material = self.upload(project=self.project['id'])
        other = self.client.post('/api/library-items', json={'title': '其他版本'}).json()
        response = self.client.post('/api/materials/import', params={'filename': 'duplicate.txt', 'entry_id': other['id'], 'project_id': self.project['id']}, content='天地玄黄，宇宙洪荒。'.encode())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.client.get('/api/global-materials').json()), 1)
        self.assertEqual(self.client.get('/api/materials/' + material['id']).status_code, 200)

    def test_same_file_is_shared_and_roles_are_project_specific(self):
        a = self.upload(project=self.project['id'])
        b = self.upload(project=self.other['id'])
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['duplicate'])
        self.client.put(f"/api/projects/{self.other['id']}/materials/{b['id']}", json={'roles': ['citation', 'reference'], 'scope_note': '只使用卷一'})
        ma = self.client.get('/api/materials', params={'project_id': self.project['id']}).json()[0]
        mb = self.client.get('/api/materials', params={'project_id': self.other['id']}).json()[0]
        self.assertEqual(ma['roles'], ['base'])
        self.assertEqual(mb['roles'], ['citation', 'reference'])
        self.assertEqual(len(self.client.get('/api/global-materials').json()), 1)

    def test_unlink_retains_original_other_project_and_historical_source(self):
        material = self.upload(project=self.project['id'])
        self.upload(project=self.other['id'])
        unit = self.unit(material)
        self.client.delete(f"/api/projects/{self.project['id']}/materials/{material['id']}")
        self.assertEqual(self.client.get('/api/materials', params={'project_id': self.project['id']}).json(), [])
        self.assertEqual(len(self.client.get('/api/materials', params={'project_id': self.other['id']}).json()), 1)
        self.assertEqual(self.client.get('/api/units/' + unit['id']).json()['original_text'], unit['original_text'])
        self.assertEqual(self.client.get('/api/materials/' + material['id'] + '/file').status_code, 200)
        service.initialize(self.root)
        self.assertEqual(self.client.get('/api/materials', params={'project_id': self.project['id']}).json(), [])

    def test_docx_preserves_body_table_and_notes_in_order(self):
        m = self.upload(filename='原文.docx', data=word_fixture())
        detail = self.client.get('/api/materials/' + m['id']).json()
        text = detail['pages'][0]['text']
        self.assertTrue(text.index('类书卷一') < text.index('表格中的底本原文') < text.index('其后正文'))
        self.assertIn('脚注中的版本说明', text)
        self.assertEqual(m['position_kind'], 'section')
        self.assertEqual(m['file_format'], 'docx')

    def test_txt_encodings_and_long_segments_preserve_all_text(self):
        text = '简体繁體史料\n' * 3000
        for encoding in ['utf-8-sig', 'utf-16', 'gb18030']:
            m = self.upload(filename=encoding + '.txt', data=text.encode(encoding))
            detail = self.client.get('/api/materials/' + m['id']).json()
            self.assertEqual(''.join(p['text'] for p in detail['pages']), text)
            self.assertGreater(len(detail['pages']), 1)
            self.assertEqual(m['position_kind'], 'section')

    def test_invalid_documents_and_project_are_rejected_before_asset_write(self):
        for filename, data in [('bad.pdf', b'not a PDF'), ('bad.docx', b'not a zip'), ('bad.txt', b'\x00\x00\x01binary'), ('file.exe', b'wrong')]:
            self.assertEqual(self.client.post('/api/materials/import', params={'filename': filename}, content=data).status_code, 400)
        self.assertEqual(self.client.post('/api/materials/import', params={'filename': 'a.txt', 'project_id': 'missing'}, content=b'text').status_code, 404)
        self.assertEqual(list((self.root / 'assets').rglob('*')), [])
        self.assertEqual(self.client.get('/api/global-materials').json(), [])

    def test_book_without_file_can_join_project_and_later_attach_volumes(self):
        entry = self.client.post('/api/library-items', json={'title': '某类书', 'item_type': 'ancient', 'version_label': '甲刻本', 'genre': '类书'}).json()
        self.client.put(f"/api/projects/{self.project['id']}/library-items/{entry['id']}", json={'roles': ['base']})
        first = self.upload('卷一正文', entry=entry['id'])
        second = self.upload('卷二正文', entry=entry['id'])
        self.assertNotEqual(first['id'], second['id'])
        files = self.client.get('/api/materials', params={'project_id': self.project['id']}).json()
        self.assertEqual(len(files), 2)
        self.assertTrue(all(m['roles'] == ['base'] for m in files))
        result = self.client.get('/api/library-items').json()[0]
        self.assertEqual(set(result['material_ids']), {first['id'], second['id']})
        self.assertEqual(result['version_label'], '甲刻本')

    def test_project_template_change_retains_units_and_associations(self):
        m = self.upload(project=self.project['id'])
        unit = self.unit(m)
        self.client.put('/api/projects/' + self.project['id'], json={**self.project, 'project_type': 'translation', 'rules': '保留专名'})
        bootstrap = self.client.get('/api/bootstrap', params={'project_id': self.project['id']}).json()
        self.assertEqual(bootstrap['units'][0]['id'], unit['id'])
        self.assertEqual(bootstrap['materials'][0]['id'], m['id'])
        p = next(p for p in bootstrap['projects'] if p['id'] == self.project['id'])
        self.assertEqual(p['project_type'], 'translation')
        self.assertEqual(self.client.post('/api/projects', json={'name': '错误模板', 'project_type': 'unknown'}).status_code, 422)

    def test_archive_and_restore_keep_materials_and_work(self):
        material = self.upload(project=self.project['id'])
        unit = self.unit(material)
        result = self.client.post('/api/projects/' + self.project['id'] + '/archive')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get('/api/units/' + unit['id']).json()['content'], unit['content'])
        self.client.post('/api/projects/' + self.project['id'] + '/restore')
        bootstrap = self.client.get('/api/bootstrap', params={'project_id': self.project['id']}).json()
        self.assertEqual(bootstrap['materials'][0]['id'], material['id'])
        self.assertEqual(bootstrap['units'][0]['id'], unit['id'])

    def test_translation_starts_with_separate_source_and_empty_draft(self):
        material = self.upload(project=self.project['id'])
        unit = self.unit(material, kind='translation')
        self.assertEqual(unit['content'], '')
        self.assertIn('天地玄黄', unit['original_text'])

    def test_historical_note_survives_unlink_of_witness(self):
        material = self.upload(project=self.project['id'])
        witness = self.upload('参校原文', project=self.project['id'])
        unit = self.unit(material)
        note = {'id': 'v', 'kind': 'variant', 'witness_id': witness['id'], 'witness_reading': '乙本读法', 'status': 'accepted'}
        saved = self.save(unit, notes=[note]).json()
        self.client.delete(f"/api/projects/{self.project['id']}/materials/{witness['id']}")
        self.assertEqual(self.save(saved, content='继续修订').status_code, 200)

    def test_draft_versions_conflict_approval_and_edit_after_approval(self):
        m = self.upload(project=self.project['id'])
        unit = self.unit(m)
        changed = self.save(unit, content='人工修订稿').json()
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(changed['original_text'], unit['original_text'])
        self.assertEqual(self.save(unit, content='旧窗口覆盖').status_code, 409)
        approved = self.save(changed, status='approved', confirm_review=True).json()
        self.assertEqual(approved['status'], 'approved')
        edited = self.save(approved, content='审定后再修订').json()
        self.assertEqual(edited['status'], 'review')
        old = self.client.get(f"/api/units/{unit['id']}/versions/2").json()
        self.assertEqual(old['content'], '人工修订稿')
        service.initialize(self.root)
        self.assertEqual(self.client.get('/api/units/' + unit['id']).json()['content'], '审定后再修订')

    def test_pending_note_prevents_approval_and_keeps_source(self):
        unit = self.unit(self.upload(project=self.project['id']))
        note = {'id': 'question-a', 'kind': 'question', 'location': '卷一叶三', 'base_reading': '某字', 'decision': '待查', 'status': 'pending'}
        response = self.save(unit, notes=[note], status='approved', confirm_review=True)
        self.assertEqual(response.status_code, 400)
        changed = self.save(unit, notes=[note], status='review').json()
        note['status'] = 'accepted'
        self.assertEqual(self.save(changed, notes=[note], status='approved', confirm_review=True).json()['status'], 'approved')

    def test_unit_cannot_use_foreign_material_and_note_cannot_use_foreign_witness(self):
        foreign = self.upload(project=self.other['id'])
        response = self.client.post(f"/api/projects/{self.project['id']}/units", json={'title': '非法', 'material_id': foreign['id']})
        self.assertEqual(response.status_code, 400)
        material = self.upload('另一底本', project=self.project['id'])
        unit = self.unit(material)
        self.assertEqual(self.save(unit, notes=[{'id': 'a', 'kind': 'variant', 'witness_id': foreign['id']}]).status_code, 400)

    def test_model_task_does_not_approve_draft_and_records_input_revision(self):
        material = self.upload(project=self.project['id'])
        unit = self.unit(material)
        self.client.put('/api/settings', json={'base_url': 'http://127.0.0.1:9999/v1', 'model': 'test'})
        with patch.object(service, 'generate_text', return_value='模型建议，尚需人工复核。'):
            response = self.client.post('/api/tasks', json={'project_id': self.project['id'], 'material_id': material['id'], 'unit_id': unit['id'], 'source_text': unit['content'], 'task_type': 'punctuate', 'page_from': 1, 'page_to': 1})
            self.assertEqual(response.status_code, 200, response.text)
            for thread in list(service.service.tasks):
                thread.join(5)
        task = self.client.get('/api/tasks/' + response.json()['id']).json()
        self.assertEqual(task['status'], 'completed', task['error'])
        self.assertEqual(task['artifact']['source_context']['input_revision'], 1)
        self.assertEqual(task['artifact']['source_context']['unit_id'], unit['id'])
        fresh = self.client.get('/api/units/' + unit['id']).json()
        self.assertEqual(fresh['status'], 'working')
        self.assertEqual(fresh['content'], unit['content'])


class LegacyMigrationTests(unittest.TestCase):
    def test_upgrade_backs_up_and_preserves_material_task_source_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with closing(sqlite3.connect(root / 'library.sqlite')) as db:
                db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
                  CREATE TABLE materials(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,filename TEXT NOT NULL,title TEXT NOT NULL,sha256 TEXT NOT NULL,asset_path TEXT NOT NULL,page_count INTEGER NOT NULL,extraction_coverage REAL NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,sha256));''')
                db.execute("INSERT INTO projects VALUES('old-project','旧项目','',?)", (service.utc_now(),))
                db.execute("INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES('old-source','old-project','old.pdf','旧底本','hash','old.pdf',3,1,?)", (service.utc_now(),))
                db.commit()
            service.initialize(root)
            with service.connection() as db:
                m = service.research.materials_for(db, 'old-project')[0]
                self.assertEqual(m['id'], 'old-source')
                self.assertEqual(m['file_format'], 'pdf')
                self.assertEqual(m['project_ids'], ['old-project'])
                self.assertEqual(db.execute("SELECT project_type FROM projects WHERE id='old-project'").fetchone()[0], 'general')
                db.execute("DELETE FROM project_materials WHERE project_id='old-project'")
            service.initialize(root)
            with service.connection() as db:
                self.assertEqual(service.research.materials_for(db, 'old-project'), [])
            with closing(sqlite3.connect(root / 'backups' / 'before-0.2.0.sqlite')) as db:
                self.assertEqual(db.execute('SELECT id FROM materials').fetchone()[0], 'old-source')


if __name__ == '__main__':
    unittest.main()
