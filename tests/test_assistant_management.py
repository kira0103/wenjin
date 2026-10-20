import io,json,os,threading,time,unittest,uuid,zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend.knowledge import assistant_registry as registry,repository as r,collaboration
from backend.knowledge.worker import Worker,Cancelled,worker
from backend.knowledge.research_assistant import detail as research_detail
from tests import test_research_assistant as legacy
SETTINGS=legacy.SETTINGS


class SkillPackageRules(unittest.TestCase):
    def test_roundtrip_preserves_instruction_and_resources(self):
        value=registry.Definition(slug='paper-review',name='论文复核',description='论文分析',instructions='检查材料𠀀与论点',files={'references/guide.md':'原文不改字'})
        self.assertEqual(registry.import_package(registry.export_package(value.model_dump())).model_dump(),value.model_dump())

    def test_import_rejects_escape_links_scripts_duplicate_and_oversize(self):
        header='---\nname: paper-review\ndescription: 论文分析\n---\n核验原文'
        for bad in ['../escape.md','C:/private.md','paper-review/scripts/run.py','paper-review/../../escape.md']:
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as z:z.writestr('paper-review/SKILL.md',header);z.writestr(bad,'bad')
            with self.assertRaises(HTTPException):registry.import_package(buffer.getvalue())
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('paper-review/SKILL.md',header)
            link=zipfile.ZipInfo('paper-review/references/link.md');link.create_system=3;link.external_attr=0o120777<<16;z.writestr(link,'/outside')
        with self.assertRaises(HTTPException):registry.import_package(buffer.getvalue())
        with self.assertRaises(ValidationError):registry.Definition(slug='valid',name='x',description='x',instructions='x',files={'manifest.json':'override'})
        with self.assertRaises(ValidationError):registry.Definition(slug='valid',name='x',description='x',instructions='x',tools=['shell'])

    def test_handoff_remaps_ids_and_does_not_promote_missing_evidence(self):
        first={'evidence_id':'E1','block_id':'a','start_offset':0,'end_offset':1,'quote':'甲'}
        second={**first,'block_id':'b','quote':'乙'}
        evidence,text=collaboration.transfer([{'node_key':'a','title':'甲','answer':'观点 [E1]','evidence':[first]},
                                             {'node_key':'b','title':'乙','answer':'观点 [E1] 猜测 [E99]','evidence':[second]}])
        self.assertEqual([v['evidence_id'] for v in evidence],['E1','E2']);self.assertIn('[E2]',text);self.assertNotIn('[E99]',text)


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要隔离的内置 PostgreSQL')
class AssistantNative(unittest.TestCase):
    setUpClass=classmethod(legacy.ResearchNative.setUpClass.__func__)
    tearDownClass=classmethod(legacy.ResearchNative.tearDownClass.__func__)
    document=legacy.ResearchNative.document
    queued=legacy.ResearchNative.queued
    execute=legacy.ResearchNative.execute
    fake=legacy.ResearchNative.fake

    def setUp(self):
        legacy.ResearchNative.setUp(self)
        with r.database.transaction(self.root) as c:
            r.execute(c,"UPDATE kb_jobs SET state='cancelled' WHERE workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running')")
        from backend import app as service
        with service.connection() as c:
            for key,value in SETTINGS.items():c.execute('INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value',
                         (key,json.dumps(value) if key in ('context_tokens','max_output_tokens') else value))

    def tearDown(self):worker.stop();self.client.close()

    def api(self,path,data=None,method=None,expected=200):
        with patch.object(worker,'start'):
            response=self.client.request(method or ('POST' if data is not None else 'GET'),'/api/knowledge'+path,json=data)
        self.assertEqual(response.status_code,expected,response.text)
        return response.json()

    def skill(self,tools=None,publish=True):
        payload={'slug':'fixture-'+uuid.uuid4().hex[:12],'name':'自定义核验方法','description':'独立测试','instructions':'按旧方法核验古籍财政材料'}
        if tools is not None:payload['tools']=tools
        value=self.api('/skills',payload)
        if publish:value=self.api('/skills/'+value['id']+'/publish',{'edit_version':value['edit_version']})
        return value

    def team(self,workflow='compare',**options):
        edition,_,_=self.document('财政赋税与折银论文观点，须对照材料。')
        with r.database.transaction(self.root) as c:r.execute(c,"UPDATE kb_works SET item_type='article' WHERE id=(SELECT work_id FROM kb_editions WHERE id=:e)",e=edition)
        return self.api('/collaborations',{'query':'财政赋税与折银有何依据？','mode':'exact','scope':{'edition_ids':[self.edition,edition]},'workflow':workflow,'token_budget':180000,**options})

    def completion(self,messages,settings,budget):
        body=json.loads(messages[-1]['content'])
        if body['task']=='plan':
            action={'action':'finish'} if any(s['action']=='search' for s in body['recent_steps']) else {'action':'search','query':'财政赋税','mode':'exact'}
            return {'text':json.dumps(action),'usage':{'total_tokens':20}}
        return {'text':'财政赋税与折银的论断应对照材料。[E1]','usage':{'total_tokens':30}}

    def drain(self,identifier,completion=None):
        runner=Worker();runner.root=self.root;runner.research_model_settings=lambda:SETTINGS;runner.research_completion=completion or self.completion
        for _ in range(20):
            collaboration.advance(self.root,SETTINGS)
            value=collaboration.detail(self.root,identifier)
            if value['state'] not in ('queued','running'):return value
            jobs=[]
            for _ in range(2):
                job=runner.claim(research_only=True)
                if job:jobs.append(dict(job))
            with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(runner.run,jobs))
        self.fail('协作任务没有在有限步骤内结束')

    def test_builtin_seed_and_user_draft_are_not_overwritten(self):
        skills=self.api('/skills');self.assertEqual(len(skills),6)
        agents=self.api('/agents');self.assertEqual(len(agents),5)
        value=skills[0];draft={**value['draft'],'name':'我修改的名称','edit_version':value['edit_version']}
        updated=self.api('/skills/'+value['id'],draft,method='PUT');self.assertEqual(updated['draft']['name'],'我修改的名称')
        self.assertEqual(next(v for v in self.api('/skills') if v['id']==value['id'])['draft']['name'],'我修改的名称')
        self.api('/skills/'+value['id'],draft,method='PUT',expected=409)

    def test_pinned_skill_injection_new_version_and_rollback(self):
        skill=self.skill();old=skill['active_version'];run=self.queued(skill_version_ids=[old])
        draft={**skill['draft'],'instructions':'按新方法复核','edit_version':skill['edit_version']}
        edited=self.api('/skills/'+skill['id'],draft,method='PUT');new=self.api('/skills/'+skill['id']+'/publish',{'edit_version':edited['edit_version']})
        self.assertNotEqual(old,new['active_version'])
        seen=[]
        def complete(messages,settings,budget):seen.append(str(messages));return self.completion(messages,settings,budget)
        value=self.execute(run,complete);self.assertEqual(value['state'],'succeeded');self.assertIn('按旧方法',str(seen));self.assertNotIn('按新方法',str(seen))
        rollback=self.api('/skills/'+skill['id']+'/activate',{'version_id':old});self.assertEqual(rollback['active_version'],old)

    def test_task_submission_is_idempotent_and_rejects_changed_request(self):
        body={'query':'财政赋税的依据','scope':{'edition_ids':[self.edition]},'request_key':str(uuid.uuid4())}
        with patch.object(worker,'start'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                values=list(pool.map(lambda _:self.client.post('/api/knowledge/research-runs',json=body),range(2)))
        self.assertTrue(all(v.status_code==200 for v in values),[v.text for v in values])
        self.assertEqual(values[0].json()['id'],values[1].json()['id'])
        self.api('/research-runs',{**body,'query':'另一问题'},expected=409)

    def test_disabled_skill_blocks_new_runs_and_keeps_pinned_run(self):
        skill=self.skill();run=self.queued(skill_version_ids=[skill['active_version']])
        self.api('/skills/'+skill['id']+'/state',{'enabled':False})
        self.assertEqual(self.execute(run,self.completion)['state'],'succeeded')
        self.api('/research-runs',{'query':'测试','scope':{'edition_ids':[self.edition]},'skill_version_ids':[skill['active_version']]},expected=409)

    def test_role_binding_and_tool_permission_are_enforced(self):
        skill=self.skill(tools=['read']);role=self.api('/agents',{'slug':'role-'+uuid.uuid4().hex[:10],'name':'受限角色','description':'测试','instructions':'核验材料','tools':['read'],'skill_version_ids':[skill['active_version']]})
        role=self.api('/agents/'+role['id']+'/publish',{'edit_version':role['edit_version']})
        run=self.queued(agent_version_id=role['active_version'])
        value=self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}]))
        self.assertTrue(any(s['action']=='search' and s['state']=='failed' for s in value['steps']));self.assertFalse(value['evidence'])
        other=self.skill()
        self.api('/research-runs',{'query':'测试','scope':{'edition_ids':[self.edition]},'agent_version_id':role['active_version'],'skill_version_ids':[other['active_version']]},expected=403)

    def test_draft_trial_does_not_activate_and_package_roundtrip(self):
        skill=self.skill(publish=False)
        run=self.api('/skills/'+skill['id']+'/tests',{'query':'财政赋税如何折银？','mode':'exact','scope':{'edition_ids':[self.edition]},'edit_version':skill['edit_version']})
        self.assertIsNone(self.api('/skills/'+skill['id'])['active_version'])
        self.execute(run,self.completion)
        trials=self.api('/skills/'+skill['id']+'/tests');self.assertEqual(trials[0]['state'],'succeeded')
        version=trials[0]['skill_version_id'];response=self.client.get('/api/knowledge/skills/'+skill['id']+'/versions/'+version+'/export')
        self.assertEqual(response.status_code,200);draft=registry.import_package(response.content);self.assertEqual(draft.instructions,skill['draft']['instructions'])

    def test_revoke_stops_inflight_skill_without_publication(self):
        skill=self.skill();run=self.queued(skill_version_ids=[skill['active_version']])
        def complete(messages,settings,budget):
            self.api('/skills/'+skill['id']+'/versions/'+skill['active_version']+'/revoke',{})
            self.assertTrue(settings['_cancel_event'].wait(3));raise RuntimeError('cancelled')
        with self.assertRaises(Cancelled):self.execute(run,complete)
        self.assertEqual(research_detail(self.root,run['id'])['answer'],'')

    def test_collaboration_parallel_evidence_review_and_synthesis(self):
        team=self.team();active=0;peak=0;lock=threading.Lock()
        def complete(*args):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:time.sleep(.08);return self.completion(*args)
            finally:
                with lock:active-=1
        value=self.drain(team['id'],complete)
        self.assertEqual(value['state'],'succeeded',value);self.assertEqual(value['outcome'],'complete');self.assertEqual(peak,2)
        self.assertEqual([n['state'] for n in value['nodes']],['succeeded']*4)
        self.assertTrue(value['evidence']);self.assertLessEqual(value['usage']['reserved_tokens'],180000)
        review=next(n for n in value['nodes'] if n['node_key']=='review')
        self.assertTrue(all(s['action'] not in ('search','read') for s in research_detail(self.root,review['research_run_id'])['steps']))

    def test_single_concurrency_and_expired_lease_are_respected(self):
        team=self.team(max_parallel=1);collaboration.advance(self.root,SETTINGS)
        runner=Worker();runner.root=self.root
        first=runner.claim(research_only=True);self.assertIsNotNone(first);self.assertIsNone(runner.claim(research_only=True))
        with r.database.transaction(self.root) as c:r.execute(c,"UPDATE kb_jobs SET lease_until=now()-interval '1 second' WHERE id=:id",id=first['id'])
        from backend.knowledge.research_assistant import gate
        with self.assertRaises(Cancelled):
            with r.database.transaction(self.root) as c:gate(c,{'run_id':first['payload']['run_id'],'_job_id':str(first['id']),'_job_owner':first['owner']})
        recovered=runner.claim(research_only=True)
        self.assertEqual(str(recovered['id']),str(first['id']));self.assertNotEqual(recovered['owner'],first['owner'])

    def test_scheduler_restart_keeps_completed_branch(self):
        team=self.team();collaboration.advance(self.root,SETTINGS)
        runner=Worker();runner.root=self.root;runner.research_model_settings=lambda:SETTINGS;runner.research_completion=self.completion
        job=runner.claim(research_only=True);runner.run(dict(job))
        run_id=job['payload']['run_id'];before=research_detail(self.root,run_id)
        value=self.drain(team['id']);after=research_detail(self.root,run_id)
        self.assertEqual(value['state'],'succeeded');self.assertEqual(before['usage'],after['usage']);self.assertEqual(len(before['steps']),len(after['steps']))

    def test_published_content_is_immutable_in_database(self):
        from sqlalchemy.exc import DBAPIError
        skill=self.skill()
        with self.assertRaises(DBAPIError):
            with r.database.transaction(self.root) as c:r.execute(c,"UPDATE kb_assistant_versions SET content='{}' WHERE id=:id",id=skill['active_version'])
        with r.database.transaction(self.root) as c:
            self.assertEqual(registry.release(c,skill['active_version'])['content']['instructions'],skill['draft']['instructions'])

    def test_missing_paper_is_partial_and_scope_change_stops_team(self):
        team=self.api('/collaborations',{'query':'财政赋税的依据','mode':'exact','scope':{'edition_ids':[self.edition]}})
        value=self.drain(team['id']);self.assertEqual(value['state'],'succeeded');self.assertEqual(value['outcome'],'partial');self.assertIn('论文资料缺失',str(value['warnings']))
        next_team=self.team()
        with r.database.transaction(self.root) as c:r.execute(c,'UPDATE kb_works SET version=version+1 WHERE id=(SELECT work_id FROM kb_editions WHERE id=:id)',id=self.edition)
        collaboration.advance(self.root,SETTINGS);self.assertEqual(collaboration.detail(self.root,next_team['id'])['state'],'failed')

    def test_empty_gathering_branch_cannot_claim_complete_comparison(self):
        team=self.team()
        def complete(messages,settings,budget):
            body=json.loads(messages[-1]['content'])
            if body['task'].startswith('final') and '角色：论文分析助手' in body.get('research_method',''):return {'text':'INSUFFICIENT_EVIDENCE'}
            return self.completion(messages,settings,budget)
        value=self.drain(team['id'],complete)
        self.assertEqual(value['state'],'succeeded');self.assertEqual(value['outcome'],'partial');self.assertIn('比较结果不完整',str(value['warnings']))

    def test_unused_disabled_role_does_not_block_single_corpus_workflow(self):
        agents=self.api('/agents');paper=next(a for a in agents if a['slug']=='paper-researcher')
        self.api('/agents/'+paper['id']+'/state',{'enabled':False})
        try:
            team=self.api('/collaborations',{'query':'财政赋税的依据','mode':'exact','scope':{'edition_ids':[self.edition]},'workflow':'review'})
            self.assertEqual(self.drain(team['id'])['state'],'succeeded')
        finally:self.api('/agents/'+paper['id']+'/state',{'enabled':True})

    def test_cancel_cascades_and_branch_retry_preserves_budget(self):
        team=self.team();collaboration.advance(self.root,SETTINGS)
        before=collaboration.detail(self.root,team['id']);self.assertEqual(sum(n['state']=='queued' for n in before['nodes']),2)
        cancelled=self.api('/collaborations/'+team['id']+'/cancel',{});self.assertEqual(cancelled['state'],'cancelled')
        for n in cancelled['nodes']:
            if n['research_run_id']:self.assertEqual(research_detail(self.root,n['research_run_id'])['state'],'cancelled')
        node=cancelled['nodes'][0];self.api('/collaborations/'+team['id']+'/nodes/'+node['id']+'/retry',{})
        # The untouched cancelled branch is a documented partial failure.
        value=self.drain(team['id']);self.assertIn(value['state'],('succeeded','failed'))
        self.assertLessEqual(value['usage']['reserved_tokens'],180000)

    def test_workspace_rebinding_includes_roles_skills_and_team(self):
        from backend.knowledge.backup import restore_workspace,workspace_key
        from backend.knowledge.config import register_managed,unregister_managed
        skill=self.skill();team=self.team();other=self.root.parent/'assistant-other';register_managed(other,self.product.manager.url)
        try:
            with self.assertRaises(HTTPException):registry.detail(other,skill['id'])
            with self.product.manager.connect() as c:restore_workspace(c,other,{'workspace_key':workspace_key(self.root)},native=True)
            self.assertEqual(str(registry.detail(other,skill['id'])['id']),skill['id'])
            self.assertEqual(str(collaboration.detail(other,team['id'])['id']),team['id'])
            with self.product.manager.connect() as c:restore_workspace(c,self.root,{'workspace_key':workspace_key(other)},native=True)
        finally:unregister_managed(other)


if __name__=='__main__':unittest.main()
