"""Verify candidate migration, scoped preparations and native restore, isolated."""
import json,os,secrets,socket,subprocess,tempfile,time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from backend import app as seed
import hashlib


def main():
    project=Path(__file__).resolve().parents[1]
    binary=Path(os.environ.get('SHIXUE_CATALOG_BINARY',str(project/'build/backend/ShixueWorkbenchService/ShixueWorkbenchService.exe')))
    environment={**os.environ,'SHIXUE_KB_MODEL_CACHE':str(Path(os.environ['APPDATA'])/'shixue-workbench/models')}
    environment.pop('SHIXUE_KB_DATABASE_URL',None);environment.pop('SHIXUE_KB_RESTORE_URL',None)
    with tempfile.TemporaryDirectory(prefix='文献库 打包迁移-') as folder:
        root=Path(folder)/'旧资料 中文';target=Path(folder)/'恢复副本 中文'
        seed.initialize(root)
        data='卷一\n财政赋税𠀀\n卷二\n山川地理'.encode();original=root/'assets/原文.txt';original.write_bytes(data)
        with seed.connection() as db:
            db.execute("INSERT INTO bibliography(id,title,authors,year,version_label,created_at) VALUES('legacy-book','打包旧古籍','作者甲','明代','甲本',?)",(seed.utc_now(),))
            db.execute("INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES('legacy-file','project-main','原文.txt','打包旧古籍',?,?,2,1,?)",(hashlib.sha256(data).hexdigest(),str(original),seed.utc_now()))
            db.execute("INSERT INTO library_materials(material_id,entry_id,file_format,position_kind) VALUES('legacy-file','legacy-book','txt','section')")
            db.executemany('INSERT INTO pages(material_id,page_number,text) VALUES(?,?,?)',[('legacy-file',1,'卷一\n财政赋税𠀀'),('legacy-file',2,'卷二\n山川地理')])
            db.execute("INSERT INTO project_materials(project_id,material_id,roles_json,witness_label,scope_note) VALUES('project-main','legacy-file','[\"base\"]','甲本','本册')")
        def launch(directory):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            token=secrets.token_hex(32)
            process=subprocess.Popen([str(binary),'--port',str(port),'--data-dir',str(directory),'--token',token,'--database-mode','internal'],env=environment,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            client=httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-App-Token':token},timeout=90)
            for _ in range(300):
                if process.poll() is not None:raise RuntimeError('打包服务启动失败')
                try:
                    if client.get('/health').is_success:return process,client
                except httpx.TransportError:pass
                time.sleep(.2)
            raise RuntimeError('打包服务启动超时')
        def call(client,path,method='GET',**kwargs):
            response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()
        process,client=launch(root)
        try:
            status=call(client,'/api/knowledge/health');assert status['ready'] and status['mode']=='internal',status
            assert not call(client,'/api/knowledge/activation')['active']
            assert client.post('/api/materials/import?filename=blocked.txt',content=b'blocked').status_code==409
            assert call(client,'/api/knowledge/activation','POST')['active']
            material=call(client,'/api/materials?project_id=project-main')[0]
            source=call(client,'/api/knowledge/projects/project-main/sources')[0]
            payload={key:source[key] for key in ('version','mode','ranges','roles','witness_label','scope_note')};payload['ranges'][0]['page_from']=2
            call(client,'/api/knowledge/projects/project-main/sources/'+source['id'],'PATCH',json=payload)
            prepared=call(client,'/api/projects/project-main/preprocess/split','POST',json={'material_id':material['id']})
            assert call(client,'/api/projects/project-main/preprocess/runs?material_id='+material['id'])[0]['id']==prepared['id']
            texts=call(client,'/api/preprocess/runs/'+prepared['id']+'/texts')['items']
            units=call(client,'/api/preprocess/runs/'+prepared['id']+'/units','POST',json={'text_ids':[texts[0]['id']]})
            unit=call(client,'/api/units/'+units[0]['id']);assert '财政' not in unit['original_text'] and '山川' in unit['original_text']
            fixed=call(client,'/api/materials/'+unit['material_id']);assert fixed['kind']=='working_input'
            call(client,'/api/knowledge/projects/project-main/sources/'+source['id'],'DELETE')
            assert call(client,'/api/materials/'+unit['material_id'])['pages']==fixed['pages']
            paper=call(client,'/api/knowledge/catalog','POST',json={'title':'参考文献提取测试','item_type':'article'})
            call(client,'/api/knowledge/catalog/editions/'+paper['id']+'/imports?filename=paper.txt','POST',content='论文正文。\n参考文献\n[1] 作者甲，《财政制度研究》，2020，历史研究。'.encode())
            for _ in range(300):
                detail=call(client,'/api/knowledge/editions/'+paper['id'])
                if detail['active_revision']:break
                time.sleep(.2)
            else:raise RuntimeError('论文解析超时')
            alias=paper['id']+'~'+detail['files'][0]['id']
            candidate=call(client,'/api/materials/'+alias)['references'][0]
            added=call(client,'/api/references/'+candidate['id']+'/add-to-library','POST')
            assert added['date_label']=='2020' and added['title']=='财政制度研究'
            export=call(client,'/api/knowledge/exports','POST')
            for _ in range(300):
                jobs=call(client,'/api/knowledge/jobs');job=next(j for j in jobs if j['id']==export['id'])
                if job['state']=='failed':raise RuntimeError(job['error'])
                if job['state']=='succeeded':break
                time.sleep(.2)
            else:raise RuntimeError('打包备份超时')
            archive=client.get('/api/knowledge/exports/'+export['id']+'/file');archive.raise_for_status()
            bundle=Path(folder)/'成套备份.zip';bundle.write_bytes(archive.content)
            call(client,'/api/service/exit','POST');process.wait(timeout=55)
            assert not (root/'knowledge-db/postmaster.pid').exists()
            restored=subprocess.run([str(binary),'--restore-bundle',str(bundle),'--restore-target',str(target)],env=environment,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=150)
            assert restored.returncode==0,'打包内置恢复失败'
            client.close();process,client=launch(target)
            assert call(client,'/api/knowledge/activation')['active']
            assert call(client,'/api/units/'+unit['id'])['original_text']==unit['original_text']
            assert call(client,'/api/materials/'+unit['material_id'])['pages']==fixed['pages']
            call(client,'/api/service/exit','POST');process.wait(timeout=55)
            assert not (target/'knowledge-db/postmaster.pid').exists()
            report={'passed':True,'native_packaged_start':True,'legacy_migration':True,'scope':True,'frozen_input_after_unlink':True,'reference_candidates':True,'native_restore_restart':True,'clean_shutdown':True}
            (project/'.local/catalog-packaged-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report))
        finally:
            if process.poll() is None:
                client.post('/api/service/exit');process.wait(timeout=55)
            client.close()


if __name__=='__main__':main()
