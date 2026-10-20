"""Exercise OCR, embeddings, RAG and backup in an isolated database using built EXE."""
import io,json,os,secrets,socket,subprocess,tempfile,time,uuid,threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

import httpx,psycopg
from psycopg import sql
from sqlalchemy import URL
from PIL import Image,ImageDraw,ImageFont
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.knowledge.migration import upgrade


def main():
    project=Path(__file__).resolve().parents[1];folder=project/'.local'/'knowledge'
    passwords={name:(folder/(name+'_password')).read_text().strip() for name in ('admin','owner','app')}
    database='shixue_kb_test_'+uuid.uuid4().hex
    admin=psycopg.connect(host='127.0.0.1',port=55432,dbname='shixue_knowledge',user='shixue_admin',password=passwords['admin'],autocommit=True)
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    process=None;mock=None;restore_database=None
    try:
        with psycopg.connect(host='127.0.0.1',port=55432,dbname=database,user='shixue_admin',password=passwords['admin']) as setup:
            for command in ('CREATE EXTENSION vector','CREATE EXTENSION pg_trgm','REVOKE CREATE ON SCHEMA public FROM PUBLIC',
                'GRANT USAGE,CREATE ON SCHEMA public TO shixue_owner','GRANT USAGE ON SCHEMA public TO shixue_app',
                'ALTER DEFAULT PRIVILEGES FOR ROLE shixue_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO shixue_app'):
                setup.execute(command)
        owner=URL.create('postgresql+psycopg',username='shixue_owner',password=passwords['owner'],host='127.0.0.1',port=55432,database=database)
        upgrade(owner)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        token=secrets.token_hex(32)
        environment={**os.environ,'SHIXUE_KB_DATABASE_URL':owner.set(username='shixue_app',password=passwords['app']).render_as_string(hide_password=False),
                     'SHIXUE_KB_MODEL_CACHE':str(Path(os.environ['APPDATA'])/'shixue-workbench'/'models')}
        with tempfile.TemporaryDirectory(prefix='kb-exe-full-') as directory:
            process=subprocess.Popen([str(project/'build'/'backend'/'ShixueWorkbenchService.exe'),'--port',str(port),'--data-dir',directory,'--token',token],
                env=environment,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-App-Token':token},timeout=90) as client:
                for _ in range(150):
                    try:
                        if client.get('/health').is_success:break
                    except httpx.TransportError:pass
                    if process.poll() is not None:raise RuntimeError('打包服务启动失败')
                    time.sleep(.1)
                def post(path,**kwargs):
                    response=client.post(path,**kwargs);response.raise_for_status();return response.json()
                activation=post('/api/knowledge/activation');assert activation['active'],'统一文献库未激活'
                doc=post('/api/knowledge/editions',json={'title':'打包流程测试','label':'独立样本','item_type':'ancient'})
                job=post(f"/api/knowledge/editions/{doc['id']}/imports?filename=source.txt",content='财政收入主要来自赋税。𠀀'.encode())
                def wait_all():
                    deadline=time.monotonic()+100
                    while time.monotonic()<deadline:
                        jobs=client.get('/api/knowledge/jobs').json()
                        if jobs and all(j['state'] not in {'queued','running'} for j in jobs):
                            if any(j['state']=='failed' for j in jobs):raise RuntimeError('打包后台任务失败')
                            return jobs
                        time.sleep(.2)
                    raise RuntimeError('打包后台任务超时')
                wait_all()
                found=post('/api/knowledge/search',json={'query':'政府收入来自税收','mode':'semantic','scope':{'edition_ids':[doc['id']]}})
                assert found['results'],'打包语义检索失败'
                image=Image.new('RGB',(900,180),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',48)
                draw.text((20,50),'明代财政制度',font=font,fill='black');buffer=io.BytesIO();image.save(buffer,format='PNG')
                scan=post('/api/knowledge/editions',json={'title':'扫描测试'})
                post(f"/api/knowledge/editions/{scan['id']}/imports?filename=scan.png",content=buffer.getvalue());wait_all()
                detail=client.get('/api/knowledge/editions/'+scan['id']).json();assert any('财政' in b['body'] for b in detail['blocks']),'打包 OCR 失败'
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers.get('Content-Length','0')))
                        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                        for text in ('财政收入主要来自赋税。','[E1]'):
                            self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':text}}]})+'\n\n').encode());self.wfile.flush()
                        self.wfile.write(b'data: [DONE]\n\n')
                mock=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=mock.serve_forever,daemon=True).start()
                response=client.put('/api/settings',json={'base_url':f'http://127.0.0.1:{mock.server_port}/v1','model':'local-stream-fixture','provider_name':'隔离测试'})
                response.raise_for_status()
                stream=client.post('/api/knowledge/ask-stream',json={'query':'财政','mode':'exact','scope':{'edition_ids':[doc['id']]}})
                assert '"type": "complete"' in stream.text and '"type": "delta"' in stream.text,'打包流式问答失败'
                export=post('/api/knowledge/exports');jobs=wait_all()
                archive=client.get(f"/api/knowledge/exports/{export['id']}/file");assert archive.is_success and archive.content[:2]==b'PK','打包备份失败'
                client.post('/api/service/exit');process.wait(timeout=10)
                bundle=Path(directory)/'backup.zip';bundle.write_bytes(archive.content)
                restore_database='shixue_kb_test_'+uuid.uuid4().hex
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_database)))
                restore_environment={**environment,'SHIXUE_KB_RESTORE_URL':owner.set(username='shixue_admin',password=passwords['admin'],database=restore_database).render_as_string(hide_password=False)}
                destination=Path(directory)/'restored'
                restored=subprocess.run([str(project/'build'/'backend'/'ShixueWorkbenchService.exe'),'--restore-bundle',str(bundle),'--restore-target',str(destination)],
                    env=restore_environment,creationflags=subprocess.CREATE_NO_WINDOW,capture_output=True,timeout=100)
                assert restored.returncode==0 and (destination/'library.sqlite').is_file(),'打包恢复失败'
                print('Packaged full workflows passed: TXT ingestion, local embeddings, Chinese OCR, validated streaming RAG, backup and native EXE restore')
    finally:
        if mock:mock.shutdown();mock.server_close()
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        if restore_database:
            assert restore_database.startswith('shixue_kb_test_')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(restore_database)))
        assert database.startswith('shixue_kb_test_')
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)));admin.close()


if __name__=='__main__':main()
