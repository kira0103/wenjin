"""One-request collation experiments. No manuscript writes and no automatic retry."""
import json,sqlite3,threading,time
from pathlib import Path
from contextlib import closing
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
try:
    from backend import collation
    from backend.collation_prompt import Profile,CONTRACT,DEFAULT_SYSTEM,RECOMMENDED_SYSTEM,read_profile,request_options
    from backend.preprocess_engine import digest
except ModuleNotFoundError:
    import collation
    from collation_prompt import Profile,CONTRACT,DEFAULT_SYSTEM,RECOMMENDED_SYSTEM,read_profile,request_options
    from preprocess_engine import digest

def migrate(db,root,now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collation_configs'").fetchone():
        db.commit()
        if db.execute('SELECT 1 FROM materials LIMIT 1').fetchone():
            folder=Path(root)/'backups';folder.mkdir(exist_ok=True);backup=folder/'before-0.3.8.sqlite'
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as target: db.backup(target)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS collation_configs(project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,config_json TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS collation_debug_runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,fingerprint TEXT NOT NULL,config_json TEXT NOT NULL,model_json TEXT NOT NULL,
        request_json TEXT NOT NULL,input_text TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,reply_json TEXT NOT NULL DEFAULT '{}',
        validation_json TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT '',owner_pid INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS idx_collation_debug_project ON collation_debug_runs(project_id,created_at);
    ''')
    for row in db.execute("SELECT id,owner_pid FROM collation_debug_runs WHERE status='running'").fetchall():
        if not collation.alive(row['owner_pid']):
            db.execute("UPDATE collation_debug_runs SET status='interrupted',error='调试请求中断，响应及用量未知；请手动重新测试',finished_at=? WHERE id=?",(now(),row['id']))

class Experiment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    config: Profile
    input_text: str = Field(min_length=1,max_length=8000)
    title: str = Field(default='手动样本',max_length=200)
    lead: str = Field(default='',max_length=200)
    attempt_id: str | None = None
    validation_feedback: str = Field(default='',max_length=3000)

class RunInput(Experiment):
    request_id: str = Field(min_length=8,max_length=80)

class SaveInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    config: Profile
    expected_revision: int = Field(ge=0)
    debug_run_id: str

def project_row(db,project_id):
    row=db.execute('SELECT * FROM projects WHERE id=? AND deleted=0',(project_id,)).fetchone()
    if not row: raise HTTPException(404,'找不到研究项目')
    if row['archived'] or row['project_type']!='collation': raise HTTPException(400,'请在活动的古籍点校项目中调试')
    return row

def seed_row(db,project_id,attempt_id):
    row=db.execute('''SELECT a.*,c.input_text,c.lead,i.title,b.model_json,b.rules,b.project_id FROM collation_attempts a
      JOIN collation_chunks c ON c.id=a.chunk_id JOIN collation_items i ON i.id=a.item_id JOIN collation_batches b ON b.id=a.batch_id WHERE a.id=?''',(attempt_id,)).fetchone()
    if not row or row['project_id']!=project_id: raise HTTPException(404,'找不到当前项目的请求')
    return row

def preview(db,project_id,payload,model_settings):
    if not payload.input_text.strip(): raise HTTPException(400,'调试样本只有空白，请选择一段实际正文')
    project=project_row(db,project_id)
    model=model_settings()
    rules=project['rules']
    if payload.attempt_id:
        seed=seed_row(db,project_id,payload.attempt_id);model=json.loads(seed['model_json']);rules=seed['rules']
    config=payload.config.model_dump()
    model['collation_config']=config
    model['collation_contract']=CONTRACT
    model['collation_input_frame']='edge-whitespace-v1'
    model['collation_glyph_mode']='pua-placeholder-v1'
    messages=collation.messages_for({'title':payload.title},{'input_text':payload.input_text,'lead':payload.lead},rules,config,CONTRACT,model['collation_input_frame'],payload.validation_feedback,model['collation_glyph_mode'])
    request={'model':model['model'],'messages':messages,**request_options(model),**collation.output_options(config['max_tokens'])}
    # This is a conservative preflight bound, never an actual usage measurement.
    bound=3*sum(len(m['content']) for m in messages)+(config['max_tokens'] or 0)
    if model.get('context_tokens') and bound>model['context_tokens']:
        raise HTTPException(400,'样本与输出预算超过已填写模型容量，请缩短样本或降低输出上限')
    return model,request

def report(row):
    value=dict(row);value.pop('owner_pid',None);value.pop('fingerprint',None)
    for field in ('config','model','request','reply','validation'):
        value[field]=json.loads(value.pop(field+'_json'))
    reply=value['reply'];usage=reply.get('usage')
    a,b,c=collation.token_usage(usage)
    details=usage.get('completion_tokens_details') if isinstance(usage,dict) else None
    reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else None
    value['usage']={'input_tokens':a,'output_tokens':b,'total_tokens':c,'reasoning_tokens':reasoning if isinstance(reasoning,int) and not isinstance(reasoning,bool) and reasoning>=0 else None}
    # Also re-evaluate old debug successes; unchanged text is not a successful experiment.
    if value['validation'].get('text') is not None:
        value['validation']['quality']=collation.punctuation_review(value['input_text'],value['validation']['text'])
        if value['status']=='passed' and value['validation']['quality']['requires_review']:
            value['status']='no_change'
    value['passed']=value['status'] in ('passed','review')
    value['retry_feedback']=''
    if value['status']=='failed' and '原字校验' in value['error']:
        # Upgrade the explanation of historical failures without rewriting receipts.
        model=value['model']
        try:
            result=collation.validate_reply(value['input_text'],reply,input_frame=model.get('collation_input_frame'),glyph_mode=model.get('collation_glyph_mode'))
            quality=collation.punctuation_review(value['input_text'],result.text)
            value.update(status='review',passed=True,error='',original_error=value['error'],historical_fidelity_failure=True,
                         validation={'text':result.text,'questions':[q.model_dump() for q in result.questions],
                                     'characters_preserved':quality['characters_preserved'],'quality':quality})
            return value
        except Exception as exc:
            if getattr(exc,'fidelity_error',False): value['error']=str(exc)[:3000]
        wire=collation.model_input_text(value['input_text'],model.get('collation_input_frame'),model.get('collation_glyph_mode'))
        value['retry_feedback']=collation.fidelity_feedback(wire,value['error'])[:3000]
    return value

class Controller:
    def __init__(self,service,now,call,batch_runner):
        self.service,self.now,self.call,self.batch_runner=service,now,call,batch_runner
        self.lock=threading.Lock();self.workers={}
    def active(self,root):
        with self.lock: return any(r==Path(root) for r in self.workers.values())
    def start(self,root,run_id):
        if not self.service.embedded_workers:
            return
        with self.lock: self.workers[run_id]=Path(root)
        worker=threading.Thread(target=self.execute,args=(Path(root),run_id),daemon=True,name='collation-debug')
        self.service.tasks.add(worker);worker.start()
    def execute(self,root,run_id):
        started=time.monotonic();reply={};status='failed';error='';validation={}
        try:
            with collation.database(root) as db:
                import os
                db.execute("UPDATE collation_debug_runs SET status='running',owner_pid=? WHERE id=?", (os.getpid(),run_id))
                row=db.execute('SELECT * FROM collation_debug_runs WHERE id=?',(run_id,)).fetchone()
            model,request=json.loads(row['model_json']),json.loads(row['request_json'])
            try:
                reply=self.call(request['messages'],model,request.get('max_tokens'))
            except Exception as exc:
                reply=getattr(exc,'reply',None) or {}
                if not getattr(exc,'sent',True): reply={'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0},'finish_reason':'not_sent'}
                raise
            result=collation.validate_reply(row['input_text'],reply,input_frame=model.get('collation_input_frame'),glyph_mode=model.get('collation_glyph_mode'))
            quality=collation.punctuation_review(row['input_text'],result.text)
            validation={'text':result.text,'questions':[q.model_dump() for q in result.questions],'characters_preserved':quality['characters_preserved'],'quality':quality}
            status='review' if quality['text_changes'] else 'no_change' if quality['requires_review'] else 'passed'
        except Exception as exc:
            error=str(exc)[:3000]
        finally:
            reply['latency_ms']=round((time.monotonic()-started)*1000)
            try:
                with collation.database(root) as db:
                    db.execute('UPDATE collation_debug_runs SET status=?,reply_json=?,validation_json=?,error=?,finished_at=? WHERE id=?',
                               (status,json.dumps(reply,ensure_ascii=False),json.dumps(validation,ensure_ascii=False),error,self.now(),run_id))
            finally:
                with self.lock: self.workers.pop(run_id,None)
                self.service.tasks.discard(threading.current_thread())
                if root==Path(self.service.root): self.batch_runner.kick()

def register(app,connection,now,call,model_settings,public_settings,service,batch_runner):
    controller=Controller(service,now,call,batch_runner)
    service.debug_controller=controller
    @app.get('/api/projects/{project_id}/collation/debug/config')
    def config(project_id:str):
        with connection() as db:
            project_row(db,project_id)
            return {**read_profile(db,project_id),'model':model_settings(),'contract':CONTRACT,'recommended_prompt':RECOMMENDED_SYSTEM}
    @app.get('/api/projects/{project_id}/collation/debug/seed/{attempt_id}')
    def seed(project_id:str,attempt_id:str):
        with connection() as db:
            project_row(db,project_id);row=seed_row(db,project_id,attempt_id);model=json.loads(row['model_json'])
            config=model.get('collation_config') or Profile(system_prompt=DEFAULT_SYSTEM,max_tokens=collation.output_budget(model)).model_dump()
            original=collation.messages_for({'title':row['title']},{'input_text':row['input_text'],'lead':row['lead']},row['rules'],model.get('collation_config'),model.get('collation_contract'),model.get('collation_input_frame'),glyph_mode=model.get('collation_glyph_mode'))
            budget=collation.output_budget(model) if model.get('collation_config') else min(collation.output_budget(model),max(256,(int(model.get('context_tokens') or 16384)-2400-3*len(row['rules']))//2))
            request=json.loads(row['request_json']) if row['request_json'] else {'model':model['model'],'messages':original,**request_options(model),**collation.output_options(budget)}
            previous=json.loads(request['messages'][-1]['content']).get('previous_validation_feedback','')
            return {'attempt_id':attempt_id,'title':row['title'],'input_text':row['input_text'],'lead':row['lead'],'config':config,'model':model,
                    'original_request':request,'validation_feedback':previous,
                    'original_reply':json.loads(row['response_json'] or '{}'),'original_error':row['error'],'original_finish_reason':row['finish_reason'],'original_usage':json.loads(row['usage_json'] or 'null')}
    @app.post('/api/projects/{project_id}/collation/debug/preview')
    def request_preview(project_id:str,payload:Experiment):
        with connection() as db: model,request=preview(db,project_id,payload,model_settings)
        return {'request':request,'model':model,'sample_chars':len(payload.input_text),'model_configured':public_settings()['configured']}
    @app.post('/api/projects/{project_id}/collation/debug/runs')
    def create(project_id:str,payload:RunInput):
        root=Path(service.root);run_id=digest({'project':project_id,'request':payload.request_id})[:24]
        with connection() as db:
            db.execute('BEGIN IMMEDIATE');project_row(db,project_id)
            fingerprint=digest(payload.model_dump(exclude={'request_id'}))
            previous=db.execute('SELECT * FROM collation_debug_runs WHERE id=?',(run_id,)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint: raise HTTPException(409,'相同请求编号对应不同调试内容')
                return report(previous)
            if root in batch_runner.shutdown_roots: raise HTTPException(409,'软件正在退出，重新打开后调试')
            if not public_settings()['configured']: raise HTTPException(400,'请先在模型与设置中配置连接')
            if db.execute("SELECT 1 FROM collation_batches WHERE status IN ('queued','running','pausing','stopping') LIMIT 1").fetchone():
                raise HTTPException(409,'请先暂停活动批次，等待在途请求保存后再调试')
            if db.execute("SELECT 1 FROM collation_debug_runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise HTTPException(409,'已有调试请求执行中，请等待返回')
            model,request=preview(db,project_id,payload,model_settings)
            import os
            db.execute('''INSERT INTO collation_debug_runs(id,project_id,fingerprint,config_json,model_json,request_json,input_text,title,status,created_at,owner_pid)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(run_id,project_id,fingerprint,json.dumps(payload.config.model_dump(),ensure_ascii=False),json.dumps(model,ensure_ascii=False),json.dumps(request,ensure_ascii=False),payload.input_text,payload.title,'running' if service.embedded_workers else 'queued',now(),os.getpid()))
            value=report(db.execute('SELECT * FROM collation_debug_runs WHERE id=?',(run_id,)).fetchone())
        controller.start(root,run_id)
        return value
    @app.get('/api/projects/{project_id}/collation/debug/runs')
    def history(project_id:str):
        with connection() as db:
            project_row(db,project_id)
            return [{'id':r['id'],'title':r['title'],'status':report(r)['status'],'created_at':r['created_at']} for r in db.execute('SELECT * FROM collation_debug_runs WHERE project_id=? ORDER BY rowid DESC LIMIT 30',(project_id,))]
    @app.get('/api/projects/{project_id}/collation/debug/runs/{run_id}')
    def result(project_id:str,run_id:str):
        with connection() as db:
            project_row(db,project_id);row=db.execute('SELECT * FROM collation_debug_runs WHERE id=? AND project_id=?',(run_id,project_id)).fetchone()
            if not row: raise HTTPException(404,'找不到调试记录')
            return report(row)
    @app.put('/api/projects/{project_id}/collation/debug/config')
    def save(project_id:str,payload:SaveInput):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE');project_row(db,project_id);current=read_profile(db,project_id)
            if current['revision']!=payload.expected_revision: raise HTTPException(409,'方案已由另一页面修改，请刷新后再保存')
            run=db.execute('SELECT * FROM collation_debug_runs WHERE id=? AND project_id=?',(payload.debug_run_id,project_id)).fetchone()
            if not run or not report(run)['passed'] or json.loads(run['config_json'])!=payload.config.model_dump(): raise HTTPException(409,'请先用当前提示词与参数完成一次有实际新增标点且通过校验的调试；原样返回需人工核对')
            tested=json.loads(run['model_json']);model=model_settings()
            if (tested['base_url'],tested['model'])!=(model['base_url'],model['model']): raise HTTPException(409,'当前模型与测试模型不同，请改用当前模型调试后保存')
            collation.chunk_limit({**model,'collation_config':payload.config.model_dump()},project_row(db,project_id)['rules'])
            db.execute('INSERT INTO collation_configs(project_id,revision,config_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,config_json=excluded.config_json,updated_at=excluded.updated_at',
                       (project_id,current['revision']+1,json.dumps(payload.config.model_dump(),ensure_ascii=False),now()))
            return {**read_profile(db,project_id),'model':model,'contract':CONTRACT,'recommended_prompt':RECOMMENDED_SYSTEM}
    return controller
