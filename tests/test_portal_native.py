"""Two real account workspaces behind one portal, with a shared catalog and private research schemas."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

from backend.portal import create_app

PASSWORD = 'native-portal-password-123'


@unittest.skipUnless(os.name == 'nt' and os.environ.get('SHIXUE_MANAGED_TESTS') == '1', '需要独立内置 PostgreSQL')
class PortalNativeTests(unittest.TestCase):
    def test_shared_documents_private_research_and_jobs_survive_portal_restart(self):
        parent = Path('.local/portal-native').resolve()
        parent.mkdir(parents=True, exist_ok=True)
        started, release = threading.Event(), threading.Event()
        calls = []
        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                started.set(); release.wait(80)
                body = json.dumps({'choices':[{'message':{'content':'个人任务持续执行结果'},'finish_reason':'stop'}]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_):pass
        model = ThreadingHTTPServer(('127.0.0.1',0), Model)
        model_thread = threading.Thread(target=model.serve_forever, daemon=True); model_thread.start()
        server = server_thread = None
        fleets = []
        with tempfile.TemporaryDirectory(dir=parent, prefix='isolated-') as folder, patch.dict(os.environ, {'HF_HUB_OFFLINE':'1','SHIXUE_MODEL_API_KEY':''}):
            os.environ.pop('SHIXUE_PORTAL_PG_ADMIN_URL',None)
            root = Path(folder)
            application = create_app(root)
            accounts = application.state.accounts
            admin = accounts.create('admin','管理员',PASSWORD,'admin',require_change=False)
            alice = accounts.create('alice','研究者甲',PASSWORD,require_change=False)
            bob = accounts.create('bobby','研究者乙',PASSWORD,require_change=False)
            def until(check, label, timeout=100):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    try:
                        value=check()
                        if value:return value
                    except (httpx.HTTPError,KeyError,IndexError):pass
                    time.sleep(.1)
                self.fail(label)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
            def start_portal(app):
                fleets.append(app.state.fleet)
                instance=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
                thread=threading.Thread(target=instance.run,daemon=True);thread.start()
                until(lambda:instance.started,'portal startup',20)
                return instance,thread
            address=f'http://127.0.0.1:{port}'
            admin_client=httpx.Client(base_url=address,timeout=140)
            alice_client=httpx.Client(base_url=address,timeout=140)
            bob_client=httpx.Client(base_url=address,timeout=140)
            try:
                server,server_thread=start_portal(application)
                for client,name in [(admin_client,'admin'),(alice_client,'alice'),(bob_client,'bobby')]:
                    response=client.post('/api/session',json={'username':name,'password':PASSWORD})
                    self.assertEqual(response.status_code,200,response.text)
                configuration=admin_client.put('/api/admin/settings',json={'base_url':f'http://127.0.0.1:{model.server_port}/v1','model':'portal-fixture','provider_name':'隔离模拟模型'})
                self.assertEqual(configuration.status_code,200,configuration.text)
                project=alice_client.post('/api/projects',json={'name':'只有甲可见的项目'})
                self.assertEqual(project.status_code,200,project.text)
                bob_projects=bob_client.get('/api/projects')
                self.assertEqual(bob_projects.status_code,200,bob_projects.text)
                self.assertNotIn('只有甲可见的项目',bob_projects.text)
                self.assertEqual(bob_client.get('/api/admin/users').status_code,403)
                body='大家共享的财政赋税原文𠀀'.encode()
                upload=admin_client.post('/api/admin/library/uploads',json={'files':[{'relative_path':'甲的文献.txt','size':len(body)}]}).json()
                response=admin_client.put(f"/api/admin/library/uploads/{upload['id']}/files/0?offset=0",content=body,headers={'Content-Type':'application/octet-stream'})
                self.assertEqual(response.status_code,200,response.text)
                preview=admin_client.post(f"/api/admin/library/uploads/{upload['id']}/preview")
                self.assertEqual(preview.status_code,200,preview.text)
                confirmed=admin_client.post(f"/api/admin/library/knowledge/document-imports/{preview.json()['id']}/start",json={'category_name':'公共分类','item_type':'article'})
                self.assertEqual(confirmed.status_code,200,confirmed.text)
                group=confirmed.json()['group_id']
                def document():
                    listing=alice_client.get('/api/knowledge/document-tree',params={'group_id':group}).json()
                    items=listing.get('files',[])
                    if not items:return None
                    item=alice_client.get('/api/knowledge/document-entries/'+items[0]['id']).json()
                    return item if item.get('pages') else None
                entry=until(document,'uploaded document parsing')
                resource=f"/api/knowledge/editions/{entry['edition_id']}/files/{entry['asset_id']}"
                original=alice_client.get(resource)
                self.assertEqual(original.status_code,200,original.text)
                self.assertEqual(original.content,body)
                self.assertEqual(bob_client.get(resource).content,body)
                self.assertEqual(bob_client.get('/api/knowledge/document-entries/'+entry['id']).status_code,200)
                self.assertEqual(bob_client.get('/api/uploads/'+upload['id']).status_code,403)
                self.assertEqual(bob_client.post('/api/uploads',json={}).status_code,403)
                self.assertEqual(bob_client.delete('/api/knowledge/editions/'+entry['edition_id'],params={'confirmation':'甲的文献'}).status_code,403)
                self.assertEqual(bob_client.get('/api/admin/library/knowledge/catalog').status_code,403)
                selected=alice_client.put('/api/knowledge/projects/project-main/sources/'+entry['edition_id'],json={'roles':['reference']})
                self.assertEqual(selected.status_code,200,selected.text)
                self.assertTrue(alice_client.get('/api/knowledge/projects/project-main/sources').json())
                self.assertEqual(bob_client.get('/api/knowledge/projects/project-main/sources').json(),[])
                note=alice_client.post('/api/knowledge/notes',json={'title':'甲的私人笔记','body':'原文共享，想法私有'})
                self.assertEqual(note.status_code,200,note.text)
                self.assertEqual(bob_client.get('/api/knowledge/notes/'+note.json()['id']).status_code,404)
                self.assertEqual(bob_client.get('/api/knowledge/notes').json(),[])
                detail=alice_client.get('/api/knowledge/editions/'+entry['edition_id']).json()
                block=detail['blocks'][0]
                anchor=alice_client.post('/api/knowledge/anchors',json={'block_id':block['id'],'start':0,'end':3})
                self.assertEqual(anchor.status_code,200,anchor.text)
                self.assertEqual(bob_client.get('/api/knowledge/anchors/'+anchor.json()['id']).status_code,404)
                usage=admin_client.get('/api/admin/library/knowledge/editions/'+entry['edition_id']+'/delete-preview')
                self.assertEqual(usage.status_code,200,usage.text)
                self.assertEqual(usage.json()['project_count'],1)
                self.assertEqual(usage.json()['anchors'],1)
                self.assertNotIn('project_id',usage.text)
                frozen=alice_client.post('/api/knowledge/working-inputs',json={'project_id':'project-main','edition_id':entry['edition_id']})
                self.assertEqual(frozen.status_code,200,frozen.text)
                updated=admin_client.post('/api/admin/library/knowledge/editions/'+entry['edition_id']+'/revisions',json={'expected_revision':detail['active_revision'],'pages':[{'asset_id':entry['asset_id'],'page_index':1,'body':'管理员发布的新录文','source_block_id':block['id']}]})
                self.assertEqual(updated.status_code,200,updated.text)
                self.assertEqual(alice_client.get('/api/knowledge/anchors/'+anchor.json()['id']).json()['quote'],anchor.json()['quote'])
                self.assertIn('大家共享',json.dumps(frozen.json(),ensure_ascii=False))
                self.assertIn('管理员发布的新录文',bob_client.get('/api/knowledge/editions/'+entry['edition_id']).text)
                self.assertEqual(alice_client.get('/api/knowledge/health').json()['ready'],True)
                import psycopg
                from backend.portal_database import native_url
                configs=[json.loads((application.state.fleet.runtime(accounts.get(user['id']))/'database.json').read_text(encoding='utf-8')) for user in (alice,bob)]
                from sqlalchemy.engine import make_url
                self.assertEqual(make_url(configs[0]['runtime_url']).database,make_url(configs[1]['runtime_url']).database)
                with psycopg.connect(native_url(configs[1]['runtime_url']),autocommit=True) as restricted:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):restricted.execute('UPDATE public.kb_works SET title=title')
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):restricted.execute('SELECT * FROM '+configs[0]['schema']+'.kb_notes')
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):restricted.execute('SELECT * FROM public.kb_notes')
                self.assertEqual(len(list((root/'shared-library'/'assets'/'knowledge').rglob('*.txt'))),1)
                self.assertFalse((root/'workspaces'/accounts.get(alice['id'])['workspace_id']/'assets'/'knowledge').exists())
                library_config=json.loads((application.state.fleet.runtime(application.state.fleet.library)/'shared-database.json').read_text(encoding='utf-8'))
                with psycopg.connect(native_url(library_config['runtime_url']),autocommit=True) as librarian:
                    self.assertEqual(librarian.execute('SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()[0],False)
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):librarian.execute('SELECT * FROM '+configs[0]['schema']+'.kb_notes')
                self.assertEqual(alice_client.put('/api/settings',json={'base_url':'https://untrusted.invalid','model':'leak'}).status_code,403)
                task=alice_client.post('/api/tasks',json={'task_type':'summary','source_text':'任务应独立于网页和门户执行'}).json()
                self.assertIn('id',task,task)
                self.assertTrue(started.wait(35),'worker did not execute queued task')
                self.assertEqual(bob_client.get('/api/tasks/'+task['id']).status_code,404)
                previous_host=application.state.fleet.runtime(accounts.get(alice['id']))/'host.json'
                host_pid=json.loads(previous_host.read_text(encoding='utf-8'))['pid']
                server.should_exit=True;server_thread.join(timeout=20)
                self.assertFalse(server_thread.is_alive())
                server,server_thread=start_portal(create_app(root))
                self.assertEqual(alice_client.get('/api/tasks/'+task['id']).json()['status'],'running')
                self.assertEqual(json.loads(previous_host.read_text(encoding='utf-8'))['pid'],host_pid)
                release.set()
                final=until(lambda:(value if (value:=alice_client.get('/api/tasks/'+task['id']).json()).get('status')=='completed' else None),'task after portal restart',40)
                self.assertEqual(final['artifact']['content'],'个人任务持续执行结果')
                self.assertEqual(len(calls),1)
                # A shared native DB can choose another port after a host restart.
                # Existing private hosts must reconnect with their restricted role.
                current_fleet=fleets[-1]
                current_fleet.stop(current_fleet.library)
                until(lambda:current_fleet.state(current_fleet.library)['state']=='stopped','library shutdown',40)
                current_fleet.ensure_library()
                until(lambda:alice_client.get('/api/knowledge/notes/'+note.json()['id']).status_code==200,'private host reconnects after database restart',60)
                until(lambda:bob_client.get(resource).content==body,'shared original survives database restart',60)
                self.assertTrue((root/'workspaces'/accounts.get(alice['id'])['workspace_id']/'library.sqlite').is_file())
                self.assertTrue((root/'workspaces'/accounts.get(bob['id'])['workspace_id']/'library.sqlite').is_file())
                changed=admin_client.patch('/api/admin/users/'+alice['id'],json={'display_name':'甲','role':'user','enabled':False})
                self.assertEqual(changed.status_code,200,changed.text)
                self.assertEqual(alice_client.get('/api/projects').status_code,401)
                self.assertEqual(bob_client.get('/api/projects').status_code,200)
            finally:
                release.set()
                if server:
                    server.should_exit=True
                    server_thread.join(timeout=25)
                for client in [admin_client,alice_client,bob_client]:client.close()
                for fleet in fleets:
                    fleet.request_stop_all([accounts.get(user['id']) for user in accounts.list()])
                for fleet in fleets:
                    for child in fleet.children.values():child.wait(timeout=100)
                model.shutdown();model.server_close();model_thread.join(timeout=5)

    def test_external_database_provisioning_and_shared_runtime_roles(self):
        import psycopg
        from sqlalchemy.engine import make_url
        from backend.portal_database import provision,native_url
        from backend.portal_fleet import WorkspaceFleet
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge import repository as r
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            product=ProductDatabase(root/'cluster','internal')
            fleet=None
            identities=[]
            try:
                product.start();self.assertFalse(product.startup_error)
                admin_url=product.manager.url.render_as_string(hide_password=False)
                fleet=WorkspaceFleet(root/'portal')
                identities=[{'id':uuid.uuid4().hex,'workspace_id':uuid.uuid4().hex} for _ in range(2)]
                first=provision(admin_url,identities[0]['workspace_id'],fleet.runtime(identities[0])/'database.json')
                second=provision(admin_url,identities[1]['workspace_id'],fleet.runtime(identities[1])/'database.json')
                self.assertNotEqual(make_url(first).database,make_url(second).database)
                with patch.dict(os.environ,{'SHIXUE_KB_DATABASE_URL':first}):
                    item=CatalogService(root/'first').create({'title':'私有文献','item_type':'article'})
                with patch.dict(os.environ,{'SHIXUE_KB_DATABASE_URL':second}):
                    with r.database.transaction(root/'second') as db:
                        with self.assertRaises(HTTPException):r.edition(db,item['id'])
                other=make_url(first).set(database=make_url(second).database).render_as_string(hide_password=False)
                with self.assertRaises(psycopg.OperationalError):
                    psycopg.connect(native_url(other),connect_timeout=3)
                identities=[{'id':uuid.uuid4().hex,'workspace_id':uuid.uuid4().hex} for _ in range(2)]
                with patch.dict(os.environ,{'SHIXUE_PORTAL_PG_ADMIN_URL':admin_url,'SHIXUE_MODEL_API_KEY':'','HF_HUB_OFFLINE':'1'}):
                    targets=[fleet.ensure(identity) for identity in identities]
                    for target in targets:
                        with httpx.Client(base_url=target.url,headers={'X-App-Token':target.token},timeout=10) as client:
                            response=client.get('/api/knowledge/health')
                            self.assertEqual(response.status_code,200,response.text)
                            self.assertTrue(response.json()['ready'],response.json())
                    with httpx.Client(base_url=targets[1].url,headers={'X-App-Token':targets[1].token},timeout=10) as client:
                        self.assertEqual(client.get('/api/knowledge/editions/'+str(item['id'])).status_code,404)
            finally:
                if fleet:
                    fleet.request_stop_all(identities)
                    for child in fleet.children.values():child.wait(timeout=100)
                r.database.dispose();product.stop()

    def test_explicit_legacy_publication_retains_ids_and_private_notes(self):
        import psycopg
        from backend.portal_database import provision,native_url
        from backend.portal_shared_database import provision_private
        from backend.portal_migrate import publish_legacy
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.schemas import Note
        from backend.knowledge.document_library import DocumentLibrary
        from backend.knowledge.ingestion import publish
        from backend.knowledge import repository as r
        from backend.identity import workspace_key
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/'source';source.mkdir()
            product=ProductDatabase(source,'internal')
            try:
                product.start();self.assertFalse(product.startup_error)
                old=product.manager.url.render_as_string(hide_password=False)
                item=CatalogService(source).create({'title':'明确发布的旧文献','item_type':'article'})
                job=CatalogService(source).import_file(item['id'],'旧文献.txt','旧录文内容'.encode())
                with r.database.transaction(source) as db:
                    publish(db,item['id'],[{'asset_id':str(job['asset_id']),'page_index':1,'body':'旧录文内容'}])
                    r.execute(db,"UPDATE kb_jobs SET state='cancelled'")
                    note=r.save_note(db,None,Note(title='旧的私有笔记',body='只对原账号可见').model_dump(mode='json'))
                DocumentLibrary(source).groups()
                identifier=uuid.uuid4().hex
                runtime=provision(old,identifier,root/'new-database.json')
                configuration=json.loads((root/'new-database.json').read_text(encoding='utf-8'))
                library={'root':str(root/'shared'),'key':workspace_key(root/'shared'),'runtime_url':runtime,'owner_url':configuration['owner_url'],'admin_url':old}
                private=provision_private(library,uuid.uuid4().hex,root/'private.json')
                publish_legacy(old,library,private,source)
                publish_legacy(old,library,private,source) # durable receipt handles post-commit retries
                with patch.dict(os.environ,{'SHIXUE_KB_DATABASE_URL':private['runtime_url'],'SHIXUE_PRIVATE_SCHEMA':private['schema'],'SHIXUE_LIBRARY_KEY':library['key'],'SHIXUE_LIBRARY_ROOT':library['root']}):
                    with r.database.transaction(root/'user') as db:
                        self.assertEqual(r.edition(db,item['id'])['title'],'明确发布的旧文献')
                        self.assertEqual(str(r.one(db,'SELECT * FROM kb_notes WHERE id=:id',id=note['id'])['id']),str(note['id']))
                        key=r.one(db,'SELECT storage_key FROM kb_assets')['storage_key']
                        self.assertEqual(r.asset_path(root/'user',key).read_bytes(),'旧录文内容'.encode())
                        self.assertEqual(r.execute(db,'SELECT count(*) FROM kb_blocks').scalar_one(),1)
                with psycopg.connect(native_url(runtime)) as public:
                    self.assertEqual(public.execute('SELECT count(*) FROM public.kb_notes').fetchone()[0],0)
                with r.database.transaction(source) as db:
                    self.assertEqual(r.execute(db,'SELECT count(*) FROM kb_notes').scalar_one(),1)
            finally:
                r.database.dispose();product.stop()
