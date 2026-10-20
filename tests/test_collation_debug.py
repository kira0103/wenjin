import json,secrets,unittest,os
from unittest.mock import patch
import httpx
import test_collation as fixture
from backend import app as service,collation
from backend.collation_prompt import Profile,request_options

class DebugTests(unittest.TestCase):
    setUp=fixture.BatchTests.setUp
    tearDown=fixture.BatchTests.tearDown
    upload=fixture.BatchTests.upload
    create=fixture.BatchTests.create
    action=fixture.BatchTests.action
    get=fixture.BatchTests.get
    items=fixture.BatchTests.items
    unit=fixture.BatchTests.unit
    save=fixture.BatchTests.save
    execute=fixture.BatchTests.execute
    def body(self,config=None):
        return {'config':config or Profile().model_dump(),'input_text':'天地玄黄\n宇宙洪荒','title':'调试样本','request_id':secrets.token_hex(12)}
    def debug(self,body=None,reply=fixture.answer):
        payload=body or self.body()
        with patch.object(service.debug_controller,'start'):
            r=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=payload)
        self.assertEqual(r.status_code,200,r.text)
        with patch.object(service,'request_completion',side_effect=reply) as model:
            service.debug_controller.execute(self.root,r.json()['id'])
        result=self.client.get(f'/api/projects/{self.project}/collation/debug/runs/'+r.json()['id']).json()
        return result,model.call_count
    def apply(self,result,revision=0,config=None):
        return self.client.put(f'/api/projects/{self.project}/collation/debug/config',json={'config':config or result['config'],'expected_revision':revision,'debug_run_id':result['id']})
    def test_debug_runs_once_preserves_manuscripts_and_raw_usage(self):
        unit=self.unit(self.texts[0]);self.save(unit,'人工正文保留')
        before=self.client.get('/api/units/'+unit['id']).json()
        result,calls=self.debug()
        self.assertEqual(calls,1);self.assertTrue(result['passed'])
        self.assertEqual(result['usage']['total_tokens'],17)
        self.assertEqual(result['validation']['text'],'天地玄黄\n宇宙洪荒。')
        self.assertEqual(before,self.client.get('/api/units/'+unit['id']).json())
    def test_empty_reasoning_only_length_is_not_generic_format_error_and_not_retried(self):
        response={'id':'response-length','choices':[{'message':{'content':None,'reasoning_content':'provider supplied reasoning'},'finish_reason':'length'}],
                  'usage':{'prompt_tokens':2020,'completion_tokens':6992,'total_tokens':9012,'completion_tokens_details':{'reasoning_tokens':6992}}}
        with patch.object(service.httpx,'post',return_value=httpx.Response(200,json=response)) as http:
            with patch.object(service.debug_controller,'start'):
                r=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=self.body())
            service.debug_controller.execute(self.root,r.json()['id'])
        value=self.client.get(f'/api/projects/{self.project}/collation/debug/runs/'+r.json()['id']).json()
        self.assertEqual(http.call_count,1);self.assertFalse(value['passed'])
        self.assertIn('推理 6992',value['error']);self.assertEqual(value['reply']['finish_reason'],'length')
        self.assertEqual(value['usage']['total_tokens'],9012);self.assertEqual(value['usage']['reasoning_tokens'],6992)
        self.assertIn('reasoning_content',value['reply']['raw_response'])
    def test_batch_length_failure_stops_identical_retry_and_counts_usage(self):
        batch=self.create([self.texts[0]['id']])
        def length(*args):
            raise service.ModelCallError('推理耗尽预算',output_limit=True,reply={'text':'','finish_reason':'length','usage':{'prompt_tokens':2020,'completion_tokens':6992,'total_tokens':9012}})
        self.assertEqual(self.execute(length),1)
        self.assertEqual(self.get(batch)['usage']['total_tokens'],9012)
        self.assertEqual(self.get(batch)['counts']['failed'],1)
        self.assertIn('点校调试',self.items(batch)[0]['error'])
    def test_save_requires_matching_successful_config_and_revision(self):
        result,_=self.debug()
        changed={**result['config'],'system_prompt':'新提示词'}
        self.assertEqual(self.apply(result,config=changed).status_code,409)
        saved=self.apply(result);self.assertEqual(saved.status_code,200,saved.text);self.assertEqual(saved.json()['revision'],1)
        self.assertEqual(self.apply(result).status_code,409)
    def test_failed_debug_cannot_apply_and_does_not_auto_retry(self):
        def invalid(*args): return {'text':'错误格式','usage':None,'finish_reason':'stop'}
        result,calls=self.debug(reply=invalid)
        self.assertEqual(calls,1);self.assertFalse(result['passed']);self.assertIsNone(result['usage']['total_tokens'])
        self.assertEqual(self.apply(result).status_code,409)

    def test_changed_glyph_is_reviewable_not_failed_and_does_not_retry(self):
        body={**self.body(),'input_text':'羣阳之精 天羣阳精也春秋説题\n'}
        def converted(*args):
            return {'text':json.dumps({'text':'羣阳之精 天羣阳精也《春秋说题》。','questions':[]}), 'finish_reason':'stop'}
        result,calls=self.debug(body,converted)
        self.assertEqual(calls,1);self.assertTrue(result['passed'])
        self.assertEqual(result['status'],'review');self.assertEqual(result['error'],'')
        self.assertFalse(result['validation']['characters_preserved'])
        self.assertGreater(result['validation']['quality']['text_changes'],0)
        self.assertEqual(self.apply(result).status_code,200)

    def test_historical_fidelity_failure_is_reviewable_without_rewriting_record(self):
        body={**self.body(),'input_text':'春秋説题'}
        def converted(*args): return {'text':'{"text":"《春秋说题》。","questions":[]}','finish_reason':'stop'}
        result,_=self.debug(body,converted)
        old='标点结果未通过原字校验：原文字符位置 2 附近出现改字或重排。'
        with service.connection() as db: db.execute("UPDATE collation_debug_runs SET status='failed',error=?,validation_json='{}' WHERE id=?",(old,result['id']))
        value=self.client.get(f'/api/projects/{self.project}/collation/debug/runs/'+result['id']).json()
        self.assertEqual(value['status'],'review');self.assertEqual(value['original_error'],old)
        self.assertEqual(value['validation']['text'],'《春秋说题》。')
        with service.connection() as db:
            self.assertEqual(db.execute('SELECT error FROM collation_debug_runs WHERE id=?',(result['id'],)).fetchone()[0],old)

    def test_applied_profile_used_by_new_batches_and_frozen_during_pause(self):
        config={**Profile().model_dump(),'system_prompt':'测试点校风格甲','max_tokens':16384,'thinking':'disabled'}
        first,_=self.debug(self.body(config));self.assertEqual(self.apply(first).status_code,200)
        batch=self.create([self.texts[0]['id']]);self.action(batch,'pause')
        second,_=self.debug(self.body({**config,'system_prompt':'测试点校风格乙','thinking':'enabled'}));self.assertEqual(self.apply(second,revision=1).status_code,200)
        seen=[]
        def reply(messages,model,budget): seen.append((messages[0]['content'],request_options(model),budget));return fixture.answer(messages,model,budget)
        self.action(batch,'resume');self.execute(reply)
        self.assertEqual(len(seen),1);self.assertIn('风格甲',seen[0][0]);self.assertNotIn('风格乙',seen[0][0]);self.assertEqual(seen[0][1]['thinking'],{'type':'disabled'});self.assertEqual(seen[0][2],16384)

    def test_book_title_rules_reach_custom_profile_preview_batch_and_frozen_resume(self):
        from backend.collation_prompt import CONTRACT, BOOK_TITLE_RULES
        config={**Profile().model_dump(),'system_prompt':'我的自定义点校要求','thinking':'disabled'}
        body=self.body(config)
        preview=self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json={k:v for k,v in body.items() if k!='request_id'}).json()['request']
        self.assertIn(BOOK_TITLE_RULES,preview['messages'][0]['content'])
        self.assertEqual(preview['thinking'],{'type':'disabled'})
        result,_=self.debug(body);self.assertEqual(self.apply(result).status_code,200)
        batch=self.create([self.texts[0]['id']]);self.action(batch,'pause')
        with service.connection() as db:
            frozen=json.loads(db.execute('SELECT model_json FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()[0])
        self.assertEqual(frozen['collation_contract'],CONTRACT)
        self.action(batch,'resume')
        def reply(messages,model,budget):
            self.assertIn(BOOK_TITLE_RULES,messages[0]['content'])
            self.assertNotIn('后来的新协议',messages[0]['content'])
            return fixture.answer(messages,model,budget)
        with patch.object(collation,'CONTRACT','后来的新协议'):
            self.assertEqual(self.execute(reply),1)
        self.assertEqual(self.get(batch)['status'],'completed')
        item=self.items(batch)[0]
        attempt=self.client.get('/api/collation/items/'+item['id']+'/attempts').json()['items'][0]
        self.assertEqual(attempt['request']['thinking'],{'type':'disabled'})
        self.assertIn(BOOK_TITLE_RULES,attempt['request']['messages'][0]['content'])
    def test_adapter_sends_exact_thinking_output_and_temperature_without_secret_logging(self):
        cfg={**Profile().model_dump(),'thinking':'disabled','max_tokens':32768,'temperature':0.1}
        response={'id':'fixture','choices':[{'message':{'content':'{"text":"天地玄黄\\n宇宙洪荒。","questions":[]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':3}}
        with patch.object(service.httpx,'post',return_value=httpx.Response(200,json=response)) as http:
            with patch.object(service.debug_controller,'start'):
                r=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=self.body(cfg))
            service.debug_controller.execute(self.root,r.json()['id'])
        body=http.call_args.kwargs['json'];self.assertEqual(body['max_tokens'],32768);self.assertEqual(body['thinking'],{'type':'disabled'});self.assertEqual(body['temperature'],.1)
        self.assertNotIn('Authorization',http.call_args.kwargs['headers'])

    def test_provider_default_is_omitted_from_preview_and_actual_http_in_both_modes(self):
        self.assertIsNone(Profile().max_tokens)
        response={'id':'fixture','choices':[{'message':{'content':'{"text":"天地玄黄\\n宇宙洪荒。","questions":[]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':3}}
        for thinking in ('enabled','disabled'):
            with self.subTest(thinking=thinking):
                body=self.body({**Profile().model_dump(),'thinking':thinking})
                preview=self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json={k:v for k,v in body.items() if k!='request_id'})
                self.assertEqual(preview.status_code,200,preview.text)
                self.assertNotIn('max_tokens',preview.json()['request'])
                with patch.object(service.httpx,'post',return_value=httpx.Response(200,json=response)) as http:
                    result,calls=self.debug(body,service.request_completion)
                self.assertTrue(result['passed']);self.assertEqual(calls,1)
                self.assertNotIn('max_tokens',http.call_args.kwargs['json'])
                self.assertNotIn('max_tokens',result['request'])
                self.assertEqual(http.call_args.kwargs['json']['thinking'],{'type':thinking})

    def test_new_batch_omits_cap_but_existing_batch_keeps_frozen_explicit_cap(self):
        first,_=self.debug(self.body({**Profile().model_dump(),'max_tokens':8192}))
        self.assertEqual(self.apply(first).status_code,200)
        old=self.create([self.texts[0]['id']]);self.action(old,'pause')
        inherited,_=self.debug();self.assertEqual(self.apply(inherited,revision=1).status_code,200)
        seen=[]
        def reply(messages,model,budget):
            seen.append(budget);return fixture.answer(messages,model,budget)
        self.action(old,'resume');self.execute(reply)
        new=self.create([self.texts[0]['id']]);self.execute(reply)
        self.assertEqual(seen,[8192,None])
        for batch,expected in ((old,8192),(new,None)):
            item=self.items(batch)[0]
            record=self.client.get('/api/collation/items/'+item['id']+'/attempts').json()['items'][0]
            self.assertEqual(record['request'].get('max_tokens'),expected)
            self.assertEqual('max_tokens' in record['request'],expected is not None)
    def test_preview_has_no_model_call_and_configured_capacity_preflight(self):
        payload=self.body();payload.pop('request_id')
        with patch.object(service,'request_completion') as model:
            r=self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json=payload)
        self.assertEqual(r.status_code,200,r.text);model.assert_not_called()
        self.assertIn('程序输出约定',r.json()['request']['messages'][0]['content'])
        self.client.put('/api/settings',json={'base_url':'http://127.0.0.1:9999/v1','model':'fixture','context_tokens':8192})
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json=payload).status_code,200)
        payload['config']['max_tokens']=8192
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json=payload).status_code,400)
    def test_debug_and_active_batch_do_not_dispatch_together(self):
        batch=self.create();body=self.body()
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=body).status_code,409)
        self.action(batch,'pause')
        with patch.object(service.debug_controller,'start'):
            value=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=body)
        self.assertEqual(value.status_code,200,value.text)
        self.assertEqual(self.client.post('/api/collation/batches/'+batch['id']+'/action',json={'action':'resume'}).status_code,409)
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/collation/batches',json={'run_id':self.run['id'],'request_id':'blocked-create'}).status_code,409)
    def test_idempotent_debug_request_cannot_change_payload(self):
        body=self.body()
        with patch.object(service.debug_controller,'start') as start:
            a=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=body)
            b=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=body)
            c=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json={**body,'input_text':'不同输入'})
        self.assertEqual(a.json()['id'],b.json()['id']);self.assertEqual(start.call_count,1);self.assertEqual(c.status_code,409)
    def test_old_batch_seed_reconstructs_old_prompt_and_is_project_scoped(self):
        batch=self.create([self.texts[0]['id']])
        with service.connection() as db:
            row=db.execute('SELECT model_json FROM collation_batches WHERE id=?',(batch['id'],)).fetchone();model=json.loads(row[0]);model.pop('collation_config');model.pop('collation_config_revision')
            for field in ('collation_validation_version','collation_contract','collation_input_frame'): model.pop(field,None)
            db.execute('UPDATE collation_batches SET model_json=? WHERE id=?',(json.dumps(model),batch['id']))
        self.execute()
        item=self.items(batch)[0];attempt=self.client.get('/api/collation/items/'+item['id']+'/attempts').json()['items'][0]
        seed=self.client.get(f'/api/projects/{self.project}/collation/debug/seed/'+attempt['id'])
        self.assertEqual(seed.status_code,200,seed.text);self.assertEqual(seed.json()['original_request']['max_tokens'],6992)
        self.assertNotIn('程序输出约定',seed.json()['original_request']['messages'][0]['content'])
        other=self.client.post('/api/projects',json={'name':'其他项目','project_type':'collation'}).json()['id']
        self.assertEqual(self.client.get(f'/api/projects/{other}/collation/debug/seed/'+attempt['id']).status_code,404)
    def test_interrupted_debug_is_not_automatically_replayed_after_upgrade(self):
        with patch.object(service.debug_controller,'start'):
            r=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=self.body())
        with service.connection() as db: db.execute('UPDATE collation_debug_runs SET owner_pid=99999999 WHERE id=?',(r.json()['id'],))
        with patch.object(service,'request_completion') as model: service.initialize(self.root)
        value=self.client.get(f'/api/projects/{self.project}/collation/debug/runs/'+r.json()['id']).json()
        self.assertEqual(value['status'],'interrupted');self.assertIsNone(value['usage']['total_tokens']);model.assert_not_called()

    def test_upgrade_backs_up_existing_draft_before_adding_debug_tables(self):
        unit=self.unit(self.texts[0]);self.save(unit,'升级前人工正文')
        with service.connection() as db:
            db.execute('DROP TABLE collation_debug_runs');db.execute('DROP TABLE collation_configs')
        service.initialize(self.root)
        self.assertTrue((self.root/'backups/before-0.3.8.sqlite').exists())
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],'升级前人工正文')

    def test_provider_echoed_credential_is_redacted_in_error_and_raw_response(self):
        secret='isolated-test-secret'
        response=httpx.Response(401,json={'error':{'message':'bad '+secret}})
        with patch.object(service.keyring,'get_password',return_value=secret),patch.object(service.httpx,'post',return_value=response):
            try: service.request_completion([],service.read_model_settings(),1024)
            except service.ModelCallError as error:
                self.assertNotIn(secret,str(error));self.assertNotIn(secret,error.reply['raw_response'])
            else: self.fail('Expected HTTP 401 error')

    def test_custom_long_prompt_reserves_context_during_batch_planning(self):
        from fastapi import HTTPException
        config=Profile().model_dump()
        model={'context_tokens':16384,'collation_config':config}
        normal=collation.chunk_limit(model)
        config={**config,'system_prompt':config['system_prompt']+'补充要求'*300}
        self.assertLess(collation.chunk_limit({**model,'collation_config':config}),normal)
        config={**config,'system_prompt':'长提示词'*1500}
        with self.assertRaises(HTTPException): collation.chunk_limit({**model,'collation_config':config})

    def test_copy_only_with_questions_is_not_a_successful_debug(self):
        source='羣物之祖 天者羣物之祖也故徧覆包含而无所殊建日月风雨以和之经隂阳寒暑以成之前汉董仲舒传\n'
        def copied(messages,model,budget):
            return {'text':json.dumps({'text':source,'questions':[{'quote':'羣','reason':'字形待核','suggestion':'群'}]},ensure_ascii=False),'usage':{'prompt_tokens':973,'completion_tokens':862,'total_tokens':1835},'finish_reason':'stop'}
        payload={**self.body(),'input_text':source}
        result,calls=self.debug(payload,copied)
        self.assertEqual(calls,1);self.assertEqual(result['status'],'no_change');self.assertFalse(result['passed'])
        self.assertEqual(result['usage']['total_tokens'],1835);self.assertEqual(result['validation']['quality']['inserted_punctuation'],0)
        self.assertEqual(len(result['validation']['questions']),1);self.assertEqual(self.apply(result).status_code,409)

    def test_new_batch_unchanged_long_body_is_failed_once_and_not_applied(self):
        source='卷一\n羣物之祖 天者羣物之祖也故徧覆包含而无所殊建日月风雨以和之经隂阳寒暑以成之前汉董仲舒传\n'
        material=self.upload(source);self.run=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':material['id']}).json()
        batch=self.create()
        def copied(messages,model,budget):
            text=json.loads(messages[-1]['content'])['source_to_punctuate']
            return {'text':json.dumps({'text':text,'questions':[]},ensure_ascii=False),'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'finish_reason':'stop'}
        self.assertEqual(self.execute(copied),1);self.assertEqual(self.get(batch)['counts']['failed'],1)
        self.assertEqual(self.get(batch)['usage']['total_tokens'],20);self.assertIn('没有给未标点',self.items(batch)[0]['error'])
        with service.connection() as db: self.assertEqual(db.execute('SELECT count(*) FROM research_units WHERE project_id=?',(self.project,)).fetchone()[0],0)

    def test_already_punctuated_or_header_only_can_remain_unchanged_in_batch(self):
        for source in ('卷一\n天文门\n天\n事类\n','天地，玄黄。\n宇宙，洪荒。\n'):
            reply={'text':json.dumps({'text':source,'questions':[]},ensure_ascii=False),'finish_reason':'stop'}
            result=collation.validate_reply(source,reply,require_progress=True)
            self.assertEqual(result.text,source)
            self.assertTrue(collation.punctuation_review(source,result.text)['requires_review'])

    def test_legacy_debug_copy_is_reported_as_no_change_without_rewriting_record(self):
        result,_=self.debug()
        old={'text':result['input_text'],'questions':[],'characters_preserved':True}
        with service.connection() as db: db.execute("UPDATE collation_debug_runs SET status='passed',validation_json=? WHERE id=?",(json.dumps(old),result['id']))
        updated=self.client.get(f'/api/projects/{self.project}/collation/debug/runs/'+result['id']).json()
        self.assertEqual(updated['status'],'no_change');self.assertFalse(updated['passed']);self.assertEqual(self.apply(updated).status_code,409)
        with service.connection() as db: self.assertEqual(db.execute('SELECT status FROM collation_debug_runs WHERE id=?',(result['id'],)).fetchone()[0],'passed')

    def test_progress_validation_has_separate_cache_key_for_old_batches(self):
        item={'title':'卷一'};chunk={'input_text':'未标点正文','lead':''};model=service.read_model_settings()
        old=collation.cache_key(item,chunk,model,'')
        new=collation.cache_key(item,chunk,{**model,'collation_validation_version':'punctuation-progress-v1'},'')
        self.assertNotEqual(old,new)

    def test_recommended_prompt_does_not_overwrite_saved_project_prompt(self):
        custom={**Profile().model_dump(),'system_prompt':'人工自定义点校约定'}
        result,_=self.debug(self.body(custom));self.assertEqual(self.apply(result).status_code,200)
        config=self.client.get(f'/api/projects/{self.project}/collation/debug/config').json()
        self.assertEqual(config['config']['system_prompt'],'人工自定义点校约定')
        self.assertIn('逐句添加逗号',config['recommended_prompt'])

    def test_boundary_whitespace_is_restored_but_internal_changes_still_fail(self):
        source='　　天地玄黄\n  宇宙洪荒\n\n '
        reply={'text':json.dumps({'text':'天地，玄黄。\n  宇宙，洪荒。','questions':[]},ensure_ascii=False)}
        result=collation.validate_reply(source,reply,input_frame='edge-whitespace-v1')
        self.assertEqual(result.text,'　　天地，玄黄。\n  宇宙，洪荒。\n\n ')
        self.assertFalse(collation.punctuation_error(source,result.text))
        bad={'text':json.dumps({'text':'天地，玄黄。\n宇宙，洪荒。','questions':[]},ensure_ascii=False)}
        with self.assertRaises(ValueError): collation.validate_reply(source,bad,input_frame='edge-whitespace-v1',allow_changes=False)

    def test_metadata_volume_title_is_not_accepted_as_fragment_body(self):
        source='红云玉殿 侍臣鹄立通明殿一朶红云拥玉皇\n'
        bad={'text':json.dumps({'text':'卷一\n红云玉殿 侍臣鹄立通明殿，一朶红云拥玉皇。','questions':[]},ensure_ascii=False)}
        with self.assertRaises(ValueError): collation.validate_reply(source,bad,input_frame='edge-whitespace-v1',allow_changes=False)
        payload=self.body();payload['title']='卷一';payload['input_text']=source;payload.pop('request_id')
        preview=self.client.post(f'/api/projects/{self.project}/collation/debug/preview',json=payload).json()
        self.assertIn('file 只是系统定位标签',preview['request']['messages'][0]['content'])
        self.assertEqual(json.loads(preview['request']['messages'][-1]['content'])['source_to_punctuate'],source.rstrip())

    def test_frame_cache_key_includes_original_boundary_whitespace(self):
        item={'title':'卷一'};model={'base_url':'http://127.0.0.1','model':'fixture','collation_config':Profile().model_dump(),'collation_input_frame':'edge-whitespace-v1'}
        a=collation.cache_key(item,{'input_text':'天地\n','lead':''},model,'')
        b=collation.cache_key(item,{'input_text':'　天地\n\n','lead':''},model,'')
        self.assertNotEqual(a,b)

    def test_blank_debug_sample_rejected_before_model_request(self):
        payload={**self.body(),'input_text':' \n　'}
        with patch.object(service,'request_completion') as model:
            response=self.client.post(f'/api/projects/{self.project}/collation/debug/runs',json=payload)
        self.assertEqual(response.status_code,400);model.assert_not_called()
        self.assertEqual(collation.edge_parts(' \n　'),(' \n　','',''))

    def test_private_use_glyphs_restored_exactly_in_text_and_notes(self):
        source='　江南七渡 其要\ue645处中流最\ue645者二\n'
        encoded,mapping=collation.protected_text(source.strip())
        self.assertIn('[[PUA_E645]]',encoded)
        reply={'text':json.dumps({'text':encoded+'。','questions':[{'quote':'要[[PUA_E645]]处','reason':'私用字待核','suggestion':'紧'}]},ensure_ascii=False)}
        result=collation.validate_reply(source,reply,input_frame='edge-whitespace-v1',glyph_mode='pua-placeholder-v1')
        self.assertEqual(result.text,'　江南七渡 其要\ue645处中流最\ue645者二。\n')
        self.assertEqual(result.questions[0].quote,'要\ue645处')
        self.assertFalse(collation.punctuation_error(source,result.text))

    def test_placeholder_deletion_internal_marks_and_collision_are_rejected(self):
        source='其要\ue645处 [[PUA_E645]]'
        encoded,mapping=collation.protected_text(source)
        token=next(iter(mapping));self.assertNotEqual(token,'[[PUA_E645]]')
        for text in (encoded.replace(token,''),encoded.replace(token,token[:-2]+'。]]')):
            with self.assertRaises(ValueError): collation.validate_reply(source,{'text':json.dumps({'text':text,'questions':[]})},glyph_mode='pua-placeholder-v1',allow_changes=False)

    def test_model_changed_glyph_retry_gets_precise_feedback_and_actual_request_is_saved(self):
        material=self.upload('卷一\n诏以付沿江守师长编天下之崄要不胜言也善用崄者常使崄在我而不在敌\n')
        self.run=self.client.post(f'/api/projects/{self.project}/preprocess/split',json={'material_id':material['id']}).json();batch=self.create();seen=[]
        def reply(messages,model,budget):
            payload=json.loads(messages[-1]['content']);seen.append(payload)
            source=payload['source_to_punctuate']
            output=source.replace('守师','守帅')+'。' if len(seen)==1 else source+'。'
            return {'text':json.dumps({'text':output,'questions':[]},ensure_ascii=False),'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20},'finish_reason':'stop'}
        self.assertEqual(self.execute(reply),1);self.assertEqual(self.get(batch)['status'],'completed')
        self.assertNotIn('previous_validation_feedback',seen[0])
        file=self.items(batch)[0];unit=self.client.get('/api/units/'+file['unit_id']).json();self.assertIn('沿江守帅',unit['content']);self.assertEqual(unit['status'],'review')
        attempts=self.client.get('/api/collation/items/'+file['id']+'/attempts').json()['items']
        self.assertEqual(json.loads(attempts[0]['request']['messages'][-1]['content']),seen[0])
        seed=self.client.get(f'/api/projects/{self.project}/collation/debug/seed/'+attempts[0]['id']).json()
        self.assertEqual(seed['original_request'],attempts[0]['request']);self.assertEqual(seed['validation_feedback'],'')

    def test_actual_request_column_upgrade_backs_up_before_adding_it(self):
        unit=self.unit(self.texts[0]);self.save(unit,'原人工稿')
        with service.connection() as db: db.execute('ALTER TABLE collation_attempts DROP COLUMN request_json')
        service.initialize(self.root)
        self.assertTrue((self.root/'backups/before-0.3.9.sqlite').exists())
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],'原人工稿')

if __name__=='__main__': unittest.main()
