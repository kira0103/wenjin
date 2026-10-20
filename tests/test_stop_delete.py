import json,threading,time,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from backend import app as service
from backend.model_transport import cancellable_post,ModelCallCancelled
import test_collation as fixture

class TransportTests(unittest.TestCase):
    def test_stop_cancels_socket_despite_keepalive_data(self):
        sent=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();sent.set()
                try:
                    for _ in range(100):self.wfile.write(b' \n');self.wfile.flush();time.sleep(.1)
                except OSError:pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        cancelled=threading.Event();result=[]
        def call():
            try:cancellable_post(f'http://127.0.0.1:{server.server_port}',{}, {},cancelled)
            except Exception as error:result.append(error)
        worker=threading.Thread(target=call);worker.start()
        try:
            self.assertTrue(sent.wait(3));started=time.monotonic();cancelled.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertLess(time.monotonic()-started,3)
            self.assertIsInstance(result[0],ModelCallCancelled);self.assertIsNone(result[0].reply['usage'])
        finally:cancelled.set();worker.join(3);server.shutdown();server.server_close()

class FlowTests(unittest.TestCase):
    setUp=fixture.BatchTests.setUp
    tearDown=fixture.BatchTests.tearDown
    upload=fixture.BatchTests.upload
    create=fixture.BatchTests.create
    execute=fixture.BatchTests.execute
    action=fixture.BatchTests.action
    get=fixture.BatchTests.get
    items=fixture.BatchTests.items
    unit=fixture.BatchTests.unit
    def preview(self,kind,identifier):
        url=f'/api/projects/{identifier}' if kind=='project' else f'/api/collation/batches/{identifier}'
        value=self.client.get(url+'/delete-preview').json()
        body={'confirmation':value['name'] if kind=='project' else identifier,'preview_token':value['preview_token']}
        return url,value,body
    def delete(self,url,body):return self.client.request('DELETE',url,json=body)

    def test_stop_interrupts_request_and_preserves_completed_work_with_unknown_usage(self):
        batch=self.create();entered=threading.Event()
        def slow(messages,model,budget):
            entered.set();self.assertTrue(model['_cancel_event'].wait(3));raise ModelCallCancelled()
        with patch.object(service,'request_completion',side_effect=slow) as model:
            worker=threading.Thread(target=service.batch_runner._worker,args=(self.root,));worker.start()
            self.assertTrue(entered.wait(3));self.action(batch,'stop');worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(model.call_count,1)
        finished=self.get(batch);self.assertEqual(finished['status'],'stopped')
        self.assertEqual(finished['usage']['unknown_requests'],1);self.assertIsNone(finished['usage']['total_tokens'])
        self.assertEqual(finished['counts']['failed'],0)

    def test_batch_delete_requires_confirmation_and_retains_saved_draft(self):
        batch=self.create();self.execute();item=self.items(batch)[0];before=self.client.get('/api/units/'+item['unit_id']).json()
        url,value,body=self.preview('batch',batch['id'])
        self.assertEqual(self.delete(url,{**body,'confirmation':'wrong'}).status_code,400)
        self.assertEqual(self.delete(url,body).status_code,200)
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get('/api/units/'+item['unit_id']).json(),before)
        self.assertTrue(self.client.get('/api/global-materials').json())
        self.assertTrue(list((self.root/'backups').glob('before-delete-batch-*.sqlite')))

    def test_active_batch_and_project_cannot_be_deleted(self):
        batch=self.create()
        for kind,identifier in (('batch',batch['id']),('project',self.project)):
            url,value,body=self.preview(kind,identifier);self.assertFalse(value['can_delete'])
            self.assertEqual(self.delete(url,body).status_code,409)

    def test_project_delete_cleans_owned_work_and_keeps_shared_library_and_other_project(self):
        batch=self.create();self.execute()
        other=self.client.post('/api/projects',json={'name':'另一研究'}).json()['id']
        self.client.put(f'/api/projects/{other}/materials/'+self.material['id'],json={'roles':['reference']})
        before=self.client.get('/api/global-materials').json()
        url,value,body=self.preview('project',self.project)
        self.assertEqual(self.delete(url,{**body,'confirmation':'wrong'}).status_code,400)
        deleted=self.delete(url,body);self.assertEqual(deleted.status_code,200,deleted.text)
        self.assertEqual(deleted.json()['cleanup_errors'],[])
        self.assertNotIn(self.project,[p['id'] for p in self.client.get('/api/projects').json()])
        self.assertEqual(self.client.post(url+'/restore').status_code,404)
        self.assertEqual([m['id'] for m in self.client.get('/api/global-materials').json()],[m['id'] for m in before])
        self.assertEqual(len(self.client.get('/api/projects/'+other+'/materials').json()),1)
        with service.connection() as db:
            for table in ('research_units','tasks','artifacts','preprocess_runs','collation_batches','collation_configs','project_materials'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE project_id=?',(self.project,)).fetchone()[0],0)

    def test_stale_project_preview_cannot_delete_newer_draft(self):
        url,value,body=self.preview('project',self.project);self.unit(self.texts[0])
        self.assertEqual(self.delete(url,body).status_code,409)

    def test_project_file_cleanup_does_not_remove_other_project_artifacts_or_checkpoints(self):
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        other=self.client.post('/api/projects',json={'name':'保留项目'}).json()['id']
        with service.connection() as db:
            for project,task,artifact in ((self.project,'owned-task','owned-artifact'),(other,'other-task','other-artifact')):
                db.execute('INSERT INTO tasks(id,project_id,task_type,title,instructions,status,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(task,project,'punctuate','测试','','completed','{}',service.utc_now(),service.utc_now()))
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',(artifact,project,task,None,'测试','punctuate','{"content":"成果"}',service.utc_now()))
                (self.root/'artifacts'/(artifact+'.json')).write_text('成果',encoding='utf-8')
        checkpoint_path=self.root/'checkpoints/runs.sqlite'
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            saver.setup()
            saver.conn.execute('INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,type,checkpoint,metadata) VALUES(?,?,?,?,?,?)',('owned-task','','1','json',b'{}',b'{}'))
            saver.conn.execute('INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,type,checkpoint,metadata) VALUES(?,?,?,?,?,?)',('other-task','','1','json',b'{}',b'{}'))
            saver.conn.commit()
        url,_,body=self.preview('project',self.project);result=self.delete(url,body)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['cleanup_errors'],[])
        self.assertFalse((self.root/'artifacts/owned-artifact.json').exists());self.assertTrue((self.root/'artifacts/other-artifact.json').exists())
        from contextlib import closing
        with closing(sqlite3.connect(checkpoint_path)) as db:self.assertEqual(db.execute('SELECT thread_id FROM checkpoints').fetchall(),[('other-task',)])

    def test_last_project_delete_creates_empty_replacement(self):
        with service.connection() as db:db.execute('DELETE FROM projects WHERE id!=?',(self.project,))
        url,_,body=self.preview('project',self.project);deleted=self.delete(url,body)
        self.assertEqual(deleted.status_code,200,deleted.text)
        projects=self.client.get('/api/projects').json();self.assertEqual(len(projects),1)
        self.assertNotEqual(projects[0]['id'],self.project)
