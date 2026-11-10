"""善本影印对照（backend/siku.py）的解析、接口与注册测试。"""
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import app as service
from backend import siku


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        return FakeResult(self.rows.get(params[0]) if params else None)


@contextmanager
def fake_connection(rows):
    yield FakeDatabase(rows)


class SikuTests(unittest.TestCase):
    """unittest 兼容的测试体；用独立的临时目录，不依赖真实工作区初始化。"""
    row = {'id': 'unit-qj-1', 'title': '前集第一卷', 'volume': '', 'category': ''}

    def setUp(self):
        self.scratch = Path(__file__).resolve().parents[1] / '.local' / 'siku-test-runs' / uuid.uuid4().hex
        self.scratch.mkdir(parents=True)
        self.pdfdir = self.scratch / 'pdfs'
        self.pdfdir.mkdir()
        (self.pdfdir / siku.INDEX_NAME).write_text(
            '全书卷序,集,卷次,起始G页,结束G页,页数,卷界置信,文件\r\n'
            '1,前集,1,133,154,22,ocr,qj-001.pdf\r\n'
            '70,後集,3,1,2,2,ocr,hj-003.pdf\r\n',
            encoding='utf-8-sig')
        (self.pdfdir / 'qj-001.pdf').write_bytes(b'%PDF-1.4\n% siku fixture\n')
        (self.pdfdir / 'hj-003.pdf').write_bytes(b'%PDF-1.4\n% siku fixture 2\n')
        self.previous = os.environ.get('SHIXUE_SIKU_PDF_DIR')
        os.environ['SHIXUE_SIKU_PDF_DIR'] = str(self.pdfdir)
        siku._CACHE['key'] = None
        rows = {key: dict(value) for key, value in {'unit-qj-1': self.row,
                                                    'unit-hj-3': {'id': 'unit-hj-3', 'title': '後集第三卷', 'volume': '', 'category': ''},
                                                    'unit-front': {'id': 'unit-front', 'title': '卷首材料', 'volume': '', 'category': ''}}.items()}
        self.client = TestClient(self.app_with(rows))

    def app_with(self, rows):
        app = FastAPI()
        siku.register(app, lambda: fake_connection(rows))
        return app

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('SHIXUE_SIKU_PDF_DIR', None)
        else:
            os.environ['SHIXUE_SIKU_PDF_DIR'] = self.previous
        siku._CACHE['key'] = None
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_parse_volume_title_variants(self):
        self.assertEqual(siku.parse_volume_title('前集第一卷'), ('前集', 1))
        self.assertEqual(siku.parse_volume_title('後集第三卷'), ('後集', 3))
        self.assertEqual(siku.parse_volume_title('古今合璧事类备要前集卷二十五'), ('前集', 25))
        self.assertEqual(siku.parse_volume_title('外集第66卷'), ('外集', 66))
        self.assertIsNone(siku.parse_volume_title('卷首材料'))
        self.assertEqual(siku.parse_number('九十四'), 94)
        self.assertEqual(siku.parse_number('二十一'), 21)
        self.assertEqual(siku.parse_number('十'), 10)

    def test_unit_match_and_pdf_file(self):
        match = self.client.get('/api/units/unit-qj-1/siku').json()
        self.assertTrue(match['available'])
        self.assertEqual(match['file'], 'qj-001.pdf')
        self.assertEqual((match['ji'], match['juan']), ('前集', 1))
        response = self.client.get('/api/units/unit-qj-1/siku/file')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-1.4'))

        other = self.client.get('/api/units/unit-hj-3/siku').json()
        self.assertTrue(other['available'])
        self.assertEqual(other['file'], 'hj-003.pdf')

        self.assertFalse(self.client.get('/api/units/unit-front/siku').json()['available'])
        self.assertEqual(self.client.get('/api/units/unit-front/siku/file').status_code, 404)
        self.assertEqual(self.client.get('/api/units/not-exists/siku').status_code, 404)

    def test_volume_catalog_and_manual_file(self):
        catalog = self.client.get('/api/siku/volumes').json()
        self.assertTrue(catalog['available'])
        self.assertEqual(catalog['volume_count'], 2)
        self.assertEqual([volume['seq'] for volume in catalog['volumes']], [1, 70])
        self.assertEqual(self.client.get('/api/siku/volumes/1/file').status_code, 200)
        self.assertEqual(self.client.get('/api/siku/volumes/999/file').status_code, 404)

    def test_registered_in_application(self):
        paths = set(service.app.openapi()['paths'])
        self.assertIn('/api/units/{unit_id}/siku', paths)
        self.assertIn('/api/units/{unit_id}/siku/file', paths)
        self.assertIn('/api/siku/volumes', paths)
        self.assertIn('/api/siku/volumes/{seq}/file', paths)


if __name__ == '__main__':
    unittest.main()
