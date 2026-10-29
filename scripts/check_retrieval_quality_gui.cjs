// Real UI/API check with a new, workspace-owned native PostgreSQL library.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');
const fs=require('node:fs');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const net=require('node:net');
const project=path.resolve(__dirname,'..');
const rerankMode=process.env.SHIXUE_GUI_RERANK==='1';
const output=path.join(project,'.local',rerankMode?'rerank-context-20261013':'retrieval-quality-20261013');
const root=path.join(output,'gui-'+crypto.randomUUID());
fs.mkdirSync(root,{recursive:true});app.setPath('userData',path.join(root,'electron'));
let service,window,base,token;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label,seconds=40){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await delay(150);}throw Error('Timed out: '+label);}
async function api(route,method='GET',body){const response=await fetch(base+route,{method,headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error('API failed: '+route+' ('+response.status+')');return data;}
async function js(code){return window.webContents.executeJavaScript(code);}
async function click(text){await js(`(()=>{const b=Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)});if(!b)throw Error('Button missing');b.click();})()`);}
async function setField(label,value,tag='input'){await js(`(()=>{const el=document.querySelector(${JSON.stringify(tag+'[aria-label="'+label+'"]')});const setter=Object.getOwnPropertyDescriptor(${tag==='select'?'HTMLSelectElement':'HTMLInputElement'}.prototype,'value').set;setter.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event(${JSON.stringify(tag==='select'?'change':'input')},{bubbles:true}));})()`);}
async function has(text){return js(`document.body.innerText.includes(${JSON.stringify(text)})`);}
async function summary(text){await js(`Array.from(document.querySelectorAll('summary')).find(e=>e.textContent.trim()===${JSON.stringify(text)}).click()`);}
async function stop(){if(window&&!window.isDestroyed())window.destroy();if(service){try{await api('/api/service/exit','POST');await until(()=>service.exitCode!==null,'service exit',12);}catch{service.kill();}}}

app.whenReady().then(async()=>{
  token=crypto.randomBytes(32).toString('hex');
  const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
  base=`http://127.0.0.1:${port}`;
  const env={...process.env,HF_HUB_OFFLINE:'1',SHIXUE_KB_MODEL_CACHE:path.join(process.env.APPDATA,'shixue-workbench','models')};
  if(rerankMode)env.SHIXUE_KB_RERANK_CACHE=path.join(project,'.local','reranker-models');
  delete env.SHIXUE_KB_DATABASE_URL;delete env.SHIXUE_KB_RESTORE_URL;
  const binary=process.env.SHIXUE_GUI_BINARY;
  const args=['--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'];
  service=spawn(binary||path.join(project,'.venv','Scripts','python.exe'),binary?args:[path.join(project,'backend','app.py'),...args],{windowsHide:true,stdio:'ignore',env});
  await until(async()=>{if(service.exitCode!==null)throw Error('Service exited during startup');try{return(await fetch(base+'/health')).ok;}catch{return false;}},'native service',70);
  if(!(await api('/api/knowledge/health')).ready)throw Error('Native schema not ready');
  const edition=await api('/api/knowledge/catalog','POST',{title:'检索质量界面样本𠀀',item_type:'ancient',label:'独立测试甲本'});
  const sample=rerankMode?'前文说明：以下讨论的是国家财政收入。\n\n财政赋税是国家财政收入的重要来源，征收和折银制度影响财政运行。𠀀\n\n后文限定：这里只讨论所选版本中的记载。':'财政赋税𠀀制度原文。这里是固定修订的评测样本。'.repeat(16);
  const response=await fetch(base+`/api/knowledge/editions/${edition.id}/imports?filename=sample.txt`,{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/octet-stream'},body:Buffer.from(sample)});
  if(!response.ok)throw Error('Fixture import failed');
  let detail;await until(async()=>{detail=await api('/api/knowledge/editions/'+edition.id);return !!detail.active_revision;},'published source');
  await until(async()=>!(await api('/api/knowledge/jobs')).some(j=>['queued','running'].includes(j.state)),'initial indexing',60);
  ipcMain.handle('connection:is-remote',()=>false);
  ipcMain.handle('connection:info',()=>({remote:false,url:base,connected:true}));
  ipcMain.handle('api:request',async(event,request)=>{const response=await fetch(base+request.path,{method:request.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:request.body===undefined?undefined:JSON.stringify(request.body)});return{status:response.status,contentType:response.headers.get('content-type'),body:await response.json()};});
  ipcMain.handle('file:pick-documents',()=>[]);ipcMain.handle('file:save',()=>false);ipcMain.handle('app:open-external',()=>{});
  window=new BrowserWindow({width:1500,height:1150,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(project,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  const errors=[];window.webContents.on('console-message',(_event,level,message)=>{if(level>=3)errors.push(message);});
  await window.loadFile(path.join(project,'dist','index.html'));
  await until(()=>has('文献库'),'library navigation');await click('文献库');
  await until(()=>has('新建文献'),'library loaded');await click('检索与问答');
  if(rerankMode){await summary('重排模型与父子上下文');await until(()=>has('已就绪'),'local rerank available');await js(`document.querySelector('input[aria-label="启用本地重排"]').click()`);await click('保存检索配置');await until(()=>has('后续检索与新评测使用此配置'),'settings saved');}
  await setField('文献库查询','财政赋税');await setField('检索方式',rerankMode?'keyword':'exact','select');await click('检索');
  await until(()=>has('标记为此问题应命中的原文'),'search results');
  await summary('查看本次检索依据与处理情况');await summary('检索评测与人工标注');
  if(rerankMode){
    await until(()=>has('重排完成'),'actual reranking');
    const traces=await api('/api/knowledge/retrieval-runs');const traced=await api('/api/knowledge/retrieval-runs/'+traces[0].id);
    const stage=traced.diagnostics.stages.find(s=>s.name==='rerank');if(stage.outcome!=='completed'||!stage.model_key.startsWith('BAAI/'))throw Error('Real rerank not used');
    const reply=await api('/api/knowledge/ask','POST',{query:'财政赋税',mode:'keyword',scope:{edition_ids:[edition.id]}});
    if(reply.evidence.length<3||!reply.evidence.some(e=>e.context_role==='after'))throw Error('Parent context evidence missing');
    await summary('查看父级原文上下文');await js(`Array.from(document.querySelectorAll('summary')).find(e=>e.textContent.trim()==='查看父级原文上下文').closest('article').scrollIntoView({block:'start'})`);
    await delay(300);fs.writeFileSync(path.join(output,'parent-context.png'),(await window.webContents.capturePage()).toPNG());
  }
  await js(`Array.from(document.querySelectorAll('label')).find(e=>e.textContent.includes('标记为此问题应命中的原文')).querySelector('input').click()`);
  await click('新建评测集');await until(()=>js(`!!document.querySelector('select[aria-label="选择检索评测集"]').value`),'suite created');
  await until(()=>js(`!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='保存问题与金标准').disabled`),'label button ready');
  await click('保存问题与金标准');await until(()=>has('1 处原文标注'),'gold persisted');
  await click('运行评测并保存结果');await until(()=>has('评测完成'),'evaluation completed');
  if(!(await has('Recall@10 100.0%')))throw Error('Expected fixture recall not displayed');
  if(await has('[object Object]'))throw Error('Structured diagnostics rendered as object text');
  await js(`Array.from(document.querySelectorAll('summary')).find(e=>e.textContent.trim()==='检索评测与人工标注').closest('details').scrollIntoView({block:'start'})`);
  await delay(350);fs.writeFileSync(path.join(output,'quality-evaluation.png'),(await window.webContents.capturePage()).toPNG());
  await click('文献与版本');await click('阅读');await until(()=>has('检索分块预览与配置'),'reader loaded');
  await summary('检索分块预览与配置');await setField('分块配置类型','ancient','select');await setField('分块目标字符数','180');await click('预览分块');
  await until(()=>has('采用此配置并重建索引'),'chunk preview');
  await js(`Array.from(document.querySelectorAll('summary')).find(e=>e.textContent.trim()==='检索分块预览与配置').closest('details').scrollIntoView({block:'start'})`);
  await delay(350);fs.writeFileSync(path.join(output,'quality-chunks.png'),(await window.webContents.capturePage()).toPNG());
  await click('采用此配置并重建索引');await until(()=>has('已加入索引任务'),'reindex queued');
  await until(async()=>!(await api('/api/knowledge/jobs')).some(j=>['queued','running'].includes(j.state)),'configured indexing',60);
  const updated=await api('/api/knowledge/editions/'+edition.id);
  if(updated.active_revision!==detail.active_revision)throw Error('Reindex altered source revision');
  const jobs=await api('/api/knowledge/jobs');if(jobs.some(j=>j.state==='failed'))throw Error('Fixture jobs failed');
  const suites=await api('/api/knowledge/evaluation/suites');const evaluated=await api('/api/knowledge/evaluation/suites/'+suites[0].id);
  if(evaluated.runs[0].report.scope_leaks!==0)throw Error('Scope leak');
  if(errors.length)throw Error('Renderer reported errors');
  fs.writeFileSync(path.join(output,binary?'packaged-gui-result.json':'gui-result.json'),JSON.stringify({passed:true,binary:!!binary,schema:(await api('/api/knowledge/health')).schema_revision,rerank_verified:rerankMode,parent_evidence_verified:rerankMode,source_revision_preserved:true,fixture_recall:1,scope_leaks:0,renderer_errors:0},null,2));
  console.log('UI, native API, source spans, gold labels, evaluation and real local indexing passed');
  await stop();app.quit();
}).catch(async error=>{console.error(error.message);await stop();app.exit(1);});
