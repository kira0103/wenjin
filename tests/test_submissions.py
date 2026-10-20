import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import submissions as s


class SubmissionsTest(unittest.TestCase):
    def setUp(self):
        temporary = Path(__file__).resolve().parents[1] / '.local' / 'temporary'
        temporary.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary)
        self.path = Path(self.temp.name) / 'library.sqlite'

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback(); raise
            finally:
                db.close()

        self.connection = connection
        with connection() as db:
            db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT);
              INSERT INTO projects VALUES('p','测试研究');
              CREATE TABLE artifacts(id TEXT PRIMARY KEY, project_id TEXT, title TEXT);
              CREATE TABLE research_units(id TEXT PRIMARY KEY, project_id TEXT, title TEXT);
              INSERT INTO research_units VALUES('u','p','成果题目');''')
            s.migrate(db, seed=False)
        app = FastAPI()
        s.register(app, connection)
        self.client = TestClient(app)
        self.base = '/api/submissions'

    def tearDown(self):
        self.client.close(); self.temp.cleanup()

    def post(self, path, body, status=200):
        r = self.client.post(self.base + path, json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def put(self, path, body, status=200):
        r = self.client.put(self.base + path, json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def journal(self, name='历史研究'):
        return self.post('/journals', {'name': name, 'website': 'https://example.org'})

    def paper(self, **fields):
        return self.post('/papers', {'title': '明清社会研究', 'status': 'ready', **fields})

    def action(self, paper, action, status=200, **fields):
        return self.post('/papers/' + paper['id'] + '/actions', {
            'action': action, 'revision': paper['revision'], 'date': date.today().isoformat(), **fields}, status)

    def overview(self):
        return self.client.get(self.base + '/overview').json()

    def import_csv(self, content, status=200):
        body = {'content': content, 'label': '测试目录'}
        report = self.post('/import/preview', body)
        return self.post('/import', {**body, 'fingerprint': report['fingerprint']}, status)

    def test_optional_seed_and_restart_idempotency(self):
        seed = Path(self.temp.name) / 'journals.csv'
        seed.write_text(','.join(s.HEADERS) + '\n示例甲刊,历史学,测试,,\n示例乙刊,文学,测试,,\n', encoding='utf-8-sig')
        with patch.object(s, 'SEED', seed), self.connection() as db:
            s.migrate(db)
            s.migrate(db)
        self.assertEqual(len(self.overview()['journals']), 2)
        self.assertEqual(len(self.overview()['datasets']), 1)
        self.assertEqual(self.overview()['papers'], [])
        self.assertEqual(len({j['discipline'] for j in self.overview()['journals']}), 2)
        for encoding in ('utf-8', 'utf-8-sig'):
            imported = self.import_csv(seed.read_bytes().decode(encoding))
            self.assertTrue(imported['already_imported'])
            self.assertEqual(imported['imported'], 0)
        self.assertEqual(len(self.overview()['datasets']), 1)

    def test_missing_optional_seed_starts_with_empty_catalog(self):
        with patch.object(s, 'SEED', Path(self.temp.name) / 'missing.csv'), self.connection() as db:
            s.migrate(db)
        self.assertEqual(self.overview()['journals'], [])
        self.assertEqual(self.overview()['datasets'], [])

    def test_editable_card_and_import_preserve_manual_fields_and_identity(self):
        content = ','.join(s.HEADERS) + '\n历史研究,历史学,C刊,https://example.org,https://example.org/submit\n'
        self.import_csv(content)
        journal = self.overview()['journals'][0]
        edited = self.put('/journals/'+journal['id'], {**journal, 'name': '历史研究（自定）', 'level': '自定等级',
                         'discipline': '中国史', 'submission_url': '', 'notes': '手工备注', 'favorite': True})
        changed = content.replace('C刊', 'C扩')
        self.assertEqual(self.import_csv(changed)['new'], 0)
        current = self.overview()['journals']
        self.assertEqual(len(current), 1)
        for key in ('id', 'name', 'level', 'discipline', 'submission_url', 'notes', 'favorite'):
            self.assertEqual(current[0][key], edited[key])
        sources = self.client.get(self.base+'/journals/'+journal['id']).json()['sources']
        self.assertEqual(len(sources), 2)
        self.assertEqual({x['original']['level'] for x in sources}, {'C刊', 'C扩'})
        self.assertEqual(self.import_csv(changed)['imported'], 0)

    def test_import_errors_are_atomic_and_fingerprint_checked(self):
        content = ','.join(s.HEADERS) + '\n甲刊,历史,C刊,https://example.org,\n乙刊,历史,C刊,javascript:alert(1),\n'
        self.import_csv(content, 422)
        self.assertEqual(self.overview()['journals'], [])
        self.post('/import', {'content': content, 'fingerprint': 'wrong'}, 409)
        duplicate = ','.join(s.HEADERS) + '\n甲刊,历史,C刊,,\n甲刊,历史,C扩,,\n'
        self.import_csv(duplicate, 422)
        self.assertEqual(self.overview()['datasets'], [])

    def test_journal_url_validation_collision_and_stale_edit(self):
        for bad in ('javascript:alert(1)', 'file:///c:/secret', 'https://user:pass@example.org', 'https://example.org\\x'):
            self.post('/journals', {'name': '坏链接', 'website': bad}, 422)
        journal = self.journal()
        self.post('/journals', {'name': '历史研究 '}, 409)
        self.put('/journals/'+journal['id'], {**journal, 'notes': '已保存'})
        self.put('/journals/'+journal['id'], {**journal, 'notes': '旧版本'}, 409)
        self.assertEqual(self.overview()['journals'][0]['notes'], '已保存')

    def test_reject_resubmit_revision_publish_full_history_and_snapshot(self):
        first, second = self.journal('甲刊'), self.journal('乙刊')
        paper = self.paper(project_id='p', source_kind='unit', source_id='u')
        self.assertEqual(paper['source_title'], '成果题目')
        paper = self.action(paper, 'submit', journal_id=first['id'])
        self.action(paper, 'submit', 409, journal_id=second['id'])
        self.put('/journals/'+first['id'], {**first, 'name': '甲刊新名'})
        paper = self.action(paper, 'reject', note='选题不符')
        paper = self.action(paper, 'submit', journal_id=second['id'])
        paper = self.action(paper, 'request_revision', note='请补充材料')
        paper = self.action(paper, 'revise', note='补充档案')
        self.assertEqual(paper['attempts'][1]['round'], 2)
        paper = self.action(paper, 'accept')
        paper = self.action(paper, 'publish')
        self.assertEqual(paper['status'], 'published')
        self.assertEqual(paper['attempts'][0]['journal_name'], '甲刊')
        self.assertEqual(paper['attempts'][0]['status'], 'rejected')
        self.assertEqual(paper['attempts'][1]['status'], 'published')
        self.assertEqual([e['kind'] for e in paper['events']], ['created', 'submit', 'reject', 'submit', 'request_revision', 'revise', 'accept', 'publish'])
        self.assertEqual(set(self.overview()['papers'][0]['journal_ids']), {first['id'], second['id']})
        # A fresh client reads the same durable events.
        self.assertEqual(self.client.get(self.base+'/papers/'+paper['id']).json()['events'], paper['events'])

    def test_invalid_association_dates_state_and_concurrency_do_not_add_history(self):
        self.post('/papers', {'title': '错误关联', 'source_kind': 'unit', 'source_id': 'u'}, 422)
        paper = self.paper()
        journal = self.journal()
        self.action(paper, 'submit', 422, journal_id=journal['id'], date=(date.today()+timedelta(days=1)).isoformat())
        self.action(paper, 'publish', 409)
        original = paper
        paper = self.action(paper, 'submit', journal_id=journal['id'])
        self.action(original, 'reject', 409)
        self.action(paper, 'reject', 422, date=(date.today()-timedelta(days=1)).isoformat())
        fresh = self.client.get(self.base+'/papers/'+paper['id']).json()
        self.assertEqual(len(fresh['events']), 2)
        self.assertEqual(fresh['status'], 'review')

    def test_withdraw_archive_and_deleted_source_preserve_history(self):
        journal = self.journal()
        paper = self.paper(project_id='p', source_kind='unit', source_id='u')
        paper = self.action(paper, 'submit', journal_id=journal['id'])
        paper = self.action(paper, 'withdraw', note='主动撤回')
        self.assertEqual(paper['attempts'][0]['status'], 'withdrawn')
        with self.connection() as db:
            db.execute('DELETE FROM research_units'); db.execute('DELETE FROM projects')
        paper = self.put('/papers/'+paper['id'], {**paper, 'archived': True})
        self.assertEqual(paper['source_title'], '成果题目')
        self.action(paper, 'submit', 409, journal_id=journal['id'])
        self.assertEqual(len(paper['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
