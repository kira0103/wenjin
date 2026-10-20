// Real renderer, isolated PostgreSQL, local model fixture; no user library or cloud calls.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');const {stopServiceTree}=require('./gui_service_process.cjs');const http=require('node:http'),net=require('node:net');
const project=path.resolve(__dirname,'..'),output=path.join(project,'.local','assistant-collaboration-20261017');fs.mkdirSync(output,{recursive:true});
const root=path.join(output,'gui-'+crypto.randomUUID());fs.mkdirSync(root);app.setPath('userData',path.join(root,'electron'));
let service,win,base,token,model,stage='startup',activeCalls=0,peakCalls=0,customUsed=false,cancelStarted=0,cancelledCalls=0,exported=false;
const rendererErrors=[],delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label,seconds=80){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await delay(150);}throw Error('Timed out: '+label);}
async function api(route,method='GET',body){const res=await fetch(base+route,{method,headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await res.json();if(!res.ok)throw Error('API '+route+' '+res.status+' '+JSON.stringify(data));return data;}
async function js(code){return win.webContents.executeJavaScript(code);}
async function click(text){stage='click '+text;await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)});if(!b||b.disabled)throw Error('Button missing/disabled: '+${JSON.stringify(text)});b.click();})()`);await delay(80);}
async function card(text){stage='card '+text;await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.querySelector('strong')?.textContent===${JSON.stringify(text)});if(!b)throw Error('Card missing');b.click();})()`);await delay(150);}
async function field(label,value,tag='input'){stage='field '+label;await js(`(()=>{const el=document.querySelector(${JSON.stringify(tag+'[aria-label="'+label+'"]')});if(!el)throw Error('Field missing');const setter=Object.getOwnPropertyDescriptor(${tag==='select'?'HTMLSelectElement':tag==='textarea'?'HTMLTextAreaElement':'HTMLInputElement'}.prototype,'value').set;setter.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event(${JSON.stringify(tag==='select'?'change':'input')},{bubbles:true}));})()`);await delay(50);}
async function has(text){return js(`document.body.innerText.includes(${JSON.stringify(text)})`);}
async function stop(){if(win&&!win.isDestroyed())win.destroy();if(service&&service.exitCode===null){try{await api('/api/service/exit','POST');await until(()=>service.exitCode!==null,'shutdown',35);}catch{await stopServiceTree(service);await until(()=>service.exitCode!==null,'service tree exit',35);}}if(model){model.closeAllConnections();await new Promise(r=>model.close(r));}}
app.whenReady().then(async()=>{
  model=http.createServer((req,res)=>{let raw='';req.on('data',d=>raw+=d);req.on('end',()=>{try{
    const call=JSON.parse(raw),input=JSON.parse(call.messages[1].content);if(raw.includes('GUI-自定义证据方法'))customUsed=true;
    activeCalls++;peakCalls=Math.max(peakCalls,activeCalls);let closed=false;const finish=()=>{if(!closed){closed=true;activeCalls--;}};res.on('close',finish);
    if(input.question==='协作停止测试'){cancelStarted++;res.on('close',()=>cancelledCalls++);setTimeout(()=>{if(!res.destroyed)res.end('{}');},12000);return;}
    let text;if(input.task==='plan')text=JSON.stringify(input.recent_steps.some(s=>s.action==='search')?{action:'finish'}:{action:'search',query:'财政赋税',mode:'exact'});
    else text='财政赋税与折银制度须依据原文核对。[E1]\n\n当前材料支持初步比较，研究解释仍待人工审核。';
    setTimeout(()=>{if(!res.destroyed){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({choices:[{message:{content:text},finish_reason:'stop'}],usage:{total_tokens:100}}));}},180);
  }catch{res.writeHead(500);res.end('{}');}});});
  await new Promise(r=>model.listen(0,'127.0.0.1',r));
  const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});base=`http://127.0.0.1:${port}`;token=crypto.randomBytes(32).toString('hex');
  const env={...process.env,HF_HUB_OFFLINE:'1',PYTHON_KEYRING_BACKEND:'keyring.backends.null.Keyring',SHIXUE_KB_MODEL_CACHE:path.join(process.env.APPDATA,'shixue-workbench','models')};delete env.SHIXUE_KB_DATABASE_URL;
  const binary=process.env.SHIXUE_GUI_BINARY,args=['--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'];
  service=spawn(binary||path.join(project,'.venv','Scripts','python.exe'),binary?args:[path.join(project,'backend','app.py'),...args],{windowsHide:true,stdio:'ignore',env});
  await until(async()=>{if(service.exitCode!==null)throw Error('Service exited');try{return(await fetch(base+'/health')).ok;}catch{return false;}},'service');
  await api('/api/settings','PUT',{base_url:`http://127.0.0.1:${model.address().port}/v1`,model:'assistant-gui-fixture',provider_name:'隔离测试模型',context_tokens:16384,max_output_tokens:2000});
  const editions=[];
  for(const type of ['ancient','article']){
    const value=await api('/api/knowledge/catalog','POST',{title:type==='ancient'?'古籍证据界面样本':'论文证据界面样本',item_type:type,label:'独立测试'});editions.push(value);
    const response=await fetch(base+`/api/knowledge/catalog/editions/${value.id}/imports?filename=${type}.txt`,{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/octet-stream'},body:Buffer.from('财政赋税𠀀关系国家财政，折银制度须对照具体史料和研究语境。')});if(!response.ok)throw Error('Import failed');
  }
  await until(async()=>!(await api('/api/knowledge/jobs')).some(j=>['queued','running'].includes(j.state)),'index');
  ipcMain.handle('api:request',async(_event,req)=>{const res=await fetch(base+req.path,{method:req.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:req.body===undefined?undefined:JSON.stringify(req.body)});const contentType=res.headers.get('content-type')||'';return{status:res.status,contentType,body:contentType.includes('application/octet-stream')?await res.arrayBuffer():await res.json()};});
  ipcMain.handle('file:pick-pdf',()=>[]);ipcMain.handle('file:save',(_event,value)=>{if(value.filename.endsWith('.zip')){fs.writeFileSync(path.join(output,'gui-exported-skill.zip'),Buffer.from(value.data));exported=true;}return true;});ipcMain.handle('app:open-external',()=>{});
  win=new BrowserWindow({width:1550,height:1200,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(project,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  win.webContents.on('console-message',(event,...args)=>{const msg=event.message||args[1]||'';if(msg)rendererErrors.push(String(msg));});
  await win.loadFile(path.join(project,'dist','index.html'));await until(()=>has('文献库'),'navigation');await click('文献库');await until(()=>has('新建文献'),'catalog');await click('助手配置');await until(()=>has('新增技能'),'skill center');
  await click('新增技能');await field('配置名称','GUI 自定义技能');await field('配置标识','gui-custom-skill');await field('配置描述','界面验证用的原文研究方法','textarea');await field('配置方法','GUI-自定义证据方法：先核对原文，再列论断与证据，不补造解释。','textarea');await click('保存草稿');await until(()=>has('草稿已保存'),'draft');await click('校验技能');await until(()=>has('静态校验通过'),'validate');
  let skill=(await api('/api/knowledge/skills')).find(v=>v.slug==='gui-custom-skill');if(skill.active_version)throw Error('Draft was activated');
  await click('用文献测试已保存草稿');await until(()=>has('技能测试：'),'trial view');await field('研究问题','自定义技能样本测试','textarea');await field('研究指定版本',editions[0].id,'select');await click('开始研究');
  await until(async()=>{const runs=await api('/api/knowledge/research-runs');return runs[0]?.state==='succeeded';},'trial completed');await until(()=>has('保存为笔记草稿'),'trial result');
  const trial=(await api('/api/knowledge/skills/'+skill.id+'/tests'))[0];if(!trial||!customUsed||(await api('/api/knowledge/skills/'+skill.id)).active_version)throw Error('Skill trial contract failed');
  await click('助手配置');await until(()=>has('GUI 自定义技能'),'reopen skills');await card('GUI 自定义技能');await until(()=>has('发布版本'),'loaded draft');await click('发布版本');await until(async()=>!!(await api('/api/knowledge/skills/'+skill.id)).active_version,'publish');
  skill=await api('/api/knowledge/skills/'+skill.id);const oldVersion=skill.active_version;
  await field('配置方法','GUI-自定义证据方法第二版：逐项标出依据和资料不足。','textarea');await click('保存草稿');await until(()=>has('草稿已保存'),'save v2');await click('发布版本');await until(async()=>(await api('/api/knowledge/skills/'+skill.id)).active_version!==oldVersion,'publish v2');
  await field('配置历史版本',oldVersion,'select');await click('设为默认版本');await until(async()=>(await api('/api/knowledge/skills/'+skill.id)).active_version===oldVersion,'rollback');await click('导出技能包');await until(()=>exported,'export');
  fs.writeFileSync(path.join(output,'skill-center.png'),(await win.webContents.capturePage()).toPNG());
  await click('智能体管理');await until(()=>has('新增智能体'),'agents');await click('新增智能体');await field('配置名称','GUI 专业助手');await field('配置标识','gui-custom-agent');await field('配置描述','绑定自定义研究技能','textarea');await field('配置方法','依据指定材料完成古籍证据搜集。','textarea');
  await js(`(()=>{const label=[...document.querySelectorAll('.kb-binding')].find(v=>v.textContent.includes('GUI 自定义技能'));if(!label)throw Error('Binding absent');label.querySelector('input').click();})()`);await click('保存草稿');await until(()=>has('草稿已保存'),'save agent');await click('发布版本');
  let agent;await until(async()=>{agent=(await api('/api/knowledge/agents')).find(v=>v.slug==='gui-custom-agent');return !!agent?.active_version;},'agent publish');
  await click('研究助手');await click('协同研究');await field('协作研究问题','古籍与论文的财政依据比较','textarea');await field('协作资料范围','all','select');await js("document.querySelector('.kb-quality').open=true");await until(()=>js(`!!document.querySelector('select[aria-label="古籍证据助手配置"] option[value="${agent.active_version}"]')`),'role options');await field('古籍证据助手配置',agent.active_version,'select');await click('开始协同研究');
  let team;await until(async()=>{const list=await api('/api/knowledge/collaborations');if(!list[0])return false;team=await api('/api/knowledge/collaborations/'+list[0].id);if(team.state==='failed')throw Error('Team failed: '+team.error);return team.state==='succeeded';},'collaboration');
  if(team.outcome!=='complete'||team.nodes.some(n=>n.state!=='succeeded')||peakCalls!==2||team.usage.reserved_tokens>180000)throw Error('Collaboration validation failed');
  await until(()=>has('保存协作笔记草稿'),'team renderer');await js("document.querySelector('.kb-quality').open=false;document.querySelector('.kb-collaboration-graph').scrollIntoView({block:'center'})");fs.writeFileSync(path.join(output,'collaboration.png'),(await win.webContents.capturePage()).toPNG());
  await click('保存协作笔记草稿');await until(()=>has('保存笔记'),'note');await click('保存笔记');await until(()=>has('笔记已保存'),'note saved');
  await click('研究助手');await click('协同研究');await field('协作研究问题','协作停止测试','textarea');await field('协作资料范围','all','select');await click('开始协同研究');await until(()=>cancelStarted===2,'two pending calls');await click('停止协作');await until(()=>cancelledCalls===2,'inflight cancellation');
  const last=(await api('/api/knowledge/collaborations'))[0],cancelled=await api('/api/knowledge/collaborations/'+last.id);if(cancelled.state!=='cancelled'||cancelled.answer||cancelled.nodes.some(n=>['queued','running'].includes(n.state)))throw Error('Cascade cancellation failed');
  const report={root,version:(await api('/health')).version,schema:(await api('/api/knowledge/health')).schema_revision,custom_skill_used:customUsed,trial_did_not_activate:true,skill_version_rollback:true,skill_export:exported,custom_agent_bound:true,
    team_nodes:team.nodes.map(n=>({role:n.node_key,state:n.state})),team_outcome:team.outcome,team_model_calls:team.usage.calls,peak_model_concurrency:peakCalls,note_saved:true,cancelled_inflight_calls:cancelledCalls};
  fs.writeFileSync(path.join(output,binary?'packaged-gui-report.json':'source-gui-report.json'),JSON.stringify(report,null,2));await stop();app.exit(0);
}).catch(async error=>{fs.writeFileSync(path.join(output,'gui-error.txt'),stage+'\n'+String(error.stack)+'\n'+rendererErrors.join('\n'));if(win&&!win.isDestroyed())fs.writeFileSync(path.join(output,'gui-error.png'),(await win.webContents.capturePage()).toPNG());try{await stop();}catch{}app.exit(1);});
