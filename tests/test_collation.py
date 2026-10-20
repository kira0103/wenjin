import io
import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as service
from backend import collation

SOURCE='卷五\n事类天地【丨丨之精】日月【明月】\n卷六\n格物总论【天地运行】\n'


def answer(messages, model, budget):
    source=json.loads(messages[-1]['content'])['source_to_punctuate']
    return {'text':json.dumps({'text':source+'。','questions':[]},ensure_ascii=False),
            'usage':{'prompt_tokens':10,'completion_tokens':7,'total_tokens':17},'response_id':'fixture','finish_reason':'stop','latency_ms':1}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        service.initialize(self.root)
        service.service.token='batch-test'
        self.client=TestClient(service.app,headers={'X-App-Token':'batch-test'})
        self.secret=patch.object(service.keyring,'get_password',return_value=None)
        self.secret.start()
        self.kick=patch.object(service.batch_runner,'kick')
        self.kick.start()
        self.old_delay=service.batch_runner.retry_delay
        service.batch_runner.retry_delay=0
        self.client.put('/api/settings',json={'base_url':'http://127.0.0.1:9999/v1','model':'fixture','provider_name':'test'})
        self.project=self.client.post('/api/projects',json={'name':'批量测试','project_type':'collation'}).json()['id']
        self.material=self.upload(SOURCE)
        self.run=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':self.material['id']}).json()
        self.texts=self.client.get(f'/api/preprocess/runs/{self.run["id"]}/texts').json()['items']

    def tearDown(self):
        self.kick.stop()
        self.secret.stop()
        service.batch_runner.retry_delay=self.old_delay
        self.client.close()
        self.directory.cleanup()

    def upload(self,text):
        r=self.client.post('/api/materials/import',params={'filename':'底本.txt','project_id':self.project,'role':'base'},content=text.encode(),headers={'Content-Type':'application/octet-stream'})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def create(self,ids=None,request_id=None):
        body={'run_id':self.run['id'],'request_id':request_id or secrets.token_hex(12)}
        if ids is not None:
            body['text_ids']=ids
        r=self.client.post(f'/api/projects/{self.project}/collation/batches',json=body)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def execute(self,reply=answer):
        with patch.object(service,'request_completion',side_effect=reply) as model:
            service.batch_runner._worker(self.root)
            return model.call_count

    def get(self,batch):
        return self.client.get('/api/collation/batches/'+batch['id']).json()

    def items(self,batch):
        return self.client.get('/api/collation/batches/'+batch['id']+'/items').json()['items']

    def action(self,batch,name):
        r=self.client.post('/api/collation/batches/'+batch['id']+'/action',json={'action':name})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def unit(self,text):
        r=self.client.post('/api/preprocess/runs/'+self.run['id']+'/units',json={'text_ids':[text['id']]})
        self.assertEqual(r.status_code,200,r.text)
        return self.client.get('/api/units/'+r.json()[0]['id']).json()

    def save(self,unit,content=None,status='working'):
        r=self.client.put('/api/units/'+unit['id'],json={'expected_revision':unit['revision'],'content':content or unit['content'],'notes':unit['notes'],'status':status})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def test_sequential_execution_auto_saves_review_and_usage(self):
        batch=self.create()
        self.assertEqual(self.execute(),2)
        finished=self.get(batch)
        self.assertEqual(finished['status'],'completed')
        self.assertEqual(finished['counts']['done'],2)
        self.assertEqual(finished['usage']['total_tokens'],34)
        self.assertEqual(finished['pending_review'],2)
        items=self.items(batch)
        self.assertEqual([i['title'] for i in items],['卷五','卷六'])
        for item in items:
            unit=self.client.get('/api/units/'+item['unit_id']).json()
            self.assertEqual(unit['status'],'review')
            self.assertTrue(unit['content'].rstrip().endswith('。'))
            self.assertTrue(unit['content'].endswith('\n'))
            baseline=self.client.get('/api/units/'+unit['id']+'/baseline').json()['baseline']
            self.assertFalse(collation.punctuation_error(baseline,unit['content']))
            self.assertTrue(any(n['kind']=='ai' and n['status']=='pending' for n in unit['notes']))
        with zipfile.ZipFile(io.BytesIO(self.client.get('/api/collation/batches/'+batch['id']+'/export').content)) as archive:
            self.assertIn('待复核稿/卷五.txt',archive.namelist())
            self.assertIn('_批次清单.json',archive.namelist())
        events=self.client.get('/api/collation/batches/'+batch['id']+'/events').json()
        starts=[e['message'] for e in events if e['message'].startswith('开始文件')]
        self.assertEqual(starts,['开始文件：卷五','开始文件：卷六'])

    def test_one_bounded_split_shares_three_request_budget(self):
        source='卷五\n'+'正文行天地玄黄\n'*50
        material=self.upload(source)
        self.run=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':material['id']}).json()
        batch=self.create()
        count=0
        def first_bad(messages,model,budget):
            nonlocal count
            count+=1
            if count==1:
                reply=answer(messages,model,budget)
                reply['text']='不是合法 JSON'
                return reply
            return answer(messages,model,budget)
        self.assertEqual(self.execute(first_bad),3)
        self.assertEqual(self.get(batch)['status'],'completed')
        self.assertEqual(self.get(batch)['usage']['total_tokens'],51)
        self.assertEqual(self.items(batch)[0]['chunks'],2)

    def test_precall_failure_is_known_no_usage(self):
        batch=self.create()
        def precall(*args):
            raise service.ModelCallError('密钥已移除，未发请求',fatal=True,sent=False)
        self.execute(precall)
        value=self.get(batch)
        self.assertEqual(value['status'],'paused')
        self.assertEqual(value['usage']['total_tokens'],0)
        self.assertEqual(value['usage']['unknown_requests'],0)

    def test_model_settings_are_frozen_and_costs_use_reported_usage(self):
        self.client.put('/api/settings',json={'base_url':'http://127.0.0.1:9999/v1','model':'fixture','price_input_per_million':2,'price_output_per_million':4,'currency':'CNY','price_date':'2026-10-08'})
        batch=self.create([self.texts[0]['id']])
        self.client.put('/api/settings',json={'base_url':'http://127.0.0.1:9999/v1','model':'changed'})
        seen=[]
        def frozen(messages,model,budget):
            seen.append(model['model'])
            return answer(messages,model,budget)
        self.execute(frozen)
        self.assertEqual(seen,['fixture'])
        self.assertAlmostEqual(self.get(batch)['estimated_cost'],.000048)
        self.assertNotIn('price_input_per_million',service.read_model_settings())

    def test_upgrade_baseline_backup_and_existing_draft_are_preserved(self):
        unit=self.unit(self.texts[0])
        self.save(unit,'升级前人工稿')
        with service.connection() as db:
            db.execute('ALTER TABLE research_units DROP COLUMN baseline_text')
        service.initialize(self.root)
        self.assertTrue((self.root/'backups'/'before-0.3.7.sqlite').exists())
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],'升级前人工稿')
        baseline=self.client.get('/api/units/'+unit['id']+'/baseline').json()
        self.assertEqual(baseline['baseline'],unit['content'])

    def test_baseline_is_current_whole_volume_and_immutable(self):
        unit=self.unit(self.texts[0])
        base=self.client.get('/api/units/'+unit['id']+'/baseline').json()
        self.assertEqual(base['baseline'],unit['content'])
        self.assertEqual(base['original'],SOURCE[self.texts[0]['start']:self.texts[0]['end']])
        self.assertNotIn('卷六',base['baseline'])
        self.save(unit,'人工编辑稿')
        self.assertEqual(self.client.get('/api/units/'+unit['id']+'/baseline').json(),base)
        with service.connection() as db:
            db.execute('UPDATE research_units SET baseline_text=NULL WHERE id=?',(unit['id'],))
        self.assertEqual(self.client.get('/api/units/'+unit['id']+'/baseline').json(),base)

    def test_idempotent_start_and_invalid_scope(self):
        batch=self.create(request_id='same-request')
        self.assertEqual(self.create(request_id='same-request')['id'],batch['id'])
        failed=self.client.post(f'/api/projects/{self.project}/collation/batches',json={'run_id':self.run['id'],'request_id':'another-request','text_ids':['not-in-source']})
        self.assertEqual(failed.status_code,400)
        foreign=self.client.post('/api/projects',json={'name':'无关','project_type':'collation'}).json()['id']
        self.assertEqual(self.client.get(f'/api/projects/{foreign}/collation/scope',params={'run_id':self.run['id']}).status_code,400)
        self.assertEqual(self.execute(),2)
        self.assertEqual(self.create(request_id='same-request')['id'],batch['id'])

    def test_failed_validation_usage_is_counted_and_next_file_continues(self):
        def bad_first(messages,model,budget):
            if json.loads(messages[-1]['content'])['file']=='卷五':
                result=answer(messages,model,budget)
                result['text']='不兼容的正文格式'
                return result
            return answer(messages,model,budget)
        batch=self.create()
        self.assertEqual(self.execute(bad_first),4)
        value=self.get(batch)
        self.assertEqual(value['status'],'completed_with_errors')
        self.assertEqual(value['counts']['failed'],1)
        self.assertEqual(value['counts']['done'],1)
        self.assertEqual(value['usage']['total_tokens'],68)
        self.action(batch,'retry')
        self.assertEqual(self.execute(),1)
        value=self.get(batch)
        self.assertEqual(value['status'],'completed')
        self.assertEqual(value['usage']['requests'],5)
        self.assertEqual(value['usage']['total_tokens'],85)

    def test_missing_usage_remains_unknown_not_zero(self):
        def unknown(messages,model,budget):
            result=answer(messages,model,budget)
            result['usage']=None
            return result
        batch=self.create()
        self.execute(unknown)
        usage=self.get(batch)['usage']
        self.assertIsNone(usage['input_tokens'])
        self.assertIsNone(usage['total_tokens'])
        self.assertEqual(usage['unknown_requests'],2)
        self.assertEqual(usage['requests'],2)

    def test_pause_during_call_stops_new_dispatch_and_resume_reuses_done_chunk(self):
        entered,release=threading.Event(),threading.Event()
        calls=[]
        def blocking(messages,model,budget):
            calls.append(messages)
            entered.set()
            self.assertTrue(release.wait(5))
            return answer(messages,model,budget)
        batch=self.create()
        with patch.object(service,'request_completion',side_effect=blocking):
            worker=threading.Thread(target=service.batch_runner._worker,args=(self.root,))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.action(batch,'pause')['status'],'pausing')
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls),1)
        self.assertEqual(self.get(batch)['status'],'paused')
        self.assertEqual(self.get(batch)['counts']['done'],1)
        self.action(batch,'resume')
        self.assertEqual(self.execute(),1)
        self.assertEqual(self.get(batch)['usage']['requests'],2)

    def test_stop_keeps_finished_results_and_unexecuted_files(self):
        batch=self.create()
        def stop_on_response(messages,model,budget):
            self.action(batch,'stop')
            return answer(messages,model,budget)
        self.assertEqual(self.execute(stop_on_response),1)
        value=self.get(batch)
        self.assertEqual(value['status'],'stopped')
        self.assertEqual(value['counts']['done'],1)
        self.assertEqual(value['counts']['stopped'],1)
        self.assertEqual(value['needs_merge'],1)

    def test_existing_manual_draft_is_candidate_even_without_concurrent_edit(self):
        unit=self.save(self.unit(self.texts[0]),'已有人工稿')
        scope=self.client.get(f'/api/projects/{self.project}/collation/scope',params={'run_id':self.run['id']}).json()
        self.assertTrue(scope[0]['manual_modified'])
        batch=self.create([self.texts[0]['id']])
        self.execute()
        item=self.items(batch)[0]
        self.assertEqual(item['merge_status'],'candidate')
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],'已有人工稿')
        self.assertEqual(self.client.post('/api/collation/items/'+item['id']+'/adopt',json={'expected_revision':unit['revision']}).status_code,409)

    def test_edit_during_execution_does_not_get_overwritten(self):
        unit=self.unit(self.texts[0])
        batch=self.create([self.texts[0]['id']])
        def editing(messages,model,budget):
            self.save(unit,'执行期间人工修改')
            return answer(messages,model,budget)
        self.execute(editing)
        self.assertEqual(self.items(batch)[0]['merge_status'],'candidate')
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],'执行期间人工修改')

    def test_received_response_recovers_without_model_call(self):
        batch=self.create([self.texts[0]['id']])
        with service.connection() as db:
            chunk=db.execute('SELECT * FROM collation_chunks LIMIT 1').fetchone()
            item=db.execute('SELECT * FROM collation_items LIMIT 1').fetchone()
            frozen=json.loads(db.execute('SELECT model_json FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()[0])
            response=answer(collation.messages_for(item,chunk,'',frozen.get('collation_config'),frozen.get('collation_contract'),frozen.get('collation_input_frame')),frozen,8192)
            db.execute('INSERT INTO collation_attempts(id,batch_id,item_id,chunk_id,budget_id,status,started_at) VALUES(?,?,?,?,?,?,?)',('received-fixture',batch['id'],item['id'],chunk['id'],chunk['root_id'],'requesting',service.utc_now()))
        service.batch_runner._receipt(self.root,'received-fixture',response,1,'received')
        self.assertEqual(self.execute(),0)
        self.assertEqual(self.get(batch)['counts']['done'],1)
        self.assertEqual(self.get(batch)['usage']['total_tokens'],17)

    def test_dead_owner_recovers_and_unknown_inflight_is_preserved(self):
        batch=self.create([self.texts[0]['id']])
        with service.connection() as db:
            chunk=db.execute('SELECT * FROM collation_chunks LIMIT 1').fetchone()
            item=db.execute('SELECT * FROM collation_items LIMIT 1').fetchone()
            db.execute("UPDATE collation_batches SET status='running',owner='dead',active_since=?",(1,))
            db.execute("UPDATE collation_control SET owner='dead',pid=2147483646,expires=?",(9999999999,))
            db.execute('INSERT INTO collation_attempts(id,batch_id,item_id,chunk_id,budget_id,status,started_at) VALUES(?,?,?,?,?,?,?)',('lost-fixture',batch['id'],item['id'],chunk['id'],chunk['root_id'],'requesting',service.utc_now()))
        value=self.get(batch)
        self.assertEqual(value['status'],'interrupted')
        self.assertEqual(value['usage']['unknown_requests'],1)
        self.action(batch,'resume')
        self.assertEqual(self.execute(),1)
        self.assertEqual(self.get(batch)['usage']['requests'],2)
        self.assertEqual(self.get(batch)['usage']['unknown_requests'],1)

    def test_fatal_error_pauses_entire_batch(self):
        batch=self.create()
        def fatal(*args):
            raise service.ModelCallError('密钥失效',fatal=True)
        self.assertEqual(self.execute(fatal),1)
        self.assertEqual(self.get(batch)['status'],'paused')
        self.assertEqual(self.get(batch)['counts']['done'],0)
        self.assertEqual(self.get(batch)['usage']['unknown_requests'],1)

    def test_validated_cache_reuse_is_not_billed_again(self):
        first=self.create([self.texts[0]['id']])
        self.assertEqual(self.execute(),1)
        second=self.create([self.texts[0]['id']])
        self.assertEqual(self.execute(),0)
        self.assertEqual(self.get(first)['usage']['total_tokens'],17)
        self.assertIsNone(self.get(second)['usage']['total_tokens'])
        self.assertEqual(self.get(second)['usage']['requests'],0)
        self.assertEqual(self.get(second)['reused_chunks'],1)

    def test_active_lease_prevents_second_worker_dispatch(self):
        batch=self.create()
        with service.connection() as db:
            db.execute("UPDATE collation_control SET owner='other',pid=?,expires=?",(collation.os.getpid(),collation.time.time()+45))
        self.assertEqual(self.execute(),0)
        self.assertEqual(self.get(batch)['status'],'queued')
        with service.connection() as db:
            db.execute("UPDATE collation_control SET owner='',pid=0,expires=0")
        self.assertEqual(self.execute(),2)

    def test_long_volume_is_automatically_chunked_and_lossless(self):
        source='卷五\n'+'天地玄黄宇宙洪荒\n'*700
        material=self.upload(source)
        self.run=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':material['id']}).json()
        batch=self.create()
        calls=self.execute()
        self.assertGreater(calls,2)
        item=self.items(batch)[0]
        result=self.client.get('/api/collation/items/'+item['id']+'/result').json()
        self.assertEqual(result['baseline'],source)
        self.assertEqual(collation.punctuation_error(source,result['content']),'')
        self.assertEqual(item['completed_chars'],len(source))
        self.assertEqual(item['chunks'],calls+self.get(batch)['reused_chunks'])
        self.assertEqual(self.get(batch)['total'],1)

    def test_batch_snapshot_survives_repeated_preprocessing(self):
        batch=self.create()
        old=self.run['id']
        new=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':self.material['id']}).json()
        self.assertNotEqual(new['id'],old)
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old).status_code,200)
        self.execute()
        self.assertEqual(self.get(batch)['counts']['done'],2)

    def test_question_quotes_are_validated_and_reviewed_separately(self):
        def questioned(messages,model,budget):
            result=answer(messages,model,budget)
            source=json.loads(messages[-1]['content'])['source_to_punctuate']
            result['text']=json.dumps({'text':source+'。','questions':[{'quote':'天地','reason':'字形需查底本','suggestion':'不自动改字'}]},ensure_ascii=False)
            return result
        batch=self.create([self.texts[0]['id']])
        self.execute(questioned)
        item=self.items(batch)[0]
        self.assertEqual(item['pending_questions'],1)
        unit=self.client.get('/api/units/'+item['unit_id']).json()
        self.assertTrue(any(n['evidence']=='字形需查底本' and n['status']=='pending' for n in unit['notes']))


class PureTests(unittest.TestCase):
    def test_fidelity_error_identifies_missing_extra_and_non_bmp_characters(self):
        self.assertIn("原文应为 '𠀀'，模型返回 '甲'",collation.punctuation_error('𠀀乙','甲乙。'))
        self.assertIn('模型已结束',collation.punctuation_error('甲乙','甲。'))
        self.assertIn("原文应为 正文结束，模型返回 '乙'",collation.punctuation_error('甲','甲。乙'))
        self.assertEqual(collation.punctuation_error('春秋説题\n','《春秋説题》。\n'),'')

    def test_plan_preserves_spaces_and_non_bmp_characters(self):
        source='𠀀 甲乙\n\n'+'正文长行 '*90+'\n尾\n'
        pieces=list(collation.plan(source,100))
        self.assertEqual(''.join(p[2] for p in pieces),source)
        self.assertTrue(all(end-start<=100 for start,end,*_ in pieces))
        self.assertEqual(pieces[-1][1],len(source))

    def test_usage_distinguishes_zero_missing_and_subtotals(self):
        self.assertEqual(collation.token_usage({'prompt_tokens':0,'completion_tokens':0}),(0,0,0))
        self.assertEqual(collation.token_usage(None),(None,None,None))
        self.assertEqual(collation.token_usage({'input_tokens':100,'output_tokens':20,'total_tokens':120,'prompt_tokens_details':{'cached_tokens':90}}),(100,20,120))
        self.assertEqual(collation.token_usage({'prompt_tokens':True,'completion_tokens':-1}),(None,None,None))

    def test_invalid_quoted_evidence_and_truncation_rejected(self):
        response={'text':json.dumps({'text':'天地。','questions':[{'quote':'不存在','reason':'乱猜'}]}),'finish_reason':'stop'}
        with self.assertRaises(ValueError):
            collation.validate_reply('天地',response)
        response={'text':json.dumps({'text':'天地。','questions':[]}),'finish_reason':'length'}
        with self.assertRaises(ValueError):
            collation.validate_reply('天地',response)


if __name__=='__main__':
    unittest.main()
