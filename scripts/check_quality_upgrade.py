"""Upgrade rc.2 fixture data with the rc.3 binary, never a user's library."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import uuid
import zipfile

import httpx

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'.local/rerank-context-20261013'


class FixtureService:
    def __init__(self,binary,folder):
        self.folder=folder;self.binary=binary;self.process=None;self.client=None

    def start(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        token=secrets.token_hex(32)
        environment=os.environ.copy()
        environment.pop('SHIXUE_KB_DATABASE_URL',None);environment.pop('SHIXUE_KB_RESTORE_URL',None)
        environment.update(HF_HUB_OFFLINE='1',SHIXUE_KB_MODEL_CACHE=str(Path(os.environ['APPDATA'])/'shixue-workbench/models'))
        self.process=subprocess.Popen([str(self.binary),'--port',str(port),'--token',token,'--data-dir',str(self.folder),'--database-mode','internal'],
            env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-App-Token':token},timeout=15)
        until(lambda:self.client.get('/health').is_success,'service startup',90)

    def api(self,path,method='GET',**kwargs):
        response=self.client.request(method,path,**kwargs);response.raise_for_status();return response.json()

    def stop(self):
        if self.process and self.process.poll() is None:
            try:self.api('/api/service/exit','POST');self.process.wait(20)
            except Exception:self.process.terminate();self.process.wait(10)
        if self.client:self.client.close()


def until(check,label,seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            if check():return
        except httpx.TransportError:pass
        time.sleep(.2)
    raise RuntimeError('Fixture timeout: '+label)


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    folder=OUTPUT/('upgrade-'+uuid.uuid4().hex)
    old=FixtureService(ROOT/'release/v0.5.0-rc.2/win-unpacked/resources/backend/ShixueWorkbenchService.exe',folder)
    new=FixtureService(ROOT/'release/v0.5.0-rc.3/win-unpacked/resources/backend/ShixueWorkbenchService.exe',folder)
    try:
        old.start();assert old.api('/api/knowledge/health')['schema_revision']=='kb0006'
        edition=old.api('/api/knowledge/catalog','POST',json={'title':'升级保留𠀀原文','item_type':'ancient','label':'旧候选测试本'})
        old.api('/api/knowledge/editions/'+edition['id']+'/imports?filename=upgrade.txt','POST',content='财政赋税𠀀原文与冻结输入。'.encode(),headers={'Content-Type':'application/octet-stream'})
        until(lambda:not any(job['state'] in ('queued','running') for job in old.api('/api/knowledge/jobs')),'old indexing')
        document=old.api('/api/knowledge/editions/'+edition['id']);assert document['blocks']
        anchor=old.api('/api/knowledge/anchors','POST',json={'block_id':document['blocks'][0]['id'],'start':4,'end':7})
        old.api('/api/knowledge/projects/project-main/sources/'+edition['id'],'PUT',json={'roles':['base']})
        frozen=old.api('/api/knowledge/working-inputs','POST',json={'project_id':'project-main','edition_id':edition['id']})
        old.stop()
        before={item.name for item in (folder/'backups').glob('*.zip')}
        new.start();health=new.api('/api/knowledge/health');assert health['ready'] and health['schema_revision']=='kb0007'
        updated=new.api('/api/knowledge/editions/'+edition['id'])
        assert updated['active_revision']==document['active_revision']
        assert updated['blocks']==document['blocks']
        assert new.api('/api/knowledge/anchors/'+anchor['id'])['quote']==anchor['quote']
        actual=new.api('/api/knowledge/working-inputs/'+frozen['id']+'?project_id=project-main')
        assert actual['body_digest']==frozen['body_digest'] and actual['positions']==frozen['positions']
        result=new.api('/api/knowledge/search','POST',json={'query':'财政赋税','mode':'exact','scope':{'edition_ids':[edition['id']]}})
        assert result['results'] and result['retrieval_run_id']
        semantic=new.api('/api/knowledge/search','POST',json={'query':'国家财政收入来自赋税','mode':'semantic','scope':{'edition_ids':[edition['id']]}})
        assert semantic['results'],'Previous vector index became inaccessible after upgrade'
        assert not any('模型不同' in warning for warning in semantic['warnings'])
        backups=[item for item in (folder/'backups').glob('*.zip') if item.name not in before]
        assert backups,'Upgrade did not create a new backup'
        with zipfile.ZipFile(backups[0]) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            assert manifest['schema_revision']=='kb0006'
            assert any(name.startswith('assets/') for name in manifest['files'])
        summary={'passed':True,'from':'kb0006','to':'kb0007','original_revision_preserved':True,
                 'blocks_preserved':True,'anchor_preserved':True,'frozen_input_preserved':True,
                 'existing_vector_index_preserved':True,
                 'upgrade_backup_created':True,'backup_schema_revision':manifest['schema_revision']}
        summary['upgrade_backup_files']=[item.name for item in backups]
        (OUTPUT/'upgrade-result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Previous candidate upgraded: source revisions, anchors and frozen inputs preserved')
    finally:old.stop();new.stop()


if __name__=='__main__':main()
