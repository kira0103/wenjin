/* Actual Web login, admin actions and isolated user workbench in hidden Chromium windows. */
const { app, BrowserWindow }=require('electron');
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
function browser(partition){const window=new BrowserWindow({show:false,width:1480,height:1000,webPreferences:{partition,offscreen:true,backgroundThrottling:false,contextIsolation:true,nodeIntegration:false,sandbox:true}});window.setSize(1480,1000);window.webContents.on('console-message',(_event,_level,message)=>{if(/Uncaught|ReferenceError|TypeError/.test(message))errors.push(message);});return window;}
async function screenshot(window,name){fs.writeFileSync(path.join(output,name+'.png'),(await window.webContents.capturePage()).toPNG());}
async function login(window,base,route,username){await window.loadURL(base+route);await until(()=>js(window,"!!document.querySelector('input[autocomplete=username]')"),'login form');await field(window,'用户名',username);await field(window,'密码',password);await click(window,route==='/admin/'?'进入管理后台':'进入研究工作区');}

app.whenReady().then(async()=>{
  const port=await freePort(),base=`http://127.0.0.1:${port}`;
  child=spawn(path.join(project,'.venv','Scripts','python.exe'),[path.join(project,'scripts','portal_fixture.py'),'--root',path.join(output,'data'),'--port',String(port)],{cwd:project,env:{...process.env,SHIXUE_GUI_PASSWORD:password},windowsHide:true,stdio:['ignore',log,log]});
  await until(async()=>{if(child.exitCode!==null)throw Error('Portal fixture exited');return(await fetch(base+'/health')).ok;},'portal startup');
  admin=browser('portal-admin-'+crypto.randomUUID());user=browser('portal-user-'+crypto.randomUUID());
  await admin.loadURL(base+'/admin/');await until(()=>js(admin,"!!document.querySelector('input[autocomplete=username]')"),'admin login');
  await screenshot(admin,'login-desktop');
  admin.setSize(430,900);await delay(150);await screenshot(admin,'login-mobile');admin.setSize(1480,1000);
  await field(admin,'用户名','administrator');await field(admin,'密码',password);await click(admin,'进入管理后台');
  await until(()=>js(admin,"document.body.innerText.includes('运行概览')"),'admin dashboard');
  await click(admin,'用户管理','nav button');await click(admin,'建立用户');
  await field(admin,'用户名','reader.demo');await field(admin,'显示名称','地方史研究');await field(admin,'临时密码',password);await click(admin,'保存');
  await until(()=>js(admin,"document.body.innerText.includes('地方史研究')&&!document.querySelector('.portal-dialog')"),'create user');
  assert.equal(await js(admin,"fetch('/api/admin/users').then(value=>value.json()).then(items=>items.length)"),4);
  await screenshot(admin,'admin-users');
  await click(admin,'模型服务','nav button');await until(()=>js(admin,"!!document.querySelector('input[type=url]')"),'model settings');
  await field(admin,'服务名称','统一研究模型');await field(admin,'模型服务地址','https://models.example.invalid/v1');await field(admin,'模型名称','research-model-fixture');await click(admin,'保存模型配置');
  await until(()=>js(admin,"document.body.innerText.includes('模型配置已保存')"),'save model settings');await screenshot(admin,'admin-models');
  await click(admin,'公共资料库','nav button');
  await until(()=>js(admin,"!!document.querySelector('.kb-file-layout')"),'shared library administrator');
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
  await click(user,'文献库','aside button');
  await until(()=>js(user,"!!document.querySelector('.kb-file-layout')"),'shared library reader');
  await until(()=>js(user,"document.querySelector('.kb-file-groups')?.textContent.includes('共享史料')"),'shared catalog visible to user');
  assert.equal(await js(user,"[...document.querySelectorAll('button')].some(button=>button.textContent==='导入文献')"),false);
  assert.equal(await js(user,"fetch('/api/knowledge/catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(value=>value.status)"),403);
  await click(user,'共享史料','.kb-file-groups button');
  await until(()=>js(user,"!!document.querySelector('.kb-file-name')"),'shared file visible');
  await click(user,'公共测试文献.txt','.kb-file-name');
  await until(()=>js(user,"!!document.querySelector('.kb-document-heading')"),'read shared original');
  await click(user,'文献信息');
  assert.equal(await js(user,"[...document.querySelectorAll('.modal-actions button')].some(button=>button.textContent==='编辑文献信息')"),false);
  await click(user,'关闭','.modal-actions button');
  await screenshot(user,'user-shared-document');
  await click(user,'个人设置');
  await until(()=>js(user,"!!document.querySelector('.settings-hub-tabs')"),'personal settings');
  const tabs=await js(user,"[...document.querySelectorAll('.settings-hub-tabs button')].map(item=>item.textContent.trim())");
  assert.ok(!tabs.includes('模型连接'));assert.ok(!tabs.includes('数据与备份'));
  await click(user,'账号与安全','.settings-hub-tabs button');await screenshot(user,'user-account');
  await user.loadURL(base+'/admin/');await until(()=>js(user,"document.body.innerText.includes('这里是管理员工作区')"),'admin route denied');
  await screenshot(user,'user-admin-denied');
  await click(admin,'运行概览','nav button');await click(admin,'刷新');
  await until(()=>js(admin,"document.querySelectorAll('.portal-stat strong')[1]?.textContent==='1'"),'real workspace statistics');
  await screenshot(admin,'admin-overview');
  admin.setSize(430,900);await delay(150);await screenshot(admin,'admin-mobile');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({web_login:true,administrator_console:true,user_creation:true,model_configuration:true,private_user_workbench:true,shared_library:true,shared_original_reader:true,public_write_denied:true,user_admin_api_denied:true,user_admin_page_denied:true,user_settings_separated:true,responsive_layout:true,renderer_errors:errors},null,2));
  console.log(JSON.stringify({verified:true,output}));
}).then(()=>cleanup(0)).catch(async error=>{console.error(stage+': '+error.message);for(const [name,window] of [['admin',admin],['user',user]])if(window&&!window.isDestroyed()){await screenshot(window,name+'-failure');fs.writeFileSync(path.join(output,name+'-failure.txt'),await js(window,'document.body.innerText'));}await cleanup(1);});

async function cleanup(code){
  for(const window of [admin,user])if(window&&!window.isDestroyed())window.destroy();
  if(child&&child.exitCode===null){fs.writeFileSync(path.join(output,'data','gui.stop'),'stop');await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(120000)]);if(child.exitCode===null){child.kill();code=1;}}
  fs.closeSync(log);app.exit(code);
}
