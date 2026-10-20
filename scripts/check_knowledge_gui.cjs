// Isolated offscreen UI check. Run with Electron, never attaches to the user's app.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('node:path');
const fs=require('node:fs');
const os=require('node:os');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const net=require('node:net');
const root=path.join(os.tmpdir(),'shixue-gui-'+crypto.randomUUID());
app.setPath('userData',path.join(root,'electron'));
let service,window;
app.whenReady().then(async()=>{
  fs.mkdirSync(root,{recursive:true});
  const project=path.resolve(__dirname,'..');
  const password=fs.readFileSync(path.join(project,'.local','knowledge','app_password'),'utf8').trim();
  const token=crypto.randomBytes(32).toString('hex');
  const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
  service=spawn(path.join(project,'.venv','Scripts','python.exe'),[path.join(project,'backend','app.py'),'--port',String(port),'--data-dir',root,'--token',token],{
    windowsHide:true,stdio:'ignore',env:{...process.env,SHIXUE_KB_DATABASE_URL:`postgresql+psycopg://shixue_app:${encodeURIComponent(password)}@127.0.0.1:55432/shixue_knowledge`}});
  const base=`http://127.0.0.1:${port}`;
  for(let i=0;i<100;i++){try{const r=await fetch(base+'/health');if(r.ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  ipcMain.handle('api:request',async(event,request)=>{
    const r=await fetch(base+request.path,{method:request.method||'GET',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:request.body===undefined?undefined:JSON.stringify(request.body)});
    return {status:r.status,contentType:r.headers.get('content-type'),body:await r.json()};
  });
  ipcMain.handle('file:pick-pdf',()=>[]);ipcMain.handle('file:save',()=>false);ipcMain.handle('app:open-external',()=>{});
  window=new BrowserWindow({width:1500,height:1000,show:false,webPreferences:{offscreen:true,backgroundThrottling:false,preload:path.join(project,'electron','preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  await window.loadFile(path.join(project,'dist','index.html'));
  for(let i=0;i<60;i++){const found=await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='研究知识库')`);if(found)break;await new Promise(r=>setTimeout(r,100));}
  await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='研究知识库').click()`);
  for(let i=0;i<80;i++){const loaded=await window.webContents.executeJavaScript(`document.body.innerText.includes('新建文献')`);if(loaded)break;await new Promise(r=>setTimeout(r,150));}
  const text=await window.webContents.executeJavaScript('document.body.innerText');
  if(!text.includes('新建文献')||!text.includes('迁移与备份'))throw Error('Knowledge UI did not finish loading');
  // Offscreen capture can retain the previous frame after the DOM is ready.
  await new Promise(resolve=>setTimeout(resolve,500));
  const screenshot=await window.webContents.capturePage();fs.writeFileSync(path.join(project,'.local','knowledge-ui.png'),screenshot.toPNG());
  await fetch(base+'/api/service/exit',{method:'POST',headers:{'X-App-Token':token}});
  console.log('Knowledge UI rendered; screenshot saved');
  window.destroy();app.quit();
}).catch(error=>{console.error(error.message);if(service)service.kill();app.exit(1);});
