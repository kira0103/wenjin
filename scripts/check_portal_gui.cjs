const pageAudit=require('./portal_page_audit.cjs');
/* Actual Web login, admin actions and isolated user workbench in hidden Chromium windows. */
const { app, BrowserWindow }=require('electron');
if(process.env.SHIXUE_GUI_PROCESSING_ONLY==='1')app.disableHardwareAcceleration();
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const project=path.resolve(__dirname,'..'),output=path.join(project,'.local','portal-gui',crypto.randomUUID());
fs.mkdirSync(output,{recursive:true});app.setPath('userData',path.join(output,'browser'));
app.on('window-all-closed',()=>{});
const password=crypto.randomBytes(24).toString('hex');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const freePort=()=>new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
async function until(check,label,seconds=100){const end=Date.now()+seconds*1000;while(Date.now()<end){try{const value=await check();if(value)return value;}catch{}await delay(100);}throw Error('Timed out: '+label);}
let child,admin,user,stage='starting';const errors=[];
const log=fs.openSync(path.join(output,'fixture.log'),'w');
const js=(window,source)=>window.webContents.executeJavaScript(source);
async function click(window,text,selector='button'){stage='click '+text;await js(window,`(()=>{const button=[...document.querySelectorAll(${JSON.stringify(selector)})].find(item=>item.textContent.trim().startsWith(${JSON.stringify(text)}));if(!button||button.disabled)throw Error('Button unavailable');button.click();})()`);await delay(100);}
async function field(window,label,value){await js(window,`(()=>{const label=[...document.querySelectorAll('label')].find(item=>item.firstChild.textContent.trim()===${JSON.stringify(label)});if(!label)throw Error('Field unavailable');const input=label.querySelector('input,select');Object.getOwnPropertyDescriptor(input.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);await delay(60);}
function browser(partition){const window=new BrowserWindow({show:false,width:1480,height:1000,webPreferences:{partition,offscreen:true,backgroundThrottling:false,contextIsolation:true,nodeIntegration:false,sandbox:true}});window.setSize(1480,1000);window.webContents.session.webRequest.onCompleted(details=>{if(details.statusCode>=500)errors.push('HTTP '+details.statusCode+' '+new URL(details.url).pathname);});window.webContents.on('console-message',(_event,_level,message)=>{if(/Uncaught|ReferenceError|TypeError/.test(message))errors.push(message);});return window;}
async function screenshot(window,name){fs.writeFileSync(path.join(output,name+'.png'),(await window.webContents.capturePage()).toPNG());}
async function login(window,base,route,username){await window.loadURL(base+route);await until(()=>js(window,"!!document.querySelector('input[autocomplete=username]')"),'login form');await field(window,'用户名',username);await field(window,'密码',password);await click(window,route==='/admin/'?'进入管理后台':'进入研究工作区');}

app.whenReady().then(async()=>{
  const port=await freePort(),base=`http://127.0.0.1:${port}`;
  child=spawn(path.join(project,'.venv','Scripts','python.exe'),[path.join(project,'scripts','portal_fixture.py'),'--root',path.join(output,'data'),'--port',String(port)],{cwd:project,env:{...process.env,SHIXUE_GUI_PASSWORD:password},windowsHide:true,stdio:['ignore',log,log]});
  await until(async()=>{if(child.exitCode!==null)throw Error('Portal fixture exited');return(await fetch(base+'/health')).ok;},'portal startup');
  admin=browser('portal-admin-'+crypto.randomUUID());user=process.env.SHIXUE_GUI_PROCESSING_ONLY==='1'?null:browser('portal-user-'+crypto.randomUUID());
  await admin.loadURL(base+'/admin/');await until(()=>js(admin,"!!document.querySelector('input[autocomplete=username]')"),'admin login');
  await screenshot(admin,'login-desktop');
  admin.setSize(430,900);await delay(150);await screenshot(admin,'login-mobile');admin.setSize(1480,1000);
  await field(admin,'用户名','administrator');await field(admin,'密码',password);await click(admin,'进入管理后台');
  await until(()=>js(admin,"document.body.innerText.includes('运行概览')"),'admin dashboard');
  if(process.env.SHIXUE_GUI_PROCESSING_ONLY!=='1'){
  await click(admin,'用户管理','nav button');await click(admin,'建立用户');
  await field(admin,'用户名','reader.demo');await field(admin,'显示名称','地方史研究');await field(admin,'临时密码',password);await click(admin,'保存');
  await until(()=>js(admin,"document.body.innerText.includes('地方史研究')&&!document.querySelector('.portal-dialog')"),'create user');
  assert.equal(await js(admin,"fetch('/api/admin/users').then(value=>value.json()).then(items=>items.length)"),4);
  await screenshot(admin,'admin-users');
  await click(admin,'模型服务','nav button');await until(()=>js(admin,"!!document.querySelector('input[type=url]')"),'model settings');
  await field(admin,'服务名称','统一研究模型');await field(admin,'模型服务地址','https://models.example.invalid/v1');await field(admin,'模型名称','research-model-fixture');await click(admin,'保存模型配置');
  await until(()=>js(admin,"document.body.innerText.includes('模型配置已保存')"),'save model settings');await screenshot(admin,'admin-models');
  await click(admin,'期刊库管理','nav button');
  await until(()=>js(admin,"document.body.innerText.includes('中文C刊库')&&document.querySelectorAll('.catalog-table tbody tr').length>0"),'administrator public journals');
  assert.equal(await js(admin,"document.querySelectorAll('h1').length"),1);
  assert.equal(await js(admin,"document.querySelector('.catalog-panel').getBoundingClientRect().width>innerWidth*.65"),true);
  await screenshot(admin,'admin-journals-chinese');
  admin.setSize(430,900);await delay(250);await screenshot(admin,'admin-journals-mobile');
  assert.equal(await js(admin,"document.documentElement.scrollWidth<=innerWidth"),true);
  admin.setSize(1480,1000);await delay(150);
  admin.setSize(1920,1080);await delay(150);await screenshot(admin,'admin-journals-wide');
  admin.setSize(3299,1709);await delay(150);
  assert.equal(await js(admin,"document.querySelector('.catalog-panel').getBoundingClientRect().width>innerWidth*.85"),true);
  assert.equal(await js(admin,"document.documentElement.scrollWidth<=innerWidth"),true);
  await screenshot(admin,'admin-journals-ultrawide');
  admin.setSize(1480,1000);await delay(100);
  await js(admin,"document.querySelector('button[aria-label=\"切换深色外观\"]').click()");await delay(150);
  assert.equal(await js(admin,"document.documentElement.dataset.theme"),'dark');
  await screenshot(admin,'admin-journals-dark');
  await js(admin,"document.querySelector('button[aria-label=\"切换浅色外观\"]').click()");
  await click(admin,'编辑','.catalog-edit');await until(()=>js(admin,"!!document.querySelector('.modal-panel')"),'journal editor');
  await field(admin,'学科门类','界面验收学科');await click(admin,'保存期刊');
  await until(()=>js(admin,"!document.querySelector('.modal-panel')&&document.body.innerText.includes('界面验收学科')"),'save catalog metadata');
  await click(admin,'英文期刊库','.submission-tabs button');
  await until(()=>js(admin,"document.querySelectorAll('.ej-table tbody tr').length>0||document.body.innerText.includes('23,113')"),'administrator English journal dataset');
  await screenshot(admin,'admin-journals');
  }
  await click(admin,'系统文献库','nav button');
  await until(()=>js(admin,"!!document.querySelector('.kb-file-layout')"),'shared library administrator');
  await until(()=>js(admin,"!!document.querySelector('.kb-directory-empty')"),'empty library onboarding');
  assert.equal(await js(admin,"document.querySelectorAll('.kb-group-card').length"),0);
  assert.equal(await js(admin,"Math.abs(document.querySelector('.kb-resources-toolbar .kb-tabs').getBoundingClientRect().top-document.querySelector('.kb-resources-toolbar .action-row').getBoundingClientRect().top)<5"),true);
  await screenshot(admin,'admin-library-empty');
  assert.equal(await js(admin,"[...document.querySelectorAll('button')].some(button=>button.textContent==='导入文献')"),true);
  const edition=await js(admin,`(async()=>{
    const api=async(path,method='GET',body)=>{const response=await fetch('/api/admin/library/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error('Library API '+response.status);return response.json();};
    const bytes=new TextEncoder().encode('这是大家共享的测试文献。'),upload=await api('uploads','POST',{files:[{relative_path:'公共测试文献.txt',size:bytes.length}]});
    await fetch('/api/admin/library/uploads/'+upload.id+'/files/0?offset=0',{method:'PUT',body:bytes});
    const preview=await api('uploads/'+upload.id+'/preview','POST');
    return api('knowledge/document-imports/'+preview.id+'/start','POST',{category_name:'共享史料',item_type:'article'});
  })()`);
  await until(()=>js(admin,`fetch('/api/admin/library/knowledge/document-tree?group_id=${edition.group_id}').then(value=>value.json()).then(value=>value.files?.[0]?.active_revision)`),'public document parsed');
  await click(admin,'刷新','.kb-workbench button');
  await until(()=>js(admin,"document.querySelector('.kb-file-groups')?.textContent.includes('共享史料')"),'shared group visible');
  await screenshot(admin,'admin-shared-library');
  assert.equal(await js(admin,"document.querySelectorAll('.kb-group-card').length"),1);
  for(const [width,height,label] of [[430,900,'mobile'],[3299,1709,'ultrawide']]){
    admin.setSize(width,height);await delay(220);
    assert.equal(await js(admin,"document.documentElement.scrollWidth<=innerWidth"),true);
    if(width<760){
      assert.equal(await js(admin,"document.querySelector('.kb-file-content').getBoundingClientRect().width>innerWidth*.7"),true);
      assert.equal(await js(admin,"document.querySelector('.kb-file-content').getBoundingClientRect().top-document.querySelector('.kb-file-groups').getBoundingClientRect().bottom<30"),true);
    }
    await screenshot(admin,'admin-library-'+label);
  }
  admin.setSize(1480,1000);await delay(100);
  if(process.env.SHIXUE_GUI_LIBRARY_ONLY==='1'){
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({library_only:true,empty_state:true,shared_library:true,mobile_single_column:true,ultrawide_layout:true,renderer_errors:errors}));
    console.log(JSON.stringify({verified:true,output}));return;
  }
  if(process.env.SHIXUE_GUI_ADAPTIVE_ONLY==='1'||process.env.SHIXUE_GUI_PROCESSING_ONLY==='1'){
    await pageAudit.auditAdmin({admin,user,js,click,field,screenshot,until,delay,output,password,processingOnly:process.env.SHIXUE_GUI_PROCESSING_ONLY==='1'});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({...(process.env.SHIXUE_GUI_PROCESSING_ONLY==='1'?{processing_only:true,whole_library_processing:true,total_progress:true,document_progress:true}:{adaptive_only:true,upload_per_document_strategy:true,persistent_document_policy:true,semantic_parent_preview:true}),page_audit:pageAudit.results,renderer_errors:errors},null,2));
    console.log(JSON.stringify({verified:true,output}));return;
  }
  await click(admin,'共享史料','.kb-file-groups button');
  await until(()=>js(admin,"!!document.querySelector('.kb-file-name')"),'administrator file visible');
  await click(admin,'公共测试文献.txt','.kb-file-name');
  await until(()=>js(admin,"!!document.querySelector('.kb-document-heading')"),'administrator shared reader');
  await click(admin,'维护原文与版本');
  await until(()=>js(admin,"[...document.querySelectorAll('button')].some(button=>button.textContent==='保存为新修订')"),'public revision maintenance');
  assert.equal(await js(admin,"document.body.innerText.includes('项目资料范围')"),false);
  await screenshot(admin,'admin-public-revision');
  await login(user,base,'/app/','alice');
  await until(()=>js(user,"document.body.innerText.includes('我的研究')"),'private user workspace');
  assert.equal(await js(user,'typeof window.desktop'),'undefined');
  assert.equal(await js(user,"fetch('/api/admin/users').then(value=>value.status)"),403);
  await click(user,'我的研究','.project-switch');await click(user,'新建研究项目');
  await js(user,"(()=>{const input=document.querySelector('input[placeholder=\"例如：明清地方社会研究\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'明清地方社会研究');input.dispatchEvent(new Event('input',{bubbles:true}));})()");await delay(100);await click(user,'创建项目');
  await until(()=>js(user,"document.querySelector('.project-switch')?.textContent.includes('明清地方社会研究')"),'create private project');
  await screenshot(user,'user-workspace');
  const auditContext={admin,user,js,click,field,screenshot,until,delay,output,password};
  await pageAudit.auditEmpty(auditContext);
  user.setSize(430,900);await delay(250);
  assert.equal(await js(user,"document.querySelector('.main-column').getBoundingClientRect().width>=innerWidth-1"),true);
  await screenshot(user,'user-workspace-mobile');
  await js(user,"document.querySelector('button[aria-label=\"研究助手\"]').click()");await delay(250);
  assert.equal(await js(user,"document.querySelector('.project-assistant-panel').getBoundingClientRect().right<=innerWidth"),true);
  await screenshot(user,'user-assistant-mobile');
  await js(user,"document.querySelector('button[aria-label=\"收起研究助手\"]').click()");
  await js(user,"document.querySelector('button[aria-label=\"展开导航\"]').click()");await delay(250);
  await screenshot(user,'user-navigation-mobile');
  await click(user,'文献库','aside button');
  assert.equal(await js(user,"document.querySelector('.sidebar').classList.contains('is-open')"),false);
  user.setSize(1480,1000);await delay(200);

  await click(user,'文献库','aside button');
  await until(()=>js(user,"!!document.querySelector('.kb-file-layout')"),'shared library reader');
  await until(()=>js(user,"document.querySelector('.kb-file-groups')?.textContent.includes('共享史料')"),'shared catalog visible to user');
  assert.equal(await js(user,"[...document.querySelectorAll('button')].some(button=>button.textContent==='导入文献')"),false);
  assert.equal(await js(user,"fetch('/api/knowledge/catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(value=>value.status)"),422);
  await click(user,'共享史料','.kb-file-groups button');
  await until(()=>js(user,"!!document.querySelector('.kb-file-name')"),'shared file visible');
  await click(user,'公共测试文献.txt','.kb-file-name');
  await until(()=>js(user,"!!document.querySelector('.kb-document-heading')"),'read shared original');
  await click(user,'文献信息');
  assert.equal(await js(user,"[...document.querySelectorAll('.modal-actions button')].some(button=>button.textContent==='编辑文献信息')"),false);
  await screenshot(user,'audit-document-info-modal');
  await click(user,'关闭','.modal-actions button');
  await screenshot(user,'user-shared-document');
  await click(user,'统一检索','.kb-tabs button');await pageAudit.capture(auditContext,user,'library-search');
  await click(user,'资料目录','.kb-tabs button');
  await click(user,'我的文献库','button[role=tab]');
  await until(()=>js(user,"!!document.querySelector('.kb-file-layout')&&[...document.querySelectorAll('button')].some(button=>button.textContent==='导入文献')"),'personal library imports');
  const personal=await js(user,`fetch('/api/knowledge/catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'仅属于林若舟的文献',item_type:'article'})}).then(value=>value.json())`);
  assert.ok(personal.id);
  await click(user,'刷新','.kb-workbench button');
  await click(user,'未分类','.kb-file-groups button');
  await until(()=>js(user,"document.body.innerText.includes('仅属于林若舟的文献')"),'personal document visible');
  await screenshot(user,'user-personal-library');
  await click(user,'导入文献');await screenshot(user,'audit-personal-import-modal');await click(user,'取消','.modal-actions button');
  await pageAudit.auditPopulated(auditContext,edition.group_id);
  await pageAudit.auditResults(auditContext);
  await pageAudit.auditSettings(auditContext);
  await pageAudit.auditCollation(auditContext,edition.group_id);
  await click(user,'个人设置');
  await until(()=>js(user,"!!document.querySelector('.settings-hub-tabs')"),'personal settings');
  const tabs=await js(user,"[...document.querySelectorAll('.settings-hub-tabs button')].map(item=>item.textContent.trim())");
  assert.ok(!tabs.includes('助手与 Skill'));assert.ok(!tabs.includes('检索配置'));assert.ok(!tabs.includes('模型连接'));assert.ok(!tabs.includes('数据与备份'));
  await click(user,'账号与安全','.settings-hub-tabs button');await screenshot(user,'user-account');
  await user.loadURL(base+'/admin/');await until(()=>js(user,"document.body.innerText.includes('这里是管理员工作区')"),'admin route denied');
  await screenshot(user,'user-admin-denied');
  await click(admin,'运行概览','nav button');await click(admin,'刷新');
  await until(()=>js(admin,"document.querySelectorAll('.portal-stat strong')[1]?.textContent==='1'"),'real workspace statistics');
  await screenshot(admin,'admin-overview');
  await pageAudit.auditAdmin(auditContext);
  await click(admin,'运行概览','nav button');
  admin.setSize(430,900);await delay(150);await screenshot(admin,'admin-mobile');
  await js(admin,"document.querySelector('button[aria-label=\"打开管理导航\"]').click()");await delay(300);await screenshot(admin,'admin-mobile-navigation');
  await click(admin,'用户管理','nav button');
  assert.equal(await js(admin,"document.querySelector('.portal-admin-sidebar').classList.contains('is-open')"),false);await delay(250);
  assert.equal(await js(admin,"document.documentElement.scrollWidth<=innerWidth"),true);
  await screenshot(admin,'admin-mobile-users');
  const latency=await js(user,`(async()=>{const samples=[];for(let i=0;i<5;i++){const start=performance.now();await fetch('/api/projects');samples.push(Math.round(performance.now()-start));}return samples;})()`);
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({web_login:true,administrator_console:true,user_creation:true,model_configuration:true,private_user_workbench:true,shared_library:true,personal_library:true,public_journals:true,shared_original_reader:true,public_write_denied:true,user_admin_api_denied:true,user_admin_page_denied:true,user_settings_separated:true,responsive_layout:true,ultrawide_layout:true,single_page_heading:true,catalog_editing:true,dark_theme:true,mobile_navigation:true,mobile_user_workspace:true,mobile_assistant:true,page_audit:pageAudit.results,warm_api_ms:latency,renderer_errors:errors},null,2));
  console.log(JSON.stringify({verified:true,output}));
}).then(()=>cleanup(0)).catch(async error=>{console.error(stage+': '+error.message);for(const [name,window] of [['admin',admin],['user',user]])if(window&&!window.isDestroyed()){try{await Promise.race([screenshot(window,name+'-failure'),delay(2000)]);const body=await Promise.race([js(window,'document.body?.innerText||""'),delay(2000).then(()=>"capture timed out")]);fs.writeFileSync(path.join(output,name+'-failure.txt'),body);}catch{}}await cleanup(1);});

async function cleanup(code){
  for(const window of [admin,user])if(window&&!window.isDestroyed())window.destroy();
  if(child&&child.exitCode===null){fs.writeFileSync(path.join(output,'data','gui.stop'),'stop');await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(120000)]);if(child.exitCode===null){child.kill();code=1;}}
  fs.closeSync(log);app.exit(code);
}
