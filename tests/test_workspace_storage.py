import os
import json
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from backend import workspace_storage as storage


class StorageRules(unittest.TestCase):
    def test_missing_or_partial_directory_is_never_an_empty_workspace(self):
        folder=Path('.local/temporary');folder.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as value:
            root=Path(value)
            with self.assertRaises(ValueError):storage.inspect(root)
            (root/'.workspace-copy-incomplete').write_text('{}')
            with self.assertRaisesRegex(ValueError,'复制尚未完成'):storage.inspect(root)


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class StorageNative(unittest.TestCase):
    def test_offline_move_rebinds_scope_verifies_assets_and_preserves_source(self):
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge import repository as r,assistant_registry
        from backend.knowledge.literature_categories import LiteratureCategories
        from backend.knowledge.catalog import CatalogService
        from backend import app
        folder=Path('.local/temporary');folder.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder.resolve()) as value:
            source=Path(value)/'old';target=Path(value)/'fixed'
            product=ProductDatabase(source,'internal')
            try:
                product.start();self.assertFalse(product.startup_error);app.initialize(source)
                # Include a workspace-scoped registry record and a private attachment.
                assistant_registry.list_definitions(source,'skill')
                categories=LiteratureCategories(source)
                category=categories.save({'name':'迁移后仍保留的文献分类','revision':categories.tree()['revision']})
                book=CatalogService(source).create({'title':'迁移分类文献','item_type':'ancient','category_ids':[category['id']]})
                asset=source/'assets/immutable.txt';asset.write_text('原件内容𠀀',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'关闭'):storage.migrate(source,target)
            finally:product.stop()
            before=storage.digest(source/'library.sqlite')
            report=storage.migrate(source,target)
            self.assertTrue(report['sqlite_rows_verified']);self.assertTrue(report['source_preserved'])
            self.assertEqual(storage.digest(source/'library.sqlite'),before)
            self.assertEqual((target/'assets/immutable.txt').read_bytes(),asset.read_bytes())
            self.assertFalse((target/'.workspace-copy-incomplete').exists())
            restored=ProductDatabase(target,'internal')
            try:
                restored.start();self.assertFalse(restored.startup_error)
                self.assertEqual(len(assistant_registry.list_definitions(target,'skill')),6)
                moved=LiteratureCategories(target).tree()
                self.assertEqual(moved['items'][0]['name'],'迁移后仍保留的文献分类')
                self.assertEqual(moved['items'][0]['ancient'],1)
                self.assertEqual(str(CatalogService(target).list(category_id=category['id'])['items'][0]['work_id']),str(book['work_id']))
                with r.database.transaction(target) as db:
                    keys=r.execute(db,'SELECT DISTINCT workspace_key FROM kb_assistant_definitions').scalars().all()
                self.assertEqual(keys,[storage.workspace_key(target)])
                with r.database.transaction(target) as db:
                    self.assertEqual(r.execute(db,'SELECT DISTINCT workspace_key FROM kb_literature_categories').scalars().all(),[storage.workspace_key(target)])
                    self.assertEqual(r.execute(db,'SELECT DISTINCT workspace_key FROM kb_literature_category_state').scalars().all(),[storage.workspace_key(target)])
            finally:restored.stop()
            # An older program must reject a future schema before backing up or changing SQLite.
            from backend.knowledge.managed import ManagedPostgres
            manager=ManagedPostgres(target)
            try:
                manager.start()
                with manager.connect() as c:c.execute("UPDATE kb_schema_version SET version_num='kb9999'")
            finally:manager.stop()
            frozen=storage.digest(target/'library.sqlite')
            older=ProductDatabase(target,'internal')
            try:
                older.start();self.assertTrue(older.incompatible);self.assertIn('不兼容',older.startup_error)
                self.assertEqual(storage.digest(target/'library.sqlite'),frozen)
            finally:older.stop()
            with self.assertRaisesRegex(ValueError,'空目录'):storage.migrate(source,target)
