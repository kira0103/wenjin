import json,os,tempfile,unittest,uuid,threading,time
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.research_assistant import ResearchRequest,Action,create,process,detail,parse_action,prompt,PLANNER,model_identity
from backend.knowledge.worker import Worker,Cancelled,worker

SETTINGS={'base_url':'http://127.0.0.1:1/v1','model':'research-fixture','context_tokens':8192,'max_output_tokens':2000,'provider_name':'兼容接口'}

class ResearchRules(unittest.TestCase):
    def test_only_valid_readonly_actions_and_bounded_ranges(self):
        for value in [{'action':'shell','query':'erase'}, {'action':'search','query':' '},
                      {'action':'read'}, {'action':'read','edition_id':str(uuid.uuid4()),'page_to':5},
                      {'action':'finish','scope':{'edition_ids':[]}}]:
            with self.assertRaises((ValidationError,HTTPException)):parse_action(json.dumps(value))
        self.assertEqual(parse_action('```json\n{"action":"finish"}\n```').action,'finish')
        with self.assertRaises(ValidationError):ResearchRequest(query='问题',max_rounds=100)
        with self.assertRaises(ValidationError):ResearchRequest(query='问题',source_kinds=['note'])

    def test_context_budget_preserves_question_and_reports_exact_used_evidence(self):
        sources=[{'evidence_id':'E'+str(i),'quote':'古籍𠀀原文'*1000,'locator':{},'context_role':'primary'} for i in range(20)]
        messages,used,size,output=prompt(SETTINGS,PLANNER,'固定研究问题',sources,[],[],'plan')
        self.assertLessEqual(size+output+512,8192)
        self.assertIn('固定研究问题',messages[-1]['content']);self.assertLess(len(used),20)
        self.assertNotIn('api-key',str(model_identity({**SETTINGS,'api_key':'api-key'})))
        secret_quality=[{'evidence_id':'E1','quote':'原文','block_id':'b','locator':{'quality':{'lines':['不应发送的整页 OCR']}},'context_role':'primary'}]
        messages,*_=prompt(SETTINGS,PLANNER,'问题',secret_quality,[],[],'plan')
        self.assertNotIn('不应发送的整页 OCR',str(messages))


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class ResearchNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.knowledge.product_runtime import ProductDatabase
        from backend.knowledge.activation import CatalogActivation
        parent=Path('.local/research-assistant-20261014');parent.mkdir(parents=True,exist_ok=True)
        cls.folder=tempfile.TemporaryDirectory(prefix='native-',dir=parent.resolve());cls.root=Path(cls.folder.name)/'data'
        cls.product=ProductDatabase(cls.root,'internal');cls.product.start()
        if cls.product.startup_error:raise RuntimeError(cls.product.startup_error)
        service.initialize(cls.root);CatalogActivation(cls.root,service.connection).migrate()

    @classmethod
    def tearDownClass(cls):
        worker.stop();cls.product.stop();cls.folder.cleanup()

    def setUp(self):
        worker.stop();service.initialize(self.root);service.service.token='research-native'
        self.client=TestClient(service.app,headers={'X-App-Token':'research-native'})
        self.project='research-'+uuid.uuid4().hex
        with service.connection() as c:c.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(self.project,'研究测试',service.utc_now()))
        self.edition,self.asset,self.block=self.document('财政赋税𠀀原文，折银制度须对照版本。')

    def tearDown(self):self.client.close()

    def document(self,body):
        from backend.knowledge.catalog import CatalogService
        from backend.knowledge.ingestion import publish
        edition=CatalogService(self.root).create({'title':'研究原文'+uuid.uuid4().hex[:8],'item_type':'ancient'})
        h,key=r.store_file(self.root,'fixture.txt',(body+uuid.uuid4().hex).encode())
        with r.database.transaction(self.root) as c:
            asset=r.uid();r.execute(c,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes) VALUES(:id,:h,:k,0)',id=asset,h=h,k=key)
            r.execute(c,"INSERT INTO kb_edition_assets(edition_id,asset_id,filename) VALUES(:e,:a,'fixture.txt')",e=edition['id'],a=asset)
            rev=publish(c,edition['id'],[{'asset_id':asset,'page_index':n,'body':text} for n,text in [(1,'范围外秘密前文'),(2,body),(3,'范围外秘密后文')]])
            block=r.execute(c,'SELECT id FROM kb_blocks WHERE revision_id=:id AND page_index=2',id=rev['revision_id']).scalar()
        return str(edition['id']),asset,str(block)

    def queued(self,**kwargs):
        from backend.knowledge.project_sources import ProjectSourceService
        ProjectSourceService(self.root,service.connection).put(self.project,self.edition,{'mode':'ranges','roles':['reference'],
          'ranges':[{'asset_id':self.asset,'page_from':2,'page_to':2}]})
        payload=ResearchRequest(query='财政赋税如何折银？',mode='exact',scope={'project_id':self.project,'edition_ids':[self.edition]},**kwargs)
        with patch.object(worker,'start'),patch('backend.knowledge.research_api.create',wraps=create),patch('backend.app.read_model_settings',return_value=SETTINGS):
            # Router callbacks are bound at registration; configure the local test workspace.
            with service.connection() as c:
                for key,value in SETTINGS.items():
                    c.execute('INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value',(key,json.dumps(value) if key in ('context_tokens','max_output_tokens') else value))
            response=self.client.post('/api/knowledge/research-runs',json=payload.model_dump(mode='json'))
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def execute(self,run,completion):
        runner=Worker();runner.root=self.root
        with r.database.transaction(self.root) as c:
            owner=r.uid();r.execute(c,"UPDATE kb_jobs SET state='running',owner=:o,lease_until=now()+interval '45 seconds' WHERE id=:id",id=run['job_id'],o=owner)
        payload={'run_id':run['id'],'_job_id':run['job_id'],'_job_owner':owner}
        def check():
            with r.database.transaction(self.root) as c:job=r.one(c,'SELECT state,owner FROM kb_jobs WHERE id=:id',id=run['job_id'])
            if job['state']!='running' or job['owner']!=owner:raise Cancelled()
        process(self.root,payload,check,lambda *args:None,completion,SETTINGS)
        return detail(self.root,run['id'])

    def fake(self,actions,body='原文记载财政赋税与折银。[E1]'):
        queue=list(actions)
        def complete(messages,settings,budget):
            planning='每轮仅输出一个 JSON 对象' in messages[0]['content']
            value=queue.pop(0) if planning and queue else body
            return {'text':json.dumps(value) if isinstance(value,dict) else value,'usage':{'total_tokens':100}}
        return complete

    def test_continuous_search_read_and_followup_keep_project_unicode_evidence(self):
        run=self.queued()
        value=self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},
            {'action':'read','block_id':self.block},{'action':'search','query':'折银','mode':'exact'},{'action':'finish'}]))
        self.assertEqual(value['state'],'succeeded');self.assertTrue(value['verified'])
        self.assertEqual(value['usage']['calls'],5)
        self.assertEqual([s['action'] for s in value['steps'] if s['action'] in ('search','read')],['search','read','search'])
        self.assertTrue(all(e['locator']['page_index']==2 for e in value['evidence']))
        self.assertIn('𠀀',str(value['evidence']));self.assertNotIn('范围外秘密',str(value['evidence']))

    def test_cross_version_read_never_returns_outside_text(self):
        other,_,_=self.document('其他版本秘密')
        run=self.queued();value=self.execute(run,self.fake([{'action':'read','edition_id':other,'page_from':2,'page_to':2},{'action':'finish'}]))
        self.assertEqual(value['evidence'],[]);self.assertIn('未找到',value['answer'])
        self.assertTrue(any(s['state']=='failed' for s in value['steps']))

    def test_changed_project_scope_stops_before_generation(self):
        from backend.knowledge.project_sources import ProjectSourceService
        run=self.queued();calls=[]
        def complete(*args):
            calls.append(1)
            if len(calls)==2:
                with r.database.transaction(self.root) as c:r.execute(c,"UPDATE kb_project_sources SET state='invalid' WHERE project_id=:p",p=self.project)
                return {'text':'{"action":"finish"}'}
            return {'text':'{"action":"search","query":"财政赋税","mode":"exact"}'}
        with self.assertRaises(HTTPException):self.execute(run,complete)
        self.assertEqual(detail(self.root,run['id'])['answer'],'')

    def test_invalid_citations_never_publish_answer(self):
        run=self.queued()
        with self.assertRaises(HTTPException):self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}],body='虚构结论 [E999]'))
        self.assertFalse(detail(self.root,run['id'])['verified'])

    def test_irrelevant_retrieval_can_return_explicit_insufficient_evidence(self):
        run=self.queued();value=self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}],body='INSUFFICIENT_EVIDENCE'))
        self.assertEqual(value['state'],'succeeded');self.assertEqual(value['evidence'],[]);self.assertIn('证据不足',value['answer'])

    def test_revision_change_aborts_and_does_not_mix_sources(self):
        from backend.knowledge.ingestion import publish
        run=self.queued()
        def change(*args):
            with r.database.transaction(self.root) as c:publish(c,self.edition,[{'asset_id':self.asset,'page_index':2,'body':'替换的新版原文'}])
            return {'text':'{"action":"finish"}'}
        with self.assertRaises(HTTPException):self.execute(run,change)
        self.assertFalse(detail(self.root,run['id'])['verified'])

    def test_small_budget_and_round_limit_do_not_create_unbounded_calls(self):
        run=self.queued(max_rounds=2,token_budget=8000)
        value=self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}]))
        self.assertLessEqual(value['usage']['calls'],3);self.assertLessEqual(value['usage']['reserved_tokens'],8000)
        self.assertEqual(value['state'],'succeeded')

    def test_direct_quote_mismatch_is_rejected(self):
        run=self.queued()
        with self.assertRaises(HTTPException):self.execute(run,self.fake([{'action':'search','query':'财政赋税','mode':'exact'},{'action':'finish'}],body='原文说“这是一段虚构的古籍直接引文” [E1]'))
        self.assertFalse(detail(self.root,run['id'])['verified'])

    def test_repeated_actions_stop_and_still_create_bounded_draft(self):
        run=self.queued();action={'action':'search','query':'财政赋税','mode':'exact'}
        result=self.execute(run,self.fake([action,action]))
        self.assertEqual(result['usage']['calls'],3);self.assertIn('重复动作已停止',str(result['warnings']))

    def test_cancel_interrupts_inflight_request_without_publishing(self):
        run=self.queued();stopped=threading.Event()
        def complete(messages,settings,budget):
            self.client.post('/api/knowledge/research-runs/'+run['id']+'/cancel')
            self.assertTrue(settings['_cancel_event'].wait(3));stopped.set();raise RuntimeError('inflight cancelled')
        with self.assertRaises(Cancelled):self.execute(run,complete)
        result=detail(self.root,run['id']);self.assertEqual(result['state'],'cancelled');self.assertEqual(result['answer'],'');self.assertTrue(stopped.is_set())

    def test_old_owner_cannot_publish(self):
        run=self.queued()
        def complete(*args):
            with r.database.transaction(self.root) as c:r.execute(c,'UPDATE kb_jobs SET owner=:o WHERE id=:id',o=r.uid(),id=run['job_id'])
            return {'text':'{"action":"finish"}'}
        with self.assertRaises(Cancelled):self.execute(run,complete)
        self.assertFalse(detail(self.root,run['id'])['verified'])

    def test_resume_uses_saved_evidence_and_budget_after_interruption(self):
        run=self.queued();calls=[]
        def complete(*args):
            calls.append(1)
            if len(calls)==1:return {'text':'{"action":"search","query":"财政赋税","mode":"exact"}'}
            raise RuntimeError('temporary model interruption')
        with self.assertRaises(HTTPException):self.execute(run,complete)
        before=detail(self.root,run['id']);self.assertTrue(before['evidence'])
        result=self.execute(run,self.fake([{'action':'finish'}]))
        self.assertEqual(result['usage']['calls'],4);self.assertEqual(result['evidence'][0]['anchor_id'],before['evidence'][0]['anchor_id'])
        self.assertTrue(any(s['state']=='interrupted' for s in result['steps']))

    def test_worker_failure_hides_upstream_errors(self):
        run=self.queued();runner=Worker();runner.root=self.root;runner.research_model_settings=lambda:SETTINGS
        def failing(*args):raise RuntimeError('secret-upstream-password')
        runner.research_completion=failing
        with r.database.transaction(self.root) as c:r.execute(c,"UPDATE kb_jobs SET state='queued' WHERE id=:id",id=run['job_id'])
        job=runner.claim();runner.run(dict(job));result=detail(self.root,run['id'])
        self.assertEqual(result['state'],'failed');self.assertNotIn('secret-upstream-password',str(result))

    def test_workspace_isolation_and_backup_rebinding(self):
        from backend.knowledge.backup import restore_workspace,workspace_key
        from backend.knowledge.config import register_managed,unregister_managed
        run=self.queued();other=self.root.parent/'another-workspace';register_managed(other,self.product.manager.url)
        try:
            with self.assertRaises(HTTPException):detail(other,run['id'])
            with self.product.manager.connect() as c:restore_workspace(c,other,{'workspace_key':workspace_key(self.root)},native=True)
            self.assertEqual(str(detail(other,run['id'])['id']),run['id'])
            with self.product.manager.connect() as c:restore_workspace(c,self.root,{'workspace_key':workspace_key(other)},native=True)
        finally:unregister_managed(other)

if __name__=='__main__':unittest.main()
