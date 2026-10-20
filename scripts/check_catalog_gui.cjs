// Offscreen regression using a new native cluster and a disposable profile.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs'),os=require('node:os'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');const net=require('node:net');
const project=path.resolve(__dirname,'..');const root=fs.mkdtempSync(path.join(os.tmpdir(),'文献库 界面-'));
app.setPath('userData',path.join(root,'electron'));
let service,window,base,token,exit;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(expression){for(let i=0;i<120;i++){if(await window.webContents.executeJavaScript(expression))return;await delay(100);}throw Error('界面状态未按预期出现');}
async function click(text){await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`);}
app.whenReady().then(async()=>{
  token=crypto.randomBytes(32).toString('hex');
  const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
  base=`http://127.0.0.1:${port}`;
  const env={...process.env};delete env.SHIXUE_KB_DATABASE_URL;
  const binary=process.env.SHIXUE_GUI_BINARY;
  service=spawn(binary||path.join(project,'.venv/Scripts/python.exe'),[...(binary?[]:[path.join(project,'backend/app.py')]),'--port',String(port),'--data-dir',root,'--token',token,'--database-mode','internal'],{windowsHide:true,stdio:'ignore',env});
  exit=new Promise(resolve=>service.on('exit',resolve));
  for(let i=0;i<300;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}if(service.exitCode!==null)throw Error('资料服务启动失败');await delay(200);}
  const headers={'X-App-Token':token,'Content-Type':'application/json'};
  const activation=await (await fetch(base+'/api/knowledge/activation',{headers})).json();if(!activation.active)throw Error('新文献库未启用');
  const edition=await (await fetch(base+'/api/knowledge/catalog',{method:'POST',headers,body:JSON.stringify({title:'古籍与论文共用文献库',label:'甲本',item_type:'ancient'})})).json();
  await fetch(base+'/api/knowledge/catalog',{method:'POST',headers,body:JSON.stringify({work_id:edition.work_id,title:edition.title,label:'乙本',item_type:'ancient'})});
  ipcMain.handle('api:request',async(event,request)=>{const response=await fetch(base+request.path,{method:request.method||'GET',headers,body:request.body===undefined?undefined:JSON.stringify(request.body)});return {status:response.status,contentType:response.headers.get('content-type'),body:await response.json()};});
  ipcMain.handle('file:pick-pdf',()=>[]);ipcMain.handle('file:save',()=>false);ipcMain.handle('app:open-external',()=>{});
  window=new BrowserWindow({width:1500,height:1000,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(project,'electron/preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  await window.loadFile(path.join(project,'dist/index.html'));
  await waitFor(`Array.from(document.querySelectorAll('.nav-list button')).some(b=>b.textContent.startsWith('文献库'))`);
  await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.nav-list button')).find(b=>b.textContent.startsWith('文献库')).click()`);
  await waitFor(`document.body.innerText.includes('古籍与论文共用文献库')`);
  const menus=await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.nav-list button')).filter(b=>b.textContent.startsWith('文献库')).length`);if(menus!==1)throw Error('文献库入口重复');
  await click('版本信息');await waitFor(`document.querySelector('.kb-editor input[required]')?.disabled===true`);
  await click('取消');await click('作品信息');await waitFor(`document.querySelector('.kb-editor input[required]')?.disabled===false`);await click('取消');
  await delay(500);fs.writeFileSync(path.join(project,'.local/catalog-ui.png'),(await window.webContents.capturePage()).toPNG());
  await click('项目材料');await waitFor(`document.body.innerText.includes('导入并关联项目')`);
  await fetch(base+'/api/service/exit',{method:'POST',headers});await Promise.race([exit,delay(55000).then(()=>{throw Error('数据库没有正常关闭');})]);
  if(fs.existsSync(path.join(root,'knowledge-db/postmaster.pid')))throw Error('数据库停止后仍有进程记录');
  console.log('统一文献库导航、作品/版本编辑、项目材料和正常退出通过');
  fs.writeFileSync(path.join(project,'.local/catalog-gui-report.json'),JSON.stringify({passed:true,single_catalog_entry:true,separate_work_and_edition_editors:true,project_sources:true,clean_shutdown:true,root},null,2));
  window.destroy();app.quit();
}).catch(async error=>{fs.writeFileSync(path.join(project,'.local/catalog-gui-report.json'),JSON.stringify({passed:false,error:error.message,root},null,2));console.error(error.message);if(base){try{await fetch(base+'/api/service/exit',{method:'POST',headers:{'X-App-Token':token}});await Promise.race([exit,delay(55000)]);}catch{}}if(window&&!window.isDestroyed())window.destroy();app.exit(1);});
