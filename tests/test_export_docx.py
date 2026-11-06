"""Unit tests for the single-volume Word manuscript export."""
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as service
from backend.services import export_docx

CONTENT = (
    '《古今合璧事類備要》前集卷一  \r\n'
    '　　宋　謝維新　撰\r\n'
    '　　天文門\r\n'
    '　　天\r\n'
    '　　事類\r\n'
    '羣物之祖　天者，羣物之祖也，故徧覆包含而无所殊。\r\n'
    '形如倚蓋　周髀家云：天形南高而北下，日出高，故見。北方\uea20武七宿。\r\n'
)


class BuildTests(unittest.TestCase):
    def test_builds_docx_with_structure_and_warning_header_data(self):
        data, warnings = export_docx.build(CONTENT, '前集第一卷', '合璧事類備要_点校')
        self.assertEqual(warnings['pua'], 1)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
            styles = archive.read('word/styles.xml').decode('utf-8')
            header = archive.read('word/header1.xml').decode('utf-8')
            footer = archive.read('word/footer1.xml').decode('utf-8')
        for text in ('前集第一卷', '天文門', '事類', '羣物之祖'):
            self.assertIn(text, document)
        self.assertIn('黑体', styles)
        self.assertIn('《古今合璧事類備要》', header)
        self.assertIn('PAGE', footer)
        # Pending notes are never included.
        self.assertNotIn('待核', document)

    def test_filename_is_sanitized_and_dated(self):
        filename = export_docx.filename_for('合璧事類備要_点校', '前集第一卷', CONTENT)
        self.assertTrue(filename.endswith('.docx'))
        self.assertIn('前集第一卷', filename)
        self.assertNotIn('/', filename)
        self.assertIn(datetime.now().strftime('%Y'), filename)

    def test_volume_title_and_author_are_not_duplicated(self):
        data, _ = export_docx.build(CONTENT, '前集第一卷', '合璧事類備要_点校')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        # The raw source title line must not become a body paragraph.
        self.assertNotIn('《古今合璧事類備要》前集卷一', document)
        self.assertIn('謝維新', document)
        self.assertIn('前集第一卷', document)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        service.initialize(self.root)
        service.service.token = 'export-docx-test'
        self.client = TestClient(service.app, headers={'X-App-Token': 'export-docx-test'})
        project = self.client.post('/api/projects', json={'name': '合璧事類備要_点校', 'project_type': 'collation'})
        self.assertEqual(project.status_code, 200, project.text)
        self.project_id = project.json()['id']
        self.unit_id = 'unit-docx-1'
        timestamp = service.utc_now()
        with service.connection() as db:
            db.execute(
                "INSERT INTO research_units(id,project_id,kind,title,volume,category,material_id,page_from,page_to,location_label,original_text,content,notes_json,status,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.unit_id, self.project_id, 'collation', '前集第一卷', '', '', '', 1, 2, '',
                 CONTENT, CONTENT, '[]', 'review', 1, timestamp, timestamp),
            )

    def tearDown(self):
        for thread in list(service.service.tasks):
            thread.join(timeout=5)
        service.service.tasks.clear()
        self.client.close()
        self.directory.cleanup()

    def export(self, revision=1, content=CONTENT):
        return self.client.post(
            f'/api/units/{self.unit_id}/export/docx',
            json={'expected_revision': revision, 'content': content, 'notes': [], 'status': 'review'},
        )

    def test_export_returns_word_attachment_with_warning_headers(self):
        response = self.export()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['content-type'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.assertIn('filename*=UTF-8', response.headers['content-disposition'])
        self.assertIn('.docx', response.headers['content-disposition'])
        self.assertEqual(response.headers['x-wenjin-export-pua'], '1')
        self.assertEqual(response.headers['x-wenjin-export-warnings'], '1')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('word/document.xml', archive.namelist())
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('前集第一卷', document)
        self.assertNotIn('待核', document)

    def test_stale_revision_is_rejected(self):
        response = self.export(revision=2)
        self.assertEqual(response.status_code, 409)

    def test_missing_unit_is_404(self):
        response = self.client.post('/api/units/not-found/export/docx', json={'expected_revision': 1, 'content': '', 'notes': [], 'status': 'review'})
        self.assertEqual(response.status_code, 404)

    def test_batch_export_packs_one_docx_per_unit(self):
        timestamp = service.utc_now()
        second = CONTENT.replace('前集卷一', '前集卷二').replace('天文門', '地理門')
        with service.connection() as db:
            db.execute(
                "INSERT INTO research_units(id,project_id,kind,title,volume,category,material_id,page_from,page_to,location_label,original_text,content,notes_json,status,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('unit-docx-2', self.project_id, 'collation', '前集第二卷', '', '', '', 3, 4, '',
                 second, second, '[]', 'review', 1, timestamp, timestamp),
            )
        response = self.client.post('/api/exports/units/docx', json={'unit_ids': [self.unit_id, 'unit-docx-2']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['content-type'], 'application/zip')
        self.assertIn('filename*=UTF-8', response.headers['content-disposition'])
        self.assertIn('.zip', response.headers['content-disposition'])
        self.assertEqual(response.headers['x-wenjin-export-pua'], '2')
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            self.assertEqual(len(names), 2)
            self.assertTrue(all(name.endswith('.docx') for name in names))
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(any('前集第一卷' in name for name in names))
            self.assertTrue(any('前集第二卷' in name for name in names))
        empty = self.client.post('/api/exports/units/docx', json={'unit_ids': []})
        self.assertEqual(empty.status_code, 422)


if __name__ == '__main__':
    unittest.main()
