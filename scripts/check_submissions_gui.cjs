// Real application UI and isolated native database. Never opens a user data directory.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),net=require('node:net');
const {stopServiceTree}=require('./gui_service_process.cjs');
const workspace=path.resolve(__dirname,'..'),output=path.join(workspace,'.local','submissions-20261018');
fs.mkdirSync(output,{recursive:true});
const root=path.join(output,'gui-'+crypto.randomUUID());fs.mkdirSync(root);
app.setPath('userData',path.join(root,'electron'));
app.on('window-all-closed',()=>{});
let win,service,base,token,stage='startup';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){stage=label;const end=Date.now()+90000;while(Date.now()<end){if(await check())return;await delay(100);}throw Error('Timed out: '+label);}
async function api(route,method='GET',body){const res=await fetch(base+route,{method,headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await res.json();if(!res.ok)throw Error(`${route}: ${res.status} ${JSON.stringify(value)}`);return value;}
async function js(code){return win.webContents.executeJavaScript(code);}
async function has(text){return js(`document.body.innerText.includes(${JSON.stringify(text)})`);}
async function click(text){stage='click '+text;await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)});if(!b||b.disabled)throw Error('Missing or disabled button '+${JSON.stringify(text)});b.click();})()`);await delay(100);}
async function field(label,value,kind='input'){stage='field '+label;await js(`(()=>{const e=document.querySelector(${JSON.stringify(kind+'[aria-label="'+label+'"]')});if(!e)throw Error('Missing field '+${JSON.stringify(label)});Object.getOwnPropertyDescriptor(${kind==='select'?'HTMLSelectElement':kind==='textarea'?'HTMLTextAreaElement':'HTMLInputElement'}.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(${JSON.stringify(kind==='select'?'change':'input')},{bubbles:true}));})()`);await delay(80);}
async function closeModal(){await js("document.querySelector('.modal-panel button[aria-label=\"关闭对话框\"]').click()");await delay(100);}
async function stop(){if(win&&!win.isDestroyed())win.destroy();if(service&&service.exitCode===null){try{await api('/api/service/exit','POST');await until(()=>service.exitCode!==null,'service exit');}catch{await stopServiceTree(service);await until(()=>service.exitCode!==null,'service tree exit');}}}
async function screenshot(name){fs.writeFileSync(path.join(output,(process.env.SHIXUE_GUI_BINARY?'packaged-':'source-')+name+'.png'),(await win.webContents.capturePage()).toPNG());}

app.whenReady().then(async()=>{
  const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const p=server.address().port;server.close(()=>resolve(p));});});
  base='http://127.0.0.1:'+port;token=crypto.randomBytes(32).toString('hex');
  const binary=process.env.SHIXUE_GUI_BINARY,env={...process.env,HF_HUB_OFFLINE:'1',PYTHON_KEYRING_BACKEND:'keyring.backends.null.Keyring'};delete env.SHIXUE_KB_DATABASE_URL;
  const args=['--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'];
  service=spawn(binary||path.join(workspace,'.venv','Scripts','python.exe'),binary?args:[path.join(workspace,'backend','app.py'),...args],{windowsHide:true,stdio:'ignore',env});
  await until(async()=>{if(service.exitCode!==null)throw Error('Service exited');try{return(await fetch(base+'/health')).ok;}catch{return false;}},'service ready');
  ipcMain.handle('connection:is-remote',()=>false);
  ipcMain.handle('connection:info',()=>({remote:false,url:base,connected:true}));
  ipcMain.handle('api:request',async(_event,req)=>{const res=await fetch(base+req.path,{method:req.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:req.body===undefined?undefined:JSON.stringify(req.body)});return{status:res.status,contentType:res.headers.get('content-type')||'',body:await res.json()};});
  ipcMain.handle('file:pick-documents',()=>[]);ipcMain.handle('file:save',()=>true);ipcMain.handle('app:open-external',()=>{});
  win=new BrowserWindow({width:1530,height:1150,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(workspace,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  await win.loadFile(path.join(workspace,'dist','index.html'));await until(()=>has('论文投稿管理'),'navigation');await click('论文投稿管理');await until(()=>has('期刊总数'),'submission page');
  let overview=await api('/api/submissions/overview');if(overview.journals.length!==935)throw Error('Real catalog not seeded');
  await click('期刊库');await field('搜索期刊','历史研究');await until(()=>has('编辑期刊'),'journal cards');await screenshot('journal-wall');await js("document.querySelector('button[aria-label=\"编辑期刊：历史研究\"]').click()");await delay(100);
  await field('期刊名称','历史研究（界面测试）');await field('学科门类','中国史');await field('期刊等级','自定等级');await field('官网','http://example.org/journal');await field('投稿地址','https://example.org/submit');await field('期刊备注','人工维护的审稿信息','textarea');await click('保存期刊');await until(()=>has('期刊信息已保存'),'edit journal saved');
  await win.reload();await until(()=>has('论文投稿管理'),'reload');await click('论文投稿管理');await click('期刊库');await field('搜索期刊','历史研究（界面测试）');await until(()=>has('人工维护的审稿信息'),'edited card survives reload');
  overview=await api('/api/submissions/overview');const journal=overview.journals.find(j=>j.name==='历史研究（界面测试）');if(!journal)throw Error('Edit missing');
  const csv='期刊名称,学科门类,期刊等级,官网,投稿地址\n历史研究,历史学,C刊,https://example.org/changed,\n新增测试期刊,历史学,C扩,https://example.org,https://example.org/new\n';
  await js(`(()=>{const f=new File([${JSON.stringify(csv)}],'增量期刊目录.csv',{type:'text/csv'});const d=new DataTransfer();d.items.add(f);const e=document.querySelector('input[type=file]');e.files=d.files;e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(()=>has('预览期刊目录'),'CSV preview');await click('确认导入');await until(()=>has('目录导入完成'),'CSV import');overview=await api('/api/submissions/overview');if(overview.journals.length!==936||overview.journals.find(j=>j.id===journal.id).notes!=='人工维护的审稿信息')throw Error('Import overwrote manual edit');
  await click('论文管理');await click('新建论文');await field('论文题目','地方社会与档案研究（界面验收）');await field('论文作者','测试作者');await field('论文阶段','ready','select');await field('目标期刊',journal.id,'select');await click('保存论文');await until(()=>has('论文已保存'),'paper save');await click('正式投稿');await click('确认记录');await until(()=>has('第 1 次投稿'),'first submission');
  await click('收到拒稿');await field('投稿进展说明','选题不符，保留原始审稿意见','textarea');await click('确认记录');await until(()=>has('改投新期刊'),'rejected state');await click('改投新期刊');const second=overview.journals.find(j=>j.name==='新增测试期刊');await field('目标期刊',second.id,'select');await click('确认记录');await until(()=>has('第 2 次投稿'),'resubmitted');
  await click('收到返修');await field('投稿进展说明','请补充地方档案','textarea');await click('确认记录');await until(()=>has('提交修订稿'),'revision requested');await click('提交修订稿');await click('确认记录');await until(async()=>{const data=await api('/api/submissions/overview');return data.papers[0].round===2;},'second revision round');await click('收到录用');await click('确认记录');await until(()=>has('确认发表'),'accepted');await click('确认发表');await click('确认记录');await until(async()=>{const data=await api('/api/submissions/overview');return data.papers[0].status==='published';},'published');await screenshot('paper-history');
  const final=await api('/api/submissions/overview'),paper=await api('/api/submissions/papers/'+final.papers[0].id);
  if(paper.attempts.length!==2||paper.attempts[0].status!=='rejected'||paper.attempts[1].round!==2||!paper.events.some(e=>e.note.includes('选题不符')))throw Error('History incomplete');
  // Existing project results can create a linked manuscript without duplicating its content.
  await closeModal();
  const imported=await fetch(base+'/api/materials/import?filename='+encodeURIComponent('研究样本.txt')+'&project_id=project-main',{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/octet-stream'},body:Buffer.from('地方档案研究材料。保存原文和研究成果。')});
  const material=await imported.json();if(!imported.ok)throw Error(JSON.stringify(material));
  await until(async()=>{const jobs=await api('/api/knowledge/jobs');const parses=jobs.filter(j=>j.kind==='import');if(parses.some(j=>j.state==='failed'))throw Error('Fixture document parsing failed');return parses.length>0&&parses.every(j=>j.state==='succeeded');},'fixture original published');
  const unit=await api('/api/projects/project-main/units','POST',{title:'成果关联验收',kind:'reading',material_id:material.id});
  await click('工作成果');await until(()=>has('成果关联验收'),'work result available');await click('创建投稿论文');await until(()=>has('新建论文'),'result to submission');await click('保存论文');await until(()=>has('论文已保存'),'linked paper saved');
  const linkedOverview=await api('/api/submissions/overview'),linked=linkedOverview.papers.find(p=>p.source_id===unit.id);if(!linked||linked.project_id!=='project-main'||linked.source_title!=='成果关联验收')throw Error('Result association missing');
  const report={version:(await api('/health')).version,seeded_journals:935,imported_journals:final.journals.length,editable_fields_persisted:true,manual_edits_preserved_on_import:true,incremental_csv_ui:true,submission_attempts:paper.attempts.length,revision_rounds:paper.attempts[1].round,final_status:paper.status,rejection_note_preserved:true,work_result_association:true,cloud_model_calls:0};
  fs.writeFileSync(path.join(output,binary?'packaged-gui-report.json':'source-gui-report.json'),JSON.stringify(report,null,2));for(const name of ['gui-error.txt','gui-error.png'])fs.rmSync(path.join(output,name),{force:true});await stop();app.exit(0);
}).catch(async error=>{fs.writeFileSync(path.join(output,'gui-error.txt'),stage+'\n'+String(error.stack));if(win&&!win.isDestroyed())fs.writeFileSync(path.join(output,'gui-error.png'),(await win.webContents.capturePage()).toPNG());try{await stop();}catch{}app.exit(1);});
