// Real Electron renderer and local PostgreSQL. Synthetic corpus, no model calls.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),net=require('node:net');
const {stopServiceTree}=require('./gui_service_process.cjs');
const workspace=path.resolve(__dirname,'..'),output=path.join(workspace,'.local','core-corpus-20261018');
fs.mkdirSync(output,{recursive:true});
const root=path.join(output,'gui-'+crypto.randomUUID());fs.mkdirSync(root);
app.setPath('userData',path.join(root,'electron'));
let win,service,base,token,stage='startup',saved=false;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){const end=Date.now()+90000;while(Date.now()<end){if(await check())return;await delay(120);}throw Error('Timed out: '+label);}
async function api(route,method='GET',body){const res=await fetch(base+route,{method,headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await res.json();if(!res.ok)throw Error(`${route}: ${res.status} ${JSON.stringify(value)}`);return value;}
async function js(code){return win.webContents.executeJavaScript(code);}
async function has(text){return js(`document.body.innerText.includes(${JSON.stringify(text)})`);}
async function click(text){stage='click '+text;await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)});if(!b||b.disabled)throw Error('Missing or disabled button '+${JSON.stringify(text)});b.click();})()`);await delay(100);}
async function field(label,value,kind='input'){stage='field '+label;await js(`(()=>{const e=document.querySelector(${JSON.stringify(kind+'[aria-label="'+label+'"]')});if(!e)throw Error('Missing field');Object.getOwnPropertyDescriptor(${kind==='select'?'HTMLSelectElement':'HTMLInputElement'}.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(${JSON.stringify(kind==='select'?'change':'input')},{bubbles:true}));})()`);await delay(60);}
async function stop(){if(win&&!win.isDestroyed())win.destroy();if(service&&service.exitCode===null){try{await api('/api/service/exit','POST');await until(()=>service.exitCode!==null,'service exit');}catch{await stopServiceTree(service);await until(()=>service.exitCode!==null,'service tree exit');}}}

app.whenReady().then(async()=>{
  const source=path.join(root,'source'),main=path.join(source,'儒藏','經部','論語.md'),part=path.join(source,'儒藏','經部','論語','卷001.md');
  fs.mkdirSync(path.dirname(part),{recursive:true});
  fs.writeFileSync(main,'---\ntitle:\n  zh-hant: 論語界面樣本\nauthors:\n  - name: 孔子\n    dynasty: 春秋\n---\n總目：第一卷');
  fs.writeFileSync(part,'---\ntitle: 論語第一卷\nsource: 隔離測試錄文\n---\n學而時習之，不亦說乎？𠀀\n學而時習之。\n學而時習之。');
  const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const p=server.address().port;server.close(()=>resolve(p));});});
  base='http://127.0.0.1:'+port;token=crypto.randomBytes(32).toString('hex');
  const binary=process.env.SHIXUE_GUI_BINARY,env={...process.env,HF_HUB_OFFLINE:'1',PYTHON_KEYRING_BACKEND:'keyring.backends.null.Keyring'};delete env.SHIXUE_KB_DATABASE_URL;
  const args=['--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'];
  service=spawn(binary||path.join(workspace,'.venv','Scripts','python.exe'),binary?args:[path.join(workspace,'backend','app.py'),...args],{windowsHide:true,stdio:'ignore',env});
  await until(async()=>{if(service.exitCode!==null)throw Error('Service exited');try{return(await fetch(base+'/health')).ok;}catch{return false;}},'service ready');
  ipcMain.handle('api:request',async(_event,req)=>{const res=await fetch(base+req.path,{method:req.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:req.body===undefined?undefined:JSON.stringify(req.body)});const contentType=res.headers.get('content-type')||'';return{status:res.status,contentType,body:contentType.includes('application/octet-stream')?await res.arrayBuffer():await res.json()};});
  ipcMain.handle('file:pick-pdf',()=>[]);ipcMain.handle('file:save',(_event,value)=>{saved=value.filename.endsWith('.md')&&Buffer.from(value.data).equals(fs.readFileSync(part));return true;});ipcMain.handle('app:open-external',()=>{});
  win=new BrowserWindow({width:1530,height:1150,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(workspace,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  await win.loadFile(path.join(workspace,'dist','index.html'));await until(()=>has('文献库'),'navigation');await click('文献库');await until(()=>has('核心古籍'),'core navigation');await click('核心古籍');await until(()=>has('导入或增量更新本地古籍库'),'core panel');
  await js("document.querySelector('.kb-core details.kb-quality').open=true");await field('本地古籍源目录',source);await click('导入核心古籍');
  await until(async()=>{const jobs=await api('/api/knowledge/jobs');const j=jobs.find(j=>j.kind==='core_import');if(j?.state==='failed')throw Error(j.error);return j?.state==='succeeded';},'import');
  await until(()=>has('論語界面樣本'),'book list');await click('搜索书目');await field('核心古籍书目检索','论语');await click('搜索书目');await until(()=>has('論語界面樣本'),'simplified title search');
  await js("document.querySelector('.kb-core details.kb-quality').open=false");await click('原文检索');await field('古籍原文检索内容','學而時習之');await field('古籍检索每页数量','10','select');await click('检索原文');await until(()=>has('本页 3 处命中'),'literal matches');
  fs.writeFileSync(path.join(output,binary?'packaged-search.png':'source-search.png'),(await win.webContents.capturePage()).toPNG());
  await click('摘录命中字句');await until(()=>has('原文摘录已加入证据篮'),'evidence');await click('阅读原文');await until(()=>has('加入项目研究范围'),'reader');
  await until(()=>has('不亦說乎'),'original text');await js("[...document.querySelectorAll('summary')].find(s=>s.textContent==='来源与原件').parentElement.open=true");await click('导出此卷原始 Markdown');if(!saved)throw Error('Original Markdown export differs');
  await click('加入项目研究范围');await until(()=>has('已关联项目'),'project link');
  const bootstrap=await api('/api/bootstrap?project_id=project-main');if(!bootstrap.materials.length)throw Error('Project source absent');
  await click('← 返回检索结果');await field('古籍原文检索内容','学而时习之');await field('古籍检索模式','exact','select');await click('检索原文');await until(()=>has('本页 3 处命中'),'normalized phrase');
  await click('用于问答');await until(()=>has('检索与问答'),'RAG handoff');
  const report={version:(await api('/health')).version,schema:(await api('/api/knowledge/health')).schema_revision,corpus:(await api('/api/knowledge/core/status')).items[0].statistics,book_grouping:true,literal_occurrences:3,normalized_search:true,source_export_identical:saved,evidence_saved:true,project_link:true,rag_handoff:true,cloud_model_calls:0};
  fs.writeFileSync(path.join(output,binary?'packaged-gui-report.json':'source-gui-report.json'),JSON.stringify(report,null,2));await stop();app.exit(0);
}).catch(async error=>{fs.writeFileSync(path.join(output,'gui-error.txt'),stage+'\n'+String(error.stack));if(win&&!win.isDestroyed())fs.writeFileSync(path.join(output,'gui-error.png'),(await win.webContents.capturePage()).toPNG());try{await stop();}catch{}app.exit(1);});
