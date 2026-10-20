import os,json,uuid,unittest
from unittest.mock import patch
from tests.test_research_assistant import ResearchNative as _Fixture,SETTINGS
from backend import app as service
from backend.knowledge import repository as r
from backend.knowledge.worker import worker
from backend.knowledge.project_sources import ProjectSourceService

@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要隔离 PostgreSQL')
class ProjectAssistantNative(unittest.TestCase):
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    document=_Fixture.document
    _fixture_setup=_Fixture.setUp
    def setUp(self):
        self._fixture_setup()
        self.other='other-'+uuid.uuid4().hex
        with service.connection() as db:
            db.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)',(self.other,'另一个项目',service.utc_now()))
            for key,value in SETTINGS.items():db.execute('INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value',(key,json.dumps(value) if key in ('context_tokens','max_output_tokens') else value))
        ProjectSourceService(self.root,service.connection).put(self.project,self.edition,{'mode':'edition','roles':['reference'],'ranges':[]})
    def tearDown(self):self.client.close()
    def start(self,context=None,scope_project=None,team=False,key=None):
        payload={'query':'财政赋税','scope':{'include_core':True,'edition_ids':[self.edition],'project_id':scope_project},'request_key':key or str(uuid.uuid4()),'source_kinds':['document']}
        if context:payload['context_project_id']=context
        if team:payload.update(workflow='review',token_budget=180000)
        with patch.object(worker,'start'):response=self.client.post('/api/knowledge/'+('collaborations' if team else 'research-runs'),json=payload)
        self.assertEqual(response.status_code,200,response.text);return response.json(),payload
    def test_project_history_is_independent_of_source_scope_and_keeps_legacy_runs(self):
        a,_=self.start(self.project);b,_=self.start(self.other);old,_=self.start(scope_project=self.project)
        own=self.client.get('/api/knowledge/research-runs',params={'project_id':self.project}).json()
        self.assertIn(a['id'],[v['id'] for v in own]);self.assertIn(old['id'],[v['id'] for v in own]);self.assertNotIn(b['id'],[v['id'] for v in own])
        self.assertIn(b['id'],[v['id'] for v in self.client.get('/api/knowledge/research-runs').json()])
    def test_collaboration_history_belongs_to_its_project(self):
        a,_=self.start(self.project,team=True);b,_=self.start(self.other,team=True)
        own=self.client.get('/api/knowledge/collaborations',params={'project_id':self.project}).json()
        self.assertIn(a['id'],[v['id'] for v in own]);self.assertNotIn(b['id'],[v['id'] for v in own])
    def test_unknown_project_cannot_enqueue_research(self):
        with r.database.transaction(self.root) as db:before=r.execute(db,'SELECT count(*) FROM kb_jobs').scalar_one()
        for route in ('research-runs','collaborations'):
            with patch.object(worker,'start'):response=self.client.post('/api/knowledge/'+route,json={'query':'财政赋税','context_project_id':'missing-project','scope':{'include_core':True},'source_kinds':['document']})
            self.assertEqual(response.status_code,404,response.text)
        with r.database.transaction(self.root) as db:self.assertEqual(before,r.execute(db,'SELECT count(*) FROM kb_jobs').scalar_one())
    def test_old_request_remains_idempotent_after_optional_context_added(self):
        value,payload=self.start()
        with r.database.transaction(self.root) as db:r.execute(db,"UPDATE kb_research_runs SET request=request-'context_project_id' WHERE id=:id",id=value['id'])
        with patch.object(worker,'start'):response=self.client.post('/api/knowledge/research-runs',json=payload)
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['id'],value['id'])

del _Fixture
