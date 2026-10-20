// Real renderer + isolated service + local compatible model fixture.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');const http=require('node:http'),net=require('node:net');
const project=path.resolve(__dirname,'..'),output=path.join(project,'.local','research-assistant-20261014');
const root=path.join(output,'gui-'+crypto.randomUUID());fs.mkdirSync(root,{recursive:true});app.setPath('userData',path.join(root,'electron'));
let service,win,base,token,model,currentAction='startup';const counts=new Map();let cancelledModel=false,cancelStarted=false;const rendererErrors=[];
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label,seconds=70){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await delay(200);}throw Error('Timed out: '+label);}
async function api(route,method='GET',body){const response=await fetch(base+route,{method,headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error('API '+route+' '+response.status);return data;}
async function js(code){return win.webContents.executeJavaScript(code);}
async function click(text){currentAction='click '+text;await js(`(()=>{const b=Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)});if(!b)throw Error('Button missing: '+${JSON.stringify(text)});b.click();})()`);}
async function field(label,value,tag='input'){currentAction='field '+label;await js(`(()=>{const el=document.querySelector(${JSON.stringify(tag+'[aria-label="'+label+'"]')});if(!el)throw Error('Field missing');const setter=Object.getOwnPropertyDescriptor(${tag==='select'?'HTMLSelectElement':tag==='textarea'?'HTMLTextAreaElement':'HTMLInputElement'}.prototype,'value').set;setter.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event(${JSON.stringify(tag==='select'?'change':'input')},{bubbles:true}));})()`);}
async function has(text){return js(`document.body.innerText.includes(${JSON.stringify(text)})`);}
async function stop(){if(win&&!win.isDestroyed())win.destroy();if(service&&service.exitCode===null){await api('/api/service/exit','POST');await until(()=>service.exitCode!==null,'service shutdown',30);}if(model)await new Promise(r=>model.close(r));}
app.whenReady().then(async()=>{
  model=http.createServer((req,res)=>{let raw='';req.on('data',d=>raw+=d);req.on('end',()=>{try{
    const call=JSON.parse(raw),input=JSON.parse(call.messages[1].content),question=input.question;
    if(question==='停止研究测试'){cancelStarted=true;res.on('close',()=>cancelledModel=true);setTimeout(()=>{if(!res.destroyed)res.end(JSON.stringify({choices:[{message:{content:'{"action":"finish"}'}}]}));},15000);return;}
    let text;if(input.task==='plan'){
      const n=(counts.get(question)||0)+1;counts.set(question,n);
      const action=n===1?{action:'search',query:'财政赋税',mode:'exact'}:n===2?{action:'read',block_id:input.evidence.find(e=>e.context_role==='primary').block_id}:n===3?{action:'search',query:'折银',mode:'exact'}:{action:'finish'};
      text=JSON.stringify(action);
    }else text='原文记载财政赋税，并提及折银制度。[E1]\n\n这是依据所选版本形成的研究草稿，尚需对照原文审核。';
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({choices:[{message:{content:text},finish_reason:'stop'}],usage:{prompt_tokens:120,completion_tokens:80,total_tokens:200}}));
  }catch{res.writeHead(500);res.end('{}');}});});
  await new Promise(r=>model.listen(0,'127.0.0.1',r));const modelPort=model.address().port;
  const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});base=`http://127.0.0.1:${port}`;token=crypto.randomBytes(32).toString('hex');
  const env={...process.env,HF_HUB_OFFLINE:'1',PYTHON_KEYRING_BACKEND:'keyring.backends.null.Keyring',SHIXUE_KB_MODEL_CACHE:path.join(process.env.APPDATA,'shixue-workbench','models')};delete env.SHIXUE_KB_DATABASE_URL;
  const binary=process.env.SHIXUE_GUI_BINARY,args=['--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'];
  service=spawn(binary||path.join(project,'.venv','Scripts','python.exe'),binary?args:[path.join(project,'backend','app.py'),...args],{windowsHide:true,stdio:'ignore',env});
  await until(async()=>{if(service.exitCode!==null)throw Error('Service exited');try{return(await fetch(base+'/health')).ok;}catch{return false;}},'service');
  await api('/api/settings','PUT',{base_url:`http://127.0.0.1:${modelPort}/v1`,model:'local-research-fixture',provider_name:'独立测试模型',context_tokens:8192,max_output_tokens:2000});
  const edition=await api('/api/knowledge/catalog','POST',{title:'研究助手界面样本𠀀',item_type:'ancient',label:'独立测试甲本'});
  const body='前文说明：以下讨论财政收入。\n\n财政赋税𠀀是国家财政收入的来源，折银制度影响征收方式。\n\n后文限定：本段仅描述所选版本的记载。';
  const response=await fetch(base+`/api/knowledge/editions/${edition.id}/imports?filename=research.txt`,{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/octet-stream'},body:Buffer.from(body)});if(!response.ok)throw Error('Import failed');
  await until(async()=>!(await api('/api/knowledge/jobs')).some(j=>['queued','running'].includes(j.state)),'index');
  ipcMain.handle('api:request',async(_event,req)=>{const res=await fetch(base+req.path,{method:req.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:req.body===undefined?undefined:JSON.stringify(req.body)});return{status:res.status,contentType:res.headers.get('content-type'),body:await res.json()};});
  ipcMain.handle('file:pick-pdf',()=>[]);ipcMain.handle('file:save',()=>false);ipcMain.handle('app:open-external',()=>{});
  win=new BrowserWindow({width:1500,height:1150,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(project,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  win.webContents.on('console-message',(event,...args)=>{const message=event.message||args[1]||'';if(message)rendererErrors.push(String(message));});
  await win.loadFile(path.join(project,'dist','index.html'));await until(()=>has('文献库'),'navigation');await click('文献库');await until(()=>has('新建文献'),'catalog');await click('研究助手');
  await field('研究问题','财政赋税研究测试','textarea');await field('研究指定版本',edition.id,'select');await click('开始研究');
  await until(()=>has('研究草稿'),'research answer');await until(()=>has('保存为笔记草稿'),'verified answer');
  const runs=await api('/api/knowledge/research-runs'),first=await api('/api/knowledge/research-runs/'+runs[0].id);
  if(first.state!=='succeeded'||first.usage.calls!==5||!first.steps.some(s=>s.action==='read'))throw Error('Research loop did not complete');
  await click('文献与版本');await click('研究助手');await until(()=>has('保存为笔记草稿'),'persisted answer');
  await click('保存为笔记草稿');await until(()=>has('保存笔记'),'note draft');await click('保存笔记');await until(()=>has('笔记已保存'),'note saved');
  const notes=await api('/api/knowledge/notes');if(!notes.some(n=>n.title==='财政赋税研究测试'))throw Error('Research note missing');
  await click('研究助手');await field('研究问题','停止研究测试','textarea');await click('开始研究');await until(()=>has('停止研究'),'running control');await until(()=>cancelStarted,'model request started');await click('停止研究');await until(()=>has('已停止'),'cancelled');
  await until(()=>cancelledModel,'inflight connection cancellation');
  const latest=(await api('/api/knowledge/research-runs'))[0],cancelled=await api('/api/knowledge/research-runs/'+latest.id);if(cancelled.state!=='cancelled'||cancelled.answer)throw Error('Cancelled answer published');
  await field('选择研究记录',first.id,'select');await until(()=>has('保存为笔记草稿'),'history selection');
  const picture=await win.webContents.capturePage();fs.writeFileSync(path.join(output,binary?'packaged-ui.png':'source-ui.png'),picture.toPNG());
  fs.writeFileSync(path.join(output,binary?'packaged-report.json':'source-report.json'),JSON.stringify({root,version:(await api('/health')).version,model_calls:first.usage.calls,steps:first.steps.map(s=>s.action),evidence_count:first.evidence.length,note_saved:true,cancelled_inflight:true},null,2));
  await stop();app.exit(0);
}).catch(async error=>{fs.writeFileSync(path.join(output,'gui-error.txt'),currentAction+'\n'+String(error.stack)+'\n'+rendererErrors.join('\n'));try{await stop();}catch{}app.exit(1);});
