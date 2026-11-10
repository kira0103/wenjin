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
        self.assertEqual(warnings['pua'], 0)
        self.assertEqual(warnings['mapped'], 1)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
            styles = archive.read('word/styles.xml').decode('utf-8')
            header = archive.read('word/header1.xml').decode('utf-8')
            footer = archive.read('word/footer1.xml').decode('utf-8')
        for text in ('前集第一卷', '天文門', '事類', '羣物之祖'):
            self.assertIn(text, document)
        self.assertIn('黑体', styles)
        self.assertIn('《合璧事類備要》', header)
        self.assertIn('PAGE', footer)
        # 私用区字已还原成扩展 B 区真字，并使用 SimSun-ExtB 字体。
        self.assertIn('𤣥', document)
        self.assertIn('SimSun-ExtB', document)
        self.assertNotIn('\uea20', document)
        # Pending notes are never included.
        self.assertNotIn('待核', document)

    def test_unmapped_pua_becomes_box_and_is_listed(self):
        content = CONTENT + '　　附考\uedab。\r\n'
        data, warnings = export_docx.build(content, '前集第一卷', '合璧事類備要_点校', include_unresolved_list=True)
        self.assertEqual(warnings['pua'], 1)
        self.assertEqual(warnings['mapped'], 1)
        self.assertEqual(warnings['unresolved'][0]['codepoint'], 'U+EDAB')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('□', document)
        self.assertNotIn('\uedab', document)
        self.assertIn('未能自动还原', document)
        self.assertIn('U+EDAB', document)

    def test_pua_mapping_data_covers_known_codes(self):
        entries = export_docx.pua_entries()
        self.assertEqual(entries['EA20']['char'], '𤣥')
        self.assertEqual(entries['EA5B']['char'], '𫝊')
        self.assertIn('description', entries['EDAB'])

    def test_book_title_uses_project_not_first_quoted_source(self):
        content = ('　　別集第七十一卷\r\n　　宋　謝維新　撰\r\n　　事類\r\n'
                   '　　左氏　《左傳》曰：國之大事，在祀與戎。\r\n')
        data, _ = export_docx.build(content, '別集第七十一卷', '合璧事類備要_点校')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            header = archive.read('word/header1.xml').decode('utf-8')
            document = archive.read('word/document.xml').decode('utf-8')
            core = archive.read('docProps/core.xml').decode('utf-8')
        self.assertIn('《合璧事類備要》', header)
        self.assertNotIn('《左傳》', header)
        self.assertIn('《合璧事類備要》', document)
        self.assertIn('合璧事類備要 別集第七十一卷 點校稿', core)

    def test_generic_project_falls_back_to_content_book_title(self):
        content = '　　某卷\r\n　　事類\r\n　　《春秋》曰：春王正月。\r\n'
        data, _ = export_docx.build(content, '某卷', '我的研究')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            header = archive.read('word/header1.xml').decode('utf-8')
        self.assertIn('《春秋》', header)
        self.assertEqual(export_docx.filename_for('我的研究', '某卷', content).split('_')[0], '春秋')

    def test_filename_is_sanitized_and_dated(self):
        filename = export_docx.filename_for('合璧事類備要_点校', '前集第一卷', CONTENT)
        self.assertTrue(filename.endswith('.docx'))
        self.assertIn('前集第一卷', filename)
        self.assertNotIn('/', filename)
        self.assertIn(datetime.now().strftime('%Y'), filename)

    def test_filenames_are_uniform_across_volumes(self):
        first = export_docx.filename_for('合璧事類備要_点校', '別集第七十一卷', '《左傳》曰云云。')
        second = export_docx.filename_for('合璧事類備要_点校', '外集第二十五卷', '《周官》曰云云。')
        third = export_docx.filename_for('合璧事類備要_点校', '卷首材料', '《欽定四庫全書》提要。')
        for name, unit in ((first, '別集第七十一卷'), (second, '外集第二十五卷'), (third, '卷首材料')):
            self.assertTrue(name.startswith('合璧事類備要_'), name)
            self.assertIn(unit, name)
            self.assertTrue(name.endswith('.docx'))

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
        self.assertEqual(response.headers['x-wenjin-export-pua'], '0')
        self.assertEqual(response.headers['x-wenjin-export-warnings'], '0')
        self.assertEqual(response.headers['x-wenjin-export-mapped'], '1')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('word/document.xml', archive.namelist())
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('前集第一卷', document)
        self.assertIn('𤣥', document)
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
        self.assertEqual(response.headers['x-wenjin-export-pua'], '0')
        self.assertEqual(response.headers['x-wenjin-export-warnings'], '0')
        self.assertEqual(response.headers['x-wenjin-export-mapped'], '2')
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            self.assertEqual(len(names), 2)
            self.assertTrue(all(name.endswith('.docx') for name in names))
            self.assertEqual(len(set(names)), 2)
            self.assertEqual({name.split('_')[0] for name in names}, {'合璧事類備要'})
            self.assertTrue(any('前集第一卷' in name for name in names))
            self.assertTrue(any('前集第二卷' in name for name in names))
        empty = self.client.post('/api/exports/units/docx', json={'unit_ids': []})
        self.assertEqual(empty.status_code, 422)

    def test_batch_export_writes_unresolved_list(self):
        timestamp = service.utc_now()
        content = CONTENT + '　　附考\uedab。\r\n'
        with service.connection() as db:
            db.execute(
                "INSERT INTO research_units(id,project_id,kind,title,volume,category,material_id,page_from,page_to,location_label,original_text,content,notes_json,status,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('unit-docx-unresolved', self.project_id, 'collation', '前集第三卷', '', '', '', 5, 6, '',
                 content, content, '[]', 'review', 1, timestamp, timestamp),
            )
        response = self.client.post('/api/exports/units/docx', json={'unit_ids': [self.unit_id, 'unit-docx-unresolved']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['x-wenjin-export-pua'], '1')
        self.assertEqual(response.headers['x-wenjin-export-mapped'], '2')
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            self.assertIn('_私用区待人工处理清单.txt', names)
            report = bundle.read('_私用区待人工处理清单.txt').decode('utf-8')
        self.assertIn('U+EDAB', report)
        self.assertIn('待人工处理：1 处', report)


class BatchExportLimitTests(unittest.TestCase):
    def test_selection_accepts_whole_book_and_rejects_absurd_payload(self):
        from pydantic import ValidationError
        from backend.api.exports import UnitBatchExport
        whole_book = [f'unit-{index}' for index in range(367)]
        self.assertEqual(len(UnitBatchExport(unit_ids=whole_book).unit_ids), 367)
        too_many = [f'unit-{index}' for index in range(5001)]
        with self.assertRaises(ValidationError):
            UnitBatchExport(unit_ids=too_many)


if __name__ == '__main__':
    unittest.main()
