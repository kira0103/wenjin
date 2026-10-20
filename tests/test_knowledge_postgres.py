"""Opt-in integration checks against a disposable database, never user documents."""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql
from sqlalchemy import URL, text
from sqlalchemy.exc import DBAPIError

from backend.knowledge.database import KnowledgeDatabase, build_engine
from backend.knowledge.migration import upgrade


@unittest.skipUnless(os.environ.get('SHIXUE_KB_INTEGRATION') == '1', '需要显式启用独立 PostgreSQL 集成测试')
class PostgresFoundationIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_database = 'shixue_kb_test_' + uuid.uuid4().hex
        directory = Path('.local/knowledge')
        cls.passwords = {name: (directory / (name + '_password')).read_text().strip()
                         for name in ('admin', 'owner', 'app')}
        cls.admin = psycopg.connect(host='127.0.0.1', port=55432, dbname='shixue_knowledge',
                                   user='shixue_admin', password=cls.passwords['admin'], autocommit=True,connect_timeout=5)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.test_database)))
        try:
            with psycopg.connect(host='127.0.0.1', port=55432, dbname=cls.test_database,
                                 user='shixue_admin', password=cls.passwords['admin'],connect_timeout=5) as connection:
                for statement in [
                    'CREATE EXTENSION vector', 'CREATE EXTENSION pg_trgm',
                    'REVOKE CREATE ON SCHEMA public FROM PUBLIC',
                    'GRANT USAGE,CREATE ON SCHEMA public TO shixue_owner',
                    'GRANT USAGE ON SCHEMA public TO shixue_app',
                    'ALTER DEFAULT PRIVILEGES FOR ROLE shixue_owner IN SCHEMA public '
                    'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO shixue_app',
                ]:
                    connection.execute(statement)
            cls.owner_url = URL.create('postgresql+psycopg', username='shixue_owner', password=cls.passwords['owner'],
                                       host='127.0.0.1', port=55432, database=cls.test_database)
            cls.app_url = cls.owner_url.set(username='shixue_app', password=cls.passwords['app'])
            upgrade(cls.owner_url)
            upgrade(cls.owner_url)  # A repeated upgrade must preserve the same schema.
            cls.engine = build_engine(cls.app_url)
        except Exception:
            cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.test_database)))
            cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert cls.test_database.startswith('shixue_kb_test_')
        cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.test_database)))
        cls.admin.close()

    def test_ready_health_requires_restricted_app_role(self):
        database = KnowledgeDatabase()
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch.dict(os.environ, {'SHIXUE_KB_DATABASE_URL': self.app_url.render_as_string(hide_password=False)}):
                    health = database.health(Path(directory))
                self.assertTrue(health['ready'], health)
                self.assertTrue(health['restricted_role'])
                with patch.dict(os.environ, {'SHIXUE_KB_DATABASE_URL': self.owner_url.render_as_string(hide_password=False)}):
                    health = database.health(Path(directory))
                self.assertFalse(health['ready'])
                self.assertFalse(health['restricted_role'])
            finally:
                database.dispose()

    def test_two_versions_share_file_but_have_independent_page_labels(self):
        library, work, a, b, asset = [uuid.uuid4() for _ in range(5)]
        with self.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text('INSERT INTO kb_libraries(id,name) VALUES(:id,:name)'), {'id': library, 'name': '测试库'})
                connection.execute(text('INSERT INTO kb_works(id,library_id,title) VALUES(:id,:lib,:title)'),
                                   {'id': work, 'lib': library, 'title': '𠀀古籍'})
                for edition in (a, b):
                    connection.execute(text('INSERT INTO kb_editions(id,work_id,label) VALUES(:id,:work,:label)'),
                                       {'id': edition, 'work': work, 'label': '测试版本'})
                connection.execute(text('INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:hash,:key,0)'),
                                   {'id': asset, 'hash': uuid.uuid4().hex*2, 'key': 'assets/test.pdf'})
                connection.execute(text('INSERT INTO kb_pages(asset_id,page_index) VALUES(:id,1)'), {'id': asset})
                for edition, label in ((a, '卷一 叶一正'), (b, '第 7 页')):
                    connection.execute(text('INSERT INTO kb_edition_assets(edition_id,asset_id) VALUES(:e,:a)'), {'e': edition, 'a': asset})
                    connection.execute(text('INSERT INTO kb_page_labels(edition_id,asset_id,page_index,printed_label) VALUES(:e,:a,1,:label)'),
                                       {'e': edition, 'a': asset, 'label': label})
                labels = set(connection.execute(text('SELECT printed_label FROM kb_page_labels WHERE asset_id=:id'), {'id': asset}).scalars())
                self.assertEqual(labels, {'卷一 叶一正', '第 7 页'})
                with self.assertRaises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text('INSERT INTO kb_pages(asset_id,page_index) VALUES(:id,0)'), {'id': asset})
                with self.assertRaises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text('INSERT INTO kb_page_labels(edition_id,asset_id,page_index) VALUES(:e,:a,2)'),
                                           {'e': a, 'a': asset})
                for invalid_key in ('../outside.pdf', 'C:\\outside.pdf', '/outside.pdf'):
                    with self.assertRaises(DBAPIError):
                        with connection.begin_nested():
                            connection.execute(text('INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:hash,:key,0)'),
                                               {'id': uuid.uuid4(), 'hash': uuid.uuid4().hex*2, 'key': invalid_key})
            finally:
                transaction.rollback()

    def test_app_cannot_create_tables_or_modify_schema_version(self):
        for statement in ('CREATE TABLE public.forbidden_test(id integer)', "UPDATE kb_schema_version SET version_num='bad'"):
            with self.assertRaises(DBAPIError):
                with self.engine.begin() as connection:
                    connection.execute(text(statement))

    def test_pgvector_cosine_operator_runs_with_app_connection(self):
        with self.engine.connect() as connection:
            self.assertAlmostEqual(connection.execute(text("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector")).scalar_one(), 0)
            self.assertAlmostEqual(connection.execute(text("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector")).scalar_one(), 1)


if __name__ == '__main__':
    unittest.main()
