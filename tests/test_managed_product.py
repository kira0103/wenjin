"""End-to-end owned database upgrade and logical recovery in disposable roots."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend import app as service
from backend.knowledge.product_runtime import ProductDatabase
from backend.knowledge.activation import CatalogActivation
from backend.knowledge.backup import export_bundle,restore_managed_bundle
from backend.knowledge.catalog import CatalogService
from backend.knowledge.execution import WorkingInputService
from backend.knowledge.project_sources import ProjectSourceService
from backend.knowledge.ingestion import publish
from backend.knowledge import repository as r


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要 Windows 内置数据库环境')
class ManagedProductTests(unittest.TestCase):
    def test_activation_backup_restore_and_restart_keep_frozen_input_and_versions(self):
        with tempfile.TemporaryDirectory(prefix='文献库 产品恢复-') as folder:
            root=Path(folder)/'原库 中文';target=Path(folder)/'恢复库 中文'
            owned=ProductDatabase(root,'internal');recovered=None
            with patch.dict(os.environ,{},clear=False):
                os.environ.pop('SHIXUE_KB_DATABASE_URL',None)
                try:
                    owned.start();self.assertFalse(owned.startup_error)
                    service.initialize(root)
                    self.assertTrue(r.database.health(root)['ready'])
                    self.assertTrue(CatalogActivation(root,service.connection).migrate()['active'])
                    doc=CatalogService(root).create({'title':'产品恢复古籍𠀀','label':'甲本','authors':['作者甲']})
                    h,key=r.store_file(root,'原文.txt','财政赋税𠀀'.encode())
                    with r.database.transaction(root) as pg:
                        asset=r.uid()
                        r.execute(pg,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:h,:key,0)',id=asset,h=h,key=key)
                        r.execute(pg,"INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind) VALUES(:e,:a,'原文.txt','section')",e=doc['id'],a=asset)
                        publish(pg,doc['id'],[{'asset_id':asset,'page_index':1,'body':'财政赋税𠀀'}])
                    ProjectSourceService(root,service.connection).put('project-main',doc['id'],{'roles':['base']})
                    frozen=WorkingInputService(root,service.connection,service.utc_now).catalog('project-main',doc['id'])
                    bundle=export_bundle(root);path=root/bundle['storage_key']
                    owned.stop()
                    result=restore_managed_bundle(path,target);self.assertTrue(result['restored'])
                    self.assertFalse((target/'knowledge-db/postmaster.pid').exists())
                    recovered=ProductDatabase(target);recovered.start();self.assertFalse(recovered.startup_error)
                    service.initialize(target)
                    self.assertTrue(CatalogActivation(target,service.connection).status()['active'])
                    actual=WorkingInputService(target,service.connection,service.utc_now).get(frozen['id'])
                    self.assertEqual(actual['body_digest'],frozen['body_digest']);self.assertEqual(actual['positions'],frozen['positions'])
                    self.assertEqual(ProjectSourceService(target,service.connection).list('project-main')[0]['roles'],['base'])
                    self.assertEqual(CatalogService(target).list()['items'][0]['title'],'产品恢复古籍𠀀')
                    recovered.stop();recovered.start();self.assertFalse(recovered.startup_error)
                    self.assertTrue(r.database.health(target)['ready'])
                    with r.database.transaction(target) as pg:
                        r.execute(pg,'UPDATE kb_catalog_activation SET epoch=:epoch WHERE id=1',epoch=r.uid())
                    activation=CatalogActivation(target,service.connection);activation.reconcile()
                    self.assertFalse(activation.status()['active'])
                    service.service.token='managed-pair-test'
                    with TestClient(service.app,headers={'X-App-Token':'managed-pair-test'}) as client:
                        self.assertEqual(client.post('/api/knowledge/catalog',json={'title':'错配库禁止写入'}).status_code,409)
                        self.assertEqual(client.get('/api/materials/'+frozen['material_id']).status_code,200)
                    from backend.knowledge.worker import worker
                    worker.stop()
                finally:
                    if recovered:recovered.stop()
                    owned.stop()

    def test_runtime_failure_keeps_local_snapshots_readable(self):
        with tempfile.TemporaryDirectory(prefix='文献库 故障-') as folder:
            root=Path(folder);service.initialize(root)
            frozen=WorkingInputService(root,service.connection,service.utc_now).text('project-main','断开后保留𠀀')
            with patch('backend.knowledge.product_runtime.ManagedPostgres.verify_runtime',side_effect=RuntimeError('数据库工具缺失')):
                owned=ProductDatabase(root,'internal');owned.start()
                try:
                    self.assertFalse(r.database.health(root)['ready'])
                    self.assertEqual(WorkingInputService(root,service.connection,service.utc_now).get(frozen['id'])['body'],'断开后保留𠀀')
                finally:owned.stop()
