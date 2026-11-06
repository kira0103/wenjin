"""Portal admin usage ledger aggregation tests (no real workspaces or services)."""
import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend import portal_usage
from backend.portal import create_app

PASSWORD = 'usage-ledger-password-123'

WORKSPACE_ALICE = 'a' * 32
WORKSPACE_BOB = 'b' * 32
WORKSPACE_CAROL = 'c' * 32
WORKSPACE_DAVE = 'd' * 32
WORKSPACE_ERIN = 'e' * 32

USAGE_SQL = """
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT '',
  feature TEXT NOT NULL,
  source_kind TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  request_key TEXT NOT NULL DEFAULT '',
  model_id TEXT NOT NULL DEFAULT '',
  model_name TEXT NOT NULL DEFAULT '',
  provider_name TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  total_tokens INTEGER,
  usage_status TEXT NOT NULL DEFAULT 'measured',
  latency_ms INTEGER,
  price_input_per_million REAL,
  price_output_per_million REAL,
  price_cached_per_million REAL,
  currency TEXT NOT NULL DEFAULT '',
  price_date TEXT NOT NULL DEFAULT '',
  estimated_cost REAL,
  cost_status TEXT NOT NULL DEFAULT 'unpriced',
  created_at TEXT NOT NULL,
  local_date TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_project_date ON usage_events(project_id, local_date);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_events(local_date);
CREATE INDEX IF NOT EXISTS idx_usage_feature ON usage_events(feature, local_date);
"""


def event(**overrides) -> dict:
    value = {'id': 'event-1', 'project_id': 'p-a1', 'feature': 'collation_batch',
             'source_kind': 'batch', 'source_id': 'batch-1', 'request_key': 'attempt-1',
             'model_id': 'model-1', 'model_name': '模型一', 'provider_name': 'provider-1',
             'model': 'fixture-model', 'input_tokens': 100, 'output_tokens': 50,
             'cached_tokens': 10, 'total_tokens': 150, 'usage_status': 'measured',
             'latency_ms': 10, 'price_input_per_million': 2.0, 'price_output_per_million': 4.0,
             'price_cached_per_million': 0.5, 'currency': 'CNY', 'price_date': '2026-10-01',
             'estimated_cost': 0.001, 'cost_status': 'estimated',
             'created_at': '2026-10-01T02:00:00+00:00', 'local_date': '2026-10-01',
             'meta_json': '{}'}
    value.update(overrides)
    return value


def make_workspace(root, workspace_id, projects, events, *, with_ledger=True):
    folder = Path(root) / 'workspaces' / workspace_id
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / 'library.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL)')
        db.executemany('INSERT OR REPLACE INTO projects(id,name) VALUES(?,?)', projects)
        if with_ledger:
            db.executescript(USAGE_SQL)
            for row in events:
                columns = list(row)
                db.execute('INSERT OR REPLACE INTO usage_events(' + ','.join(columns) + ') VALUES(' +
                           ','.join('?' for _ in columns) + ')', [row[column] for column in columns])
        db.commit()
    finally:
        db.close()


class FakeFleet:
    def close(self):
        pass

    def settings(self):
        return {}

    def state(self, _user):
        return {'state': 'not_started', 'metrics': {}}


class UsageFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.app = create_app(self.root, fleet=FakeFleet())
        self.accounts = self.app.state.accounts
        self.admin = self.accounts.create('admin', '管理员', PASSWORD, 'admin', require_change=False)
        self.alice = self.accounts.create('alice', '研究者甲', PASSWORD, require_change=False)
        self.bob = self.accounts.create('bobby', '研究者乙', PASSWORD, require_change=False)
        self.carol = self.accounts.create('carol', '研究者丙', PASSWORD, require_change=False)
        self.dave = self.accounts.create('dave', '研究者丁', PASSWORD, require_change=False)
        self.erin = self.accounts.create('erin', '研究者戊', PASSWORD, require_change=False)
        with self.accounts.connection() as db:
            db.execute('UPDATE portal_users SET workspace_id=? WHERE id=?', (WORKSPACE_ALICE, self.alice['id']))
            db.execute('UPDATE portal_users SET workspace_id=? WHERE id=?', (WORKSPACE_BOB, self.bob['id']))
            db.execute('UPDATE portal_users SET workspace_id=? WHERE id=?', (WORKSPACE_CAROL, self.carol['id']))
            db.execute('UPDATE portal_users SET workspace_id=? WHERE id=?', (WORKSPACE_DAVE, self.dave['id']))
            db.execute('UPDATE portal_users SET workspace_id=? WHERE id=?', (WORKSPACE_ERIN, self.erin['id']))
        make_workspace(self.root, WORKSPACE_ALICE, [('p-a1', '甲项目一'), ('p-a2', '甲项目二')], [
            event(),
            event(id='event-2', project_id='p-a2', feature='task', source_kind='task',
                  source_id='task-1', request_key='call-1', input_tokens=200, output_tokens=100,
                  cached_tokens=0, total_tokens=300, estimated_cost=0.002,
                  created_at='2026-10-03T03:00:00+00:00', local_date='2026-10-03'),
            event(id='event-3', project_id='p-a2', feature='task', source_kind='task',
                  source_id='task-2', request_key='call-1', model_id='model-2', model_name='模型二',
                  input_tokens=None, output_tokens=None, cached_tokens=None, total_tokens=None,
                  usage_status='unknown', estimated_cost=None, cost_status='unknown',
                  created_at='2026-10-03T04:00:00+00:00', local_date='2026-10-03'),
        ])
        make_workspace(self.root, WORKSPACE_BOB, [('p-b1', '乙项目')], [
            event(id='event-4', project_id='p-b1', feature='collation_debug', source_kind='debug',
                  source_id='debug-1', request_key='debug-1', input_tokens=10, output_tokens=5,
                  cached_tokens=0, total_tokens=15, estimated_cost=0.0005,
                  created_at='2026-10-02T03:00:00+00:00', local_date='2026-10-02'),
            event(id='event-5', project_id='p-b1', feature='other', source_kind='other',
                  source_id='other-1', request_key='other-1', model_id='model-3', model_name='模型三',
                  input_tokens=7, output_tokens=3, cached_tokens=0, total_tokens=10,
                  currency='USD', estimated_cost=1.0, cost_status='estimated',
                  created_at='2026-10-02T04:00:00+00:00', local_date='2026-10-02'),
        ])
        # carol: ledger table exists but empty; dave: old workspace without ledger;
        # erin: corrupt database file (single-db failures must be skipped).
        make_workspace(self.root, WORKSPACE_CAROL, [], [])
        make_workspace(self.root, WORKSPACE_DAVE, [], [], with_ledger=False)
        erin = Path(self.root) / 'workspaces' / WORKSPACE_ERIN
        erin.mkdir(parents=True, exist_ok=True)
        (erin / 'library.sqlite').write_bytes(b'not a sqlite database')
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def login(self, username='admin', scope='admin'):
        self.client.headers['X-Wenjin-Portal'] = scope
        response = self.client.post('/api/session', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def summary(self, **kwargs):
        values = {'from_date': '2026-10-01', 'to_date': '2026-10-05', 'granularity': 'day'}
        values.update(kwargs)
        return portal_usage.summary(self.root, self.accounts, **values)


class UserAndAggregationTests(UsageFixture):
    def test_users_reads_workspace_ids_directly(self):
        values = portal_usage.users(self.accounts)
        self.assertEqual(sorted(value['username'] for value in values),
                         ['admin', 'alice', 'bobby', 'carol', 'dave', 'erin'])
        by_name = {value['username']: value for value in values}
        self.assertEqual(by_name['alice']['workspace_id'], WORKSPACE_ALICE)
        self.assertEqual(by_name['bobby']['workspace_id'], WORKSPACE_BOB)
        self.assertEqual(by_name['carol']['workspace_id'], WORKSPACE_CAROL)
        self.assertNotIn('role', by_name['alice'])

    def test_cross_account_totals_and_groups(self):
        value = self.summary()
        self.assertEqual(value['range'], {'from': '2026-10-01', 'to': '2026-10-05',
                                          'granularity': 'day', 'timezone': 'Asia/Shanghai'})
        totals = value['totals']
        self.assertEqual(totals['requests'], 5)
        self.assertEqual(totals['measured_requests'], 4)
        self.assertEqual(totals['unknown_requests'], 1)
        self.assertEqual(totals['input_tokens'], 317)
        self.assertEqual(totals['output_tokens'], 158)
        self.assertEqual(totals['cached_tokens'], 10)
        self.assertEqual(totals['total_tokens'], 475)
        self.assertAlmostEqual(totals['estimated_cost'], 0.0035, places=6)
        self.assertEqual(totals['cost_unpriced_requests'], 1)
        self.assertEqual(totals['currency'], 'CNY')
        self.assertEqual(totals['accounts'], 2)
        self.assertEqual(totals['projects'], 3)
        self.assertEqual(value['sources']['workspaces_scanned'], 5)
        self.assertEqual(value['sources']['workspaces_with_ledger'], 3)
        self.assertTrue(value['sources']['generated_at'])
        self.assertEqual(len(value['buckets']), 5)
        self.assertEqual([bucket['date'] for bucket in value['buckets']],
                         ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05'])
        self.assertEqual(value['buckets'][0]['requests'], 1)
        self.assertEqual(value['buckets'][0]['total_tokens'], 150)
        self.assertAlmostEqual(value['buckets'][0]['estimated_cost'], 0.001, places=6)
        self.assertEqual(value['buckets'][1]['requests'], 2)
        self.assertEqual(value['buckets'][1]['total_tokens'], 25)
        self.assertAlmostEqual(value['buckets'][1]['estimated_cost'], 0.0005, places=6)
        self.assertEqual(value['buckets'][2]['requests'], 2)
        self.assertEqual(value['buckets'][2]['unknown_requests'], 1)
        self.assertEqual(value['buckets'][2]['total_tokens'], 300)
        self.assertEqual(value['buckets'][3], {'date': '2026-10-04', 'requests': 0, 'unknown_requests': 0,
                                               'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0,
                                               'total_tokens': 0, 'estimated_cost': 0.0})
        self.assertEqual([row['feature'] for row in value['by_feature']],
                         ['task', 'collation_batch', 'collation_debug', 'other'])
        self.assertEqual(value['by_feature'][0]['requests'], 2)
        self.assertEqual(value['by_feature'][0]['total_tokens'], 300)
        self.assertAlmostEqual(value['by_feature'][0]['estimated_cost'], 0.002, places=6)
        self.assertEqual([row['model_id'] for row in value['by_model']],
                         ['model-1', 'model-3', 'model-2'])
        self.assertEqual(value['by_model'][0]['requests'], 3)
        self.assertEqual(value['by_model'][0]['total_tokens'], 465)
        self.assertAlmostEqual(value['by_model'][0]['estimated_cost'], 0.0035, places=6)
        self.assertEqual([row['username'] for row in value['accounts']], ['alice', 'bobby'])
        alice = value['accounts'][0]
        self.assertEqual(alice['requests'], 3)
        self.assertEqual(alice['total_tokens'], 450)
        self.assertAlmostEqual(alice['estimated_cost'], 0.003, places=6)
        self.assertEqual([row['project_id'] for row in alice['projects']], ['p-a2', 'p-a1'])
        self.assertEqual(alice['projects'][0]['project_name'], '甲项目二')
        self.assertEqual(alice['projects'][0]['last_at'], '2026-10-03T04:00:00+00:00')
        self.assertEqual([row['project_id'] for row in value['projects']], ['p-a2', 'p-a1', 'p-b1'])
        self.assertEqual(value['projects'][2]['project_name'], '乙项目')
        self.assertEqual(value['projects'][2]['unknown_requests'], 0)
        self.assertEqual(value['projects'][2]['last_at'], '2026-10-02T04:00:00+00:00')

    def test_account_project_feature_and_model_filters(self):
        alice = self.summary(account_id=self.alice['id'])
        self.assertEqual(alice['totals']['requests'], 3)
        self.assertEqual(alice['totals']['accounts'], 1)
        self.assertEqual([row['username'] for row in alice['accounts']], ['alice'])
        self.assertEqual(alice['sources']['workspaces_scanned'], 1)
        self.assertEqual(alice['sources']['workspaces_with_ledger'], 1)
        project = self.summary(project_id='p-a2')
        self.assertEqual(project['totals']['requests'], 2)
        self.assertEqual(project['totals']['projects'], 1)
        feature = self.summary(feature='task')
        self.assertEqual(feature['totals']['requests'], 2)
        self.assertEqual([row['feature'] for row in feature['by_feature']], ['task'])
        model = self.summary(model_id='model-1')
        self.assertEqual(model['totals']['requests'], 3)
        self.assertEqual([row['model_id'] for row in model['by_model']], ['model-1'])
        empty = self.summary(account_id='missing-account')
        self.assertEqual(empty['totals']['requests'], 0)
        self.assertEqual(empty['sources']['workspaces_scanned'], 0)

    def test_week_buckets_start_on_monday_and_fill(self):
        value = self.summary(from_date='2026-10-01', to_date='2026-10-15', granularity='week')
        self.assertEqual([bucket['date'] for bucket in value['buckets']],
                         ['2026-09-28', '2026-10-05', '2026-10-12'])
        self.assertEqual(value['buckets'][0]['requests'], 5)
        self.assertEqual(value['buckets'][0]['total_tokens'], 475)
        self.assertAlmostEqual(value['buckets'][0]['estimated_cost'], 0.0035, places=6)
        self.assertEqual(value['buckets'][1]['requests'], 0)
        self.assertEqual(value['buckets'][2]['requests'], 0)

    def test_month_buckets_fill_and_sum(self):
        value = self.summary(from_date='2026-09-15', to_date='2026-11-02', granularity='month')
        self.assertEqual([bucket['date'] for bucket in value['buckets']],
                         ['2026-09-01', '2026-10-01', '2026-11-01'])
        self.assertEqual(value['buckets'][0]['requests'], 0)
        self.assertEqual(value['buckets'][1]['requests'], 5)
        self.assertEqual(value['buckets'][1]['total_tokens'], 475)
        self.assertEqual(value['buckets'][2]['requests'], 0)

    def test_empty_range_returns_zero_shape_and_still_counts_sources(self):
        value = self.summary(from_date='2025-01-01', to_date='2025-01-02')
        self.assertEqual(value['totals']['requests'], 0)
        self.assertEqual(value['totals']['estimated_cost'], 0.0)
        self.assertEqual(value['totals']['currency'], 'CNY')
        self.assertEqual(value['buckets'], [
            {'date': '2025-01-01', 'requests': 0, 'unknown_requests': 0, 'input_tokens': 0,
             'output_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0, 'estimated_cost': 0.0},
            {'date': '2025-01-02', 'requests': 0, 'unknown_requests': 0, 'input_tokens': 0,
             'output_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0, 'estimated_cost': 0.0},
        ])
        self.assertEqual(value['accounts'], [])
        self.assertEqual(value['projects'], [])
        self.assertEqual(value['sources']['workspaces_scanned'], 5)
        self.assertEqual(value['sources']['workspaces_with_ledger'], 3)


class CsvAndValidationTests(UsageFixture):
    def test_export_rows_are_aggregated_and_ordered(self):
        rows = portal_usage.export_rows(self.root, self.accounts, from_date='2026-10-01', to_date='2026-10-05')
        self.assertEqual(len(rows), 5)
        headers = portal_usage.CSV_HEADER
        self.assertEqual(headers, ['账号', '用户名', '项目', '日期', '功能', '模型', '请求数', '未计量',
                                   '输入token', '输出token', '缓存token', '总token',
                                   '估算金额(CNY)', '来源'])
        by_key = {(row[0], row[3], row[5]): row for row in rows}
        row = by_key[('alice', '2026-10-01', '模型一')]
        self.assertEqual(row[2], '甲项目一')
        self.assertEqual(row[4], 'collation_batch')
        self.assertEqual(row[6], 1)
        self.assertEqual(row[7], 0)
        self.assertEqual(row[8], 100)
        self.assertEqual(row[9], 50)
        self.assertEqual(row[10], 10)
        self.assertEqual(row[11], 150)
        self.assertAlmostEqual(row[12], 0.001, places=6)
        self.assertEqual(row[13], 'batch')
        unknown = by_key[('alice', '2026-10-03', '模型二')]
        self.assertEqual(unknown[7], 1)
        self.assertEqual(unknown[11], 0)
        usd = by_key[('bobby', '2026-10-02', '模型三')]
        self.assertEqual(usd[7], 0)
        self.assertEqual(usd[12], 0.0)

    def test_export_csv_has_bom_header_and_rows(self):
        body = portal_usage.export_csv(self.root, self.accounts, from_date='2026-10-01', to_date='2026-10-05')
        self.assertTrue(body.startswith(b'\xef\xbb\xbf'))
        text = body.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], portal_usage.CSV_HEADER)
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(int(row[6]) for row in rows[1:]), 5)
        self.assertEqual(sum(int(row[7]) for row in rows[1:]), 1)

    def test_range_validation(self):
        for from_date, to_date, message in [
            ('2026-13-01', '2026-12-01', '日期格式无效'),
            ('2026-10-05', '2026-10-01', '开始日期不能晚于结束日期'),
            ('2024-01-01', '2025-01-03', '不能超过 366 天'),
            ('not-a-date', '2026-10-01', '日期格式无效'),
        ]:
            with self.assertRaises(ValueError) as caught:
                portal_usage.resolve_range(from_date, to_date)
            self.assertIn(message, str(caught.exception))
        with self.assertRaises(ValueError):
            portal_usage.validate_granularity('year')
        with self.assertRaises(ValueError):
            self.summary(granularity='year')

    def test_routes_require_admin_and_validate_parameters(self):
        self.assertEqual(self.client.get('/api/admin/usage/summary').status_code, 401)
        self.login('alice', scope='app')
        self.assertEqual(self.client.get('/api/admin/usage/summary').status_code, 403)
        self.client.delete('/api/session')
        self.login('admin')
        for query in ['from=bad', 'from=2026-10-05&to=2026-10-01',
                      'from=2024-01-01&to=2025-01-03', 'granularity=year']:
            response = self.client.get('/api/admin/usage/summary?' + query)
            self.assertEqual(response.status_code, 400, query)
            export = self.client.get('/api/admin/usage/export.csv?' + query)
            self.assertEqual(export.status_code, 400, query)

    def test_summary_route_matches_pure_function(self):
        self.login('admin')
        response = self.client.get('/api/admin/usage/summary?from=2026-10-01&to=2026-10-05&granularity=day')
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value['totals']['requests'], 5)
        self.assertEqual(value['totals']['accounts'], 2)
        self.assertEqual(len(value['buckets']), 5)
        self.assertEqual(value['range']['timezone'], 'Asia/Shanghai')
        with_filter = self.client.get('/api/admin/usage/summary?from=2026-10-01&to=2026-10-05&account_id=' + self.alice['id'])
        self.assertEqual(with_filter.json()['totals']['requests'], 3)

    def test_export_route_sets_bom_and_filename(self):
        self.login('admin')
        response = self.client.get('/api/admin/usage/export.csv?from=2026-10-01&to=2026-10-05')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('text/csv', response.headers['content-type'])
        self.assertEqual(response.headers['content-disposition'],
                         'attachment; filename=usage-2026-10-01-2026-10-05.csv')
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0], portal_usage.CSV_HEADER)
        self.assertEqual(len(rows), 6)


if __name__ == '__main__':
    unittest.main()
