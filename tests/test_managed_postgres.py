"""Native Windows runtime checks, always in disposable independent directories."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from backend.knowledge.managed import ManagedPostgres, _run, runtime_directory


@unittest.skipUnless(os.name == 'nt' and os.environ.get('SHIXUE_MANAGED_TESTS') == '1',
                     '需要显式启用 Windows 内置数据库原型测试')
class ManagedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='史学 内置库-')
        self.root = Path(self.temporary.name) / '资料 中文 空格'
        self.manager = ManagedPostgres(self.root)

    def tearDown(self):
        self.manager.stop()
        self.temporary.cleanup()

    def test_chinese_path_auth_extensions_and_exclusive_lease(self):
        self.manager.start()
        with self.manager.connect() as connection:
            self.assertEqual(connection.execute('SHOW server_encoding').fetchone()[0], 'UTF8')
            extensions = dict(connection.execute("SELECT extname,extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm')"))
            self.assertEqual(extensions, {'vector': '0.8.7', 'pg_trgm': '1.6'})
            password = connection.execute('SELECT rolpassword FROM pg_authid WHERE rolname=current_user').fetchone()[0]
            self.assertTrue(password.startswith('SCRAM-SHA-256$'))
            hba = connection.execute('SELECT auth_method FROM pg_hba_file_rules WHERE error IS NULL').fetchall()
            self.assertTrue(hba and all(row[0] == 'scram-sha-256' for row in hba))
        with self.assertRaises(psycopg.OperationalError):
            psycopg.connect(host='127.0.0.1', port=self.manager.port, dbname='shixue_knowledge',
                            user='shixue_local', password='incorrect', connect_timeout=2)
        with self.assertRaisesRegex(RuntimeError, '另一工作台'):
            ManagedPostgres(self.root).start()

    def test_native_hnsw_filtered_scan_and_trigram(self):
        self.manager.start()
        with self.manager.connect() as connection:
            connection.execute('CREATE TABLE samples(id integer PRIMARY KEY,page integer,body text,embedding vector(3))')
            with connection.cursor() as cursor:
                cursor.executemany('INSERT INTO samples VALUES(%s,%s,%s,%s)',
                                   [(i, i, '明代财政赋税', '[1,0,0]' if i != 17 else '[0.99,0.01,0]') for i in range(1, 31)])
            connection.execute('CREATE INDEX ON samples USING hnsw(embedding vector_cosine_ops)')
            connection.execute('CREATE INDEX ON samples USING gin(body gin_trgm_ops)')
            connection.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            connection.execute('SET LOCAL enable_seqscan=off')
            result = connection.execute("SELECT id FROM samples WHERE page=17 ORDER BY embedding <=> '[1,0,0]'::vector LIMIT 1").fetchone()
            self.assertEqual(result[0], 17)
            self.assertEqual(connection.execute("SELECT count(*) FROM samples WHERE body LIKE '%财政%'").fetchone()[0], 30)

    def test_bundled_dump_restore(self):
        self.manager.start()
        with self.manager.connect() as connection:
            connection.execute('CREATE TABLE sample(body text)')
            connection.execute('INSERT INTO sample VALUES(%s)', ('古籍𠀀论文',))
        output = self.root / 'backup.dump'
        url = self.manager.url
        environment = {**self.manager._environment(), 'PGPASSWORD': url.password}
        common = ['-h', '127.0.0.1', '-p', str(url.port), '-U', url.username]
        result = _run([str(self.manager.tool('pg_dump')), *common, '-d', url.database,
                       '-Fc', '-f', str(output)], env=environment)
        self.assertEqual(result.returncode, 0)
        with self.manager.connect('postgres', autocommit=True) as connection:
            connection.execute('CREATE DATABASE restore_test')
        result = _run([str(self.manager.tool('pg_restore')), *common, '-d', 'restore_test',
                       '--single-transaction', '--exit-on-error', str(output)], env=environment)
        self.assertEqual(result.returncode, 0)
        with self.manager.connect('restore_test') as connection:
            self.assertEqual(connection.execute('SELECT body FROM sample').fetchone()[0], '古籍𠀀论文')

    def test_move_after_clean_shutdown(self):
        self.manager.start()
        with self.manager.connect() as connection:
            connection.execute('CREATE TABLE moving(body text)')
            connection.execute("INSERT INTO moving VALUES('搬迁原文')")
        self.manager.stop()
        moved = Path(self.temporary.name) / '另一个位置 空格'
        shutil.copytree(self.root, moved)
        with ManagedPostgres(moved) as manager:
            with manager.connect() as connection:
                self.assertEqual(connection.execute('SELECT body FROM moving').fetchone()[0], '搬迁原文')

    def test_interrupted_initialization_retries_only_owned_stage(self):
        self.manager.private.mkdir(parents=True)
        stage = self.root / '.knowledge-db-initializing-test'
        stage.mkdir()
        (stage / 'incomplete').write_text('partial')
        (self.manager.private / 'initialization.json').write_text(json.dumps({'stage': stage.name}))
        self.manager.start()
        self.assertFalse(stage.exists())
        self.assertTrue((self.manager.data / 'PG_VERSION').is_file())

    def test_abrupt_database_shutdown_recovers_committed_data(self):
        self.manager.start()
        with self.manager.connect() as connection:
            connection.execute('CREATE TABLE crash_test(body text)')
            connection.execute("INSERT INTO crash_test VALUES('已提交原文')")
        result = _run([str(self.manager.tool('pg_ctl')), '-D', str(self.manager.data),
                       '-m', 'immediate', '-w', 'stop'], env=self.manager._environment())
        self.assertEqual(result.returncode, 0)
        self.manager.running = False
        self.manager.lease.release()
        with ManagedPostgres(self.root) as manager:
            self.assertTrue(manager.recovered)
            with manager.connect() as connection:
                self.assertEqual(connection.execute('SELECT body FROM crash_test').fetchone()[0], '已提交原文')

    def test_port_conflict_retries_another_port(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            blocked = occupied.getsockname()[1]
            free = self.manager.free_port()
            with patch.object(self.manager, 'free_port', side_effect=[blocked, free]):
                self.manager.start()
            self.assertEqual(self.manager.port, free)

    def test_missing_credentials_do_not_reinitialize_existing_cluster(self):
        self.manager.start()
        self.manager.stop()
        credential = self.manager.private / 'credentials.json'
        saved = credential.read_bytes()
        credential.unlink()
        try:
            with self.assertRaisesRegex(RuntimeError, '凭据缺失'):
                self.manager.start()
            self.assertTrue((self.manager.data / 'PG_VERSION').exists())
        finally:
            credential.write_bytes(saved)

    def test_tampered_runtime_rejected_before_initialization(self):
        bad_runtime = Path(self.temporary.name) / 'bad-runtime'
        bad_runtime.mkdir()
        (bad_runtime / 'runtime-manifest.json').write_text(json.dumps({'files': {'missing': '0' * 64}}))
        with self.assertRaisesRegex(RuntimeError, '缺失'):
            ManagedPostgres(self.root, bad_runtime).start()
        self.assertFalse(self.manager.data.exists())
