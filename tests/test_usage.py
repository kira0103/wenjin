"""Unit tests for the workspace usage ledger (backend/usage.py).

No network, Postgres or real service is touched; every database is a temporary
SQLite file.
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import usage

SHANGHAI = timezone(timedelta(hours=8))
PRICES = {'price_input_per_million': 2.0, 'price_output_per_million': 4.0,
          'price_cached_per_million': 0.5, 'currency': 'CNY', 'price_date': '2026-11-04',
          'id': 'm_fixture', 'name': '测试模型', 'provider_name': '测试服务商', 'model': 'fixture-model'}


def shanghai_date(moment=None):
    return (moment or datetime.now(timezone.utc)).astimezone(SHANGHAI).date().isoformat()


class NormalizeTests(unittest.TestCase):
    def test_missing_usage_is_unknown(self):
        for value in (None, {}, 'not-a-dict', []):
            with self.subTest(value=value):
                normalized = usage.normalize_usage(value)
                self.assertIsNone(normalized['input'])
                self.assertIsNone(normalized['output'])
                self.assertIsNone(normalized['cached'])
                self.assertIsNone(normalized['total'])
                self.assertIsNone(normalized['reasoning'])
                self.assertEqual(normalized['status'], 'unknown')

    def test_prompt_and_completion_tokens_are_measured_and_total_derived(self):
        normalized = usage.normalize_usage({'prompt_tokens': 10, 'completion_tokens': 7})
        self.assertEqual(normalized['input'], 10)
        self.assertEqual(normalized['output'], 7)
        self.assertEqual(normalized['total'], 17)
        self.assertEqual(normalized['status'], 'measured')

    def test_alternate_input_output_names_and_explicit_total(self):
        normalized = usage.normalize_usage({'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})
        self.assertEqual((normalized['input'], normalized['output'], normalized['total']), (100, 20, 120))
        self.assertEqual(normalized['status'], 'measured')

    def test_cached_tokens_from_all_known_shapes(self):
        shapes = (
            {'prompt_tokens': 100, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 40}},
            {'input_tokens': 100, 'output_tokens': 5, 'input_tokens_details': {'cached_tokens': 40}},
            {'prompt_tokens': 100, 'completion_tokens': 5, 'cache_read_input_tokens': 40},
        )
        for value in shapes:
            with self.subTest(value=value):
                self.assertEqual(usage.normalize_usage(value)['cached'], 40)

    def test_reasoning_tokens_from_details(self):
        normalized = usage.normalize_usage({'prompt_tokens': 20, 'completion_tokens': 30,
                                            'completion_tokens_details': {'reasoning_tokens': 12}})
        self.assertEqual(normalized['reasoning'], 12)
        normalized = usage.normalize_usage({'prompt_tokens': 20, 'completion_tokens': 30,
                                            'output_tokens_details': {'reasoning_tokens': 9}})
        self.assertEqual(normalized['reasoning'], 9)

    def test_only_total_tokens_is_partial(self):
        normalized = usage.normalize_usage({'total_tokens': 50})
        self.assertIsNone(normalized['input'])
        self.assertEqual(normalized['total'], 50)
        self.assertEqual(normalized['status'], 'partial')

    def test_input_only_is_partial(self):
        normalized = usage.normalize_usage({'input_tokens': 3})
        self.assertEqual(normalized['input'], 3)
        self.assertIsNone(normalized['total'])
        self.assertEqual(normalized['status'], 'partial')

    def test_invalid_counts_are_ignored(self):
        normalized = usage.normalize_usage({'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': '9'})
        self.assertEqual(normalized['status'], 'unknown')
        self.assertIsNone(normalized['input'])
        self.assertIsNone(normalized['output'])
        self.assertIsNone(normalized['total'])

    def test_zero_tokens_are_measured_not_missing(self):
        normalized = usage.normalize_usage({'prompt_tokens': 0, 'completion_tokens': 0})
        self.assertEqual(normalized['status'], 'measured')
        self.assertEqual(normalized['total'], 0)


class CostTests(unittest.TestCase):
    def tokens(self, **value):
        return usage.normalize_usage(value)

    def test_estimated_cost_uses_cached_price(self):
        tokens = self.tokens(prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000,
                             prompt_tokens_details={'cached_tokens': 200_000})
        cost, status = usage.estimate_cost(tokens, PRICES)
        self.assertEqual(status, 'estimated')
        self.assertAlmostEqual(cost, ((1_000_000 - 200_000) * 2.0 + 200_000 * 0.5 + 500_000 * 4.0) / 1_000_000)

    def test_missing_prices_is_unpriced(self):
        tokens = self.tokens(prompt_tokens=10, completion_tokens=5)
        self.assertEqual(usage.estimate_cost(tokens, {}), (None, 'unpriced'))
        self.assertEqual(usage.estimate_cost(tokens, {'price_input_per_million': 1}), (None, 'unpriced'))
        self.assertEqual(usage.estimate_cost(tokens, None), (None, 'unpriced'))

    def test_unknown_tokens_is_unknown(self):
        self.assertEqual(usage.estimate_cost(self.tokens(), PRICES), (None, 'unknown'))
        self.assertEqual(usage.estimate_cost(self.tokens(total_tokens=9), PRICES), (None, 'unknown'))

    def test_cached_usage_without_cached_price_is_unknown(self):
        tokens = self.tokens(prompt_tokens=100, completion_tokens=5,
                             prompt_tokens_details={'cached_tokens': 40})
        prices = {'price_input_per_million': 1.0, 'price_output_per_million': 2.0}
        self.assertEqual(usage.estimate_cost(tokens, prices), (None, 'unknown'))

    def test_zero_cached_tokens_does_not_require_cached_price(self):
        tokens = self.tokens(prompt_tokens=100, completion_tokens=5,
                             prompt_tokens_details={'cached_tokens': 0})
        prices = {'price_input_per_million': 1.0, 'price_output_per_million': 2.0}
        cost, status = usage.estimate_cost(tokens, prices)
        self.assertEqual(status, 'estimated')
        self.assertAlmostEqual(cost, (100 * 1.0 + 5 * 2.0) / 1_000_000)

    def test_accepts_raw_provider_usage(self):
        prices = {'price_input_per_million': 1.0, 'price_output_per_million': 2.0}
        cost, status = usage.estimate_cost({'prompt_tokens': 100, 'completion_tokens': 5}, prices)
        self.assertEqual(status, 'estimated')
        self.assertAlmostEqual(cost, (100 * 1.0 + 5 * 2.0) / 1_000_000)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = sqlite3.connect(Path(self.directory.name) / 'library.sqlite')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        usage.ensure_schema(self.db)

    def row(self, event_id):
        return self.db.execute('SELECT * FROM usage_events WHERE id=?', (event_id,)).fetchone()

    def test_schema_creation_is_idempotent(self):
        usage.ensure_schema(self.db)
        columns = [row['name'] for row in self.db.execute('PRAGMA table_info(usage_events)')]
        for name in ('id', 'project_id', 'feature', 'source_kind', 'source_id', 'request_key',
                     'model_id', 'model_name', 'provider_name', 'model', 'input_tokens', 'output_tokens',
                     'cached_tokens', 'total_tokens', 'usage_status', 'latency_ms', 'price_input_per_million',
                     'price_output_per_million', 'price_cached_per_million', 'currency', 'price_date',
                     'estimated_cost', 'cost_status', 'created_at', 'local_date', 'meta_json'):
            self.assertIn(name, columns)
        indexes = {row['name'] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({'idx_usage_project_date', 'idx_usage_date', 'idx_usage_feature', 'idx_usage_dedupe'} <= indexes)

    def test_record_persists_counts_prices_and_dates(self):
        before = shanghai_date()
        event_id = usage.record(
            self.db, project_id='project-1', feature='task', source_kind='task', source_id='task-1',
            request_key='call-1', settings=PRICES,
            usage={'prompt_tokens': 1_000_000, 'completion_tokens': 500_000, 'total_tokens': 1_500_000,
                   'prompt_tokens_details': {'cached_tokens': 200_000}},
            latency_ms=1234, meta={'task_type': 'translate'})
        after = shanghai_date()
        expected_id = hashlib.sha256(b'task|task|task-1|call-1').hexdigest()[:24]
        self.assertEqual(event_id, expected_id)
        row = self.row(event_id)
        self.assertEqual(row['project_id'], 'project-1')
        self.assertEqual(row['feature'], 'task')
        self.assertEqual(row['source_kind'], 'task')
        self.assertEqual(row['source_id'], 'task-1')
        self.assertEqual(row['request_key'], 'call-1')
        self.assertEqual(row['model_id'], 'm_fixture')
        self.assertEqual(row['model_name'], '测试模型')
        self.assertEqual(row['provider_name'], '测试服务商')
        self.assertEqual(row['model'], 'fixture-model')
        self.assertEqual((row['input_tokens'], row['output_tokens'], row['cached_tokens'], row['total_tokens']),
                         (1_000_000, 500_000, 200_000, 1_500_000))
        self.assertEqual(row['usage_status'], 'measured')
        self.assertEqual(row['latency_ms'], 1234)
        self.assertEqual(row['price_input_per_million'], 2.0)
        self.assertEqual(row['price_output_per_million'], 4.0)
        self.assertEqual(row['price_cached_per_million'], 0.5)
        self.assertEqual(row['currency'], 'CNY')
        self.assertEqual(row['price_date'], '2026-11-04')
        self.assertEqual(row['cost_status'], 'estimated')
        self.assertAlmostEqual(row['estimated_cost'], 3.7)
        self.assertIn(row['local_date'], {before, after})
        self.assertEqual(row['local_date'], shanghai_date(datetime.fromisoformat(row['created_at'])))
        self.assertEqual(datetime.fromisoformat(row['created_at']).utcoffset(), timedelta(0))
        self.assertEqual(json.loads(row['meta_json']), {'task_type': 'translate'})

    def test_record_is_idempotent_for_same_source_and_request(self):
        arguments = dict(project_id='project-1', feature='collation_batch', source_kind='batch',
                         source_id='batch-1', request_key='attempt-1', settings=PRICES,
                         usage={'prompt_tokens': 11, 'completion_tokens': 7}, latency_ms=5)
        first = usage.record(self.db, **arguments)
        second = usage.record(self.db, **arguments)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 1)

    def test_record_keeps_distinct_requests(self):
        base = dict(project_id='project-1', feature='task', source_kind='task', source_id='task-1',
                    settings=PRICES, usage={'prompt_tokens': 1, 'completion_tokens': 1})
        usage.record(self.db, request_key='call-1', **base)
        usage.record(self.db, request_key='call-2', **base)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 2)

    def test_unknown_usage_is_stored_as_unknown(self):
        event_id = usage.record(self.db, project_id='project-1', feature='collation_batch', source_kind='batch',
                                source_id='batch-1', request_key='attempt-1', settings=PRICES, usage=None)
        row = self.row(event_id)
        self.assertEqual(row['usage_status'], 'unknown')
        self.assertIsNone(row['input_tokens'])
        self.assertIsNone(row['total_tokens'])
        self.assertEqual(row['cost_status'], 'unknown')

    def test_unpaid_estimate_is_unpriced(self):
        event_id = usage.record(self.db, project_id='project-1', feature='task', source_kind='task',
                                source_id='task-2', request_key='call-1', settings={'currency': 'CNY'},
                                usage={'prompt_tokens': 10, 'completion_tokens': 5})
        row = self.row(event_id)
        self.assertEqual(row['cost_status'], 'unpriced')
        self.assertIsNone(row['estimated_cost'])
        self.assertEqual(row['currency'], 'CNY')

    def test_record_never_writes_response_text(self):
        event_id = usage.record(
            self.db, project_id='project-1', feature='task', source_kind='task', source_id='task-3',
            request_key='call-1', settings=PRICES,
            usage={'prompt_tokens': 10, 'completion_tokens': 5, 'text': 'SECRET_BODY',
                   'raw_response': 'SECRET_BODY', 'content': 'SECRET_BODY'})
        row = dict(self.row(event_id))
        for name in ('text', 'prompt', 'content', 'body', 'response'):
            self.assertNotIn(name, row)
        self.assertNotIn('SECRET_BODY', json.dumps(row, ensure_ascii=False))

    def test_local_date_uses_asia_shanghai(self):
        self.assertEqual(usage.local_date('2026-11-04T16:30:00+00:00'), '2026-11-05')
        self.assertEqual(usage.local_date('2026-11-05T15:59:59+00:00'), '2026-11-05')
        self.assertEqual(usage.local_date('2026-11-05 16:00:00'), '2026-11-06')
        self.assertEqual(usage.local_date(datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)), '2026-11-01')


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = sqlite3.connect(Path(self.directory.name) / 'library.sqlite')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE collation_batches(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            model_json TEXT NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE collation_attempts(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, usage_json TEXT);
        ''')
        model = json.dumps({**PRICES})
        self.db.execute('INSERT INTO collation_batches VALUES(?,?,?,?)',
                        ('batch-1', 'project-1', model, '2026-10-01T02:00:00+00:00'))
        self.db.execute('INSERT INTO collation_batches VALUES(?,?,?,?)',
                        ('batch-2', 'project-2', model, '2026-10-02T17:30:00+00:00'))
        self.db.execute('INSERT INTO collation_attempts VALUES(?,?,?,?,?,?)',
                        ('attempt-1', 'batch-1', 1_000_000, 500_000, 1_500_000,
                         json.dumps({'prompt_tokens': 1_000_000, 'completion_tokens': 500_000,
                                     'total_tokens': 1_500_000,
                                     'prompt_tokens_details': {'cached_tokens': 200_000}})))
        self.db.execute('INSERT INTO collation_attempts VALUES(?,?,?,?,?,?)',
                        ('attempt-2', 'batch-1', 10, 5, 15, None))
        self.db.execute('INSERT INTO collation_attempts VALUES(?,?,?,?,?,?)',
                        ('attempt-3', 'batch-2', None, None, None, None))

    def test_backfill_inserts_historical_attempts_once(self):
        usage.ensure_schema(self.db)
        self.assertEqual(usage.backfill_collation(self.db), 3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 3)
        self.assertEqual(usage.backfill_collation(self.db), 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 3)

    def test_backfill_maps_receipts_and_prices(self):
        usage.ensure_schema(self.db)
        usage.backfill_collation(self.db)
        first = self.db.execute("SELECT * FROM usage_events WHERE request_key='attempt-1'").fetchone()
        self.assertEqual(first['project_id'], 'project-1')
        self.assertEqual(first['feature'], 'collation_batch')
        self.assertEqual(first['source_kind'], 'batch_backfill')
        self.assertEqual(first['source_id'], 'batch-1')
        self.assertEqual(first['input_tokens'], 1_000_000)
        self.assertEqual(first['cached_tokens'], 200_000)
        self.assertEqual(first['total_tokens'], 1_500_000)
        self.assertEqual(first['usage_status'], 'measured')
        self.assertEqual(first['model_id'], 'm_fixture')
        self.assertEqual(first['model_name'], '测试模型')
        self.assertEqual(first['cost_status'], 'estimated')
        self.assertAlmostEqual(first['estimated_cost'], 3.7)
        self.assertEqual(first['local_date'], '2026-10-01')

        fallback = self.db.execute("SELECT * FROM usage_events WHERE request_key='attempt-2'").fetchone()
        self.assertEqual((fallback['input_tokens'], fallback['output_tokens'], fallback['total_tokens']),
                         (10, 5, 15))
        self.assertEqual(fallback['usage_status'], 'measured')

        unknown = self.db.execute("SELECT * FROM usage_events WHERE request_key='attempt-3'").fetchone()
        self.assertEqual(unknown['usage_status'], 'unknown')
        self.assertEqual(unknown['cost_status'], 'unknown')
        self.assertEqual(unknown['local_date'], '2026-10-03')

    def test_backfill_without_collation_tables_returns_zero(self):
        other = sqlite3.connect(':memory:')
        self.addCleanup(other.close)
        self.assertEqual(usage.backfill_collation(other), 0)
        self.assertIsNone(other.execute("SELECT 1 FROM sqlite_master WHERE name='usage_events'").fetchone())


class InitializeTests(unittest.TestCase):
    """The workspace initializer creates the ledger and backfills only once."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        from backend import app as service
        self.service = service

    def test_initialize_creates_ledger_and_backfills_once(self):
        service = self.service
        service.initialize(self.root)
        with service.connection() as db:
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'").fetchone())
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM app_settings WHERE setting_key='usage_backfill_v1'").fetchone())
            db.execute('''INSERT INTO collation_batches(id,project_id,run_id,title,model_json,rules,prompt_version,status,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?)''',
                       ('batch-historic', 'project-main', 'run-1', '历史批次', json.dumps(PRICES), '', 'collation-v1',
                        'completed', '2026-10-01T02:00:00+00:00'))
            db.execute('''INSERT INTO collation_attempts(id,batch_id,item_id,chunk_id,budget_id,status,started_at,
                          input_tokens,output_tokens,total_tokens,usage_json)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                       ('attempt-historic', 'batch-historic', 'item-1', 'chunk-1', 'root-1', 'received',
                        '2026-10-01T02:00:00+00:00', 20, 10, 30,
                        json.dumps({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})))
        # A later start sees the marker and does not replay the backfill.
        service.initialize(self.root)
        with service.connection() as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM usage_events WHERE request_key='attempt-historic'").fetchone())
            db.execute("DELETE FROM app_settings WHERE setting_key='usage_backfill_v1'")
        service.initialize(self.root)
        with service.connection() as db:
            row = db.execute("SELECT * FROM usage_events WHERE request_key='attempt-historic'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['source_kind'], 'batch_backfill')
            self.assertEqual(row['source_id'], 'batch-historic')
            self.assertEqual(row['project_id'], 'project-main')
            self.assertEqual(row['local_date'], '2026-10-01')
            self.assertEqual(row['input_tokens'], 20)
