const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path = require('node:path');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const storagePaths = require('./workspace-path.cjs');

let backend;
let backendExited=Promise.resolve();
let windowRef;
let backendReady;
let resolveReady;
let rejectReady;
let ready;
const token = crypto.randomBytes(32).toString('hex');
const dev = !app.isPackaged;
const developmentOrigin = 'http://127.0.0.1:5173';

// electron-builder's portable launcher sets this variable. Keep a portable
// copy's data beside the executable; installed/dev copies use Electron's default.
const candidate=app.getVersion().includes('-rc');
const appData=app.getPath('appData');
const bootstrapFile=process.env.SHIXUE_BOOTSTRAP_FILE?path.resolve(process.env.SHIXUE_BOOTSTRAP_FILE):path.join(appData,'shixue-workbench','bootstrap.json');
const legacyProfile=path.join(appData,candidate?'shixue-workbench-v050-candidate':'shixue-workbench');
app.setPath('userData',process.env.SHIXUE_DESKTOP_PROFILE?path.resolve(process.env.SHIXUE_DESKTOP_PROFILE):path.join(appData,'shixue-workbench','desktop-profile'));
let dataDirectory='';
let workspaceChanging=false;
const startupStarted=Date.now();
const startupPhases=[];
let startupStatus={stage:'starting',message:'正在启动资料服务'};
function startupPhase(stage,message){
  startupStatus={stage,message};startupPhases.push({stage,ms:Date.now()-startupStarted});
  try{const profile=app.getPath('userData');fs.mkdirSync(profile,{recursive:true});fs.writeFileSync(path.join(profile,'startup-timing.json'),JSON.stringify({version:app.getVersion(),phases:startupPhases},null,2));}catch{}
}
const pathOptions={portableDir:process.env.PORTABLE_EXECUTABLE_DIR,projectDir:dev?path.resolve(__dirname,'..'):null,appData,profileDir:legacyProfile};

function serviceCommand(args){
  return app.isPackaged?{binary:path.join(process.resourcesPath,'backend','ShixueWorkbenchService.exe'),args}:
    {binary:path.join(__dirname,'..','.venv','Scripts','python.exe'),args:[path.join(__dirname,'..','backend','app.py'),...args]};
}
async function storageCommand(args){
  const command=serviceCommand(args),env={...process.env};delete env.SHIXUE_KB_DATABASE_URL;delete env.SHIXUE_KB_RESTORE_URL;
  return new Promise((resolve,reject)=>{let buffer='',last={};const child=spawn(command.binary,command.args,{env,windowsHide:true,stdio:['ignore','pipe','ignore']});
    child.stdout.on('data',chunk=>{buffer+=chunk.toString();for(;;){const newline=buffer.indexOf('\n');if(newline<0)break;const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);try{last=JSON.parse(line);windowRef?.webContents.send('storage:progress',last);}catch{}}});
    child.on('error',()=>reject(Error('资料目录服务无法启动')));child.on('exit',code=>code===0?resolve(last):reject(Error(last.error||'资料目录操作未完成，原目录仍保留')));
  });
}
async function resolveDataDirectory(){
  try{const configured=storagePaths.readConfig(bootstrapFile);if(configured)return configured;}catch(error){
    const choice=await dialog.showMessageBox({type:'error',title:'资料目录不可用',message:error.message,buttons:['选择已有资料目录','退出'],defaultId:1,cancelId:1});
    if(choice.response!==0)throw error;const picked=await dialog.showOpenDialog({title:'选择完整工作台资料目录',properties:['openDirectory']});
    if(picked.canceled)throw Error('未选择资料目录');await storageCommand(['--inspect-workspace',picked.filePaths[0]]);storagePaths.writeConfig(bootstrapFile,picked.filePaths[0]);return picked.filePaths[0];
  }
  const fixed=storagePaths.defaultDirectory(pathOptions);
  if(fs.existsSync(path.join(fixed,'library.sqlite'))){await storageCommand(['--inspect-workspace',fixed]);return fixed;}
  const existing=storagePaths.legacyDirectories(pathOptions);
  if(existing.length){const choice=await dialog.showMessageBox({type:'question',title:'选择已有工作台资料',message:'发现旧版本资料。请选择当前使用的一份，之后迁移到固定资料目录。',detail:existing.map((v,i)=>`${i+1}. ${v}`).join('\n'),buttons:[...existing.map((_,i)=>`使用第 ${i+1} 份`),'选择其他目录','新建空工作区','退出'],cancelId:existing.length+2});
    if(choice.response===existing.length+2)throw Error('已取消启动');
    if(choice.response===existing.length+1)return fixed;
    let selected=existing[choice.response];if(choice.response===existing.length){const picked=await dialog.showOpenDialog({properties:['openDirectory']});if(picked.canceled)throw Error('未选择资料');selected=picked.filePaths[0];}
    await storageCommand(['--inspect-workspace',selected]);return selected;
  }
  const picked=await dialog.showMessageBox({type:'question',title:'工作台资料目录',message:'为本机工作台建立固定资料目录',detail:fixed,buttons:['建立工作区','选择已有资料目录','退出'],cancelId:2});
  if(picked.response===2)throw Error('已取消启动');if(picked.response===1){const folder=await dialog.showOpenDialog({properties:['openDirectory']});if(folder.canceled)throw Error('未选择资料');await storageCommand(['--inspect-workspace',folder.filePaths[0]]);return folder.filePaths[0];}return fixed;
}

function startBackend(port) {
  backendReady=null;ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
  let resolveExit;
  backendExited=new Promise(resolve=>{resolveExit=resolve;});
  const userData = dataDirectory;
  fs.mkdirSync(userData, { recursive: true });
  if (app.isPackaged) {
    const binary = path.join(process.resourcesPath, 'backend', 'ShixueWorkbenchService.exe');
    backend = spawn(binary, ['--port', String(port), '--data-dir', userData, '--token', token], { windowsHide: true, stdio: 'pipe' });
  } else {
    const bundledEnvironment = path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
    const python = fs.existsSync(bundledEnvironment) ? bundledEnvironment : (process.env.PYTHON || 'python');
    backend = spawn(python, [path.join(__dirname, '..', 'backend', 'app.py'), '--port', String(port), '--data-dir', userData, '--token', token], { windowsHide: true, stdio: 'pipe' });
  }
  let buffer = '';
  backend.stdout.on('data', (chunk) => {
    buffer += chunk.toString();
    for (;;) {
      const newline = buffer.indexOf('\n');
      if (newline < 0) break;
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      try {
        const message = JSON.parse(line);
        if(message.type==='startup'){
          const labels={imports:'正在加载资料服务',database:'正在连接文献数据库',workspace:'正在打开项目资料',verify:'正在检查资料状态'};
          if(labels[message.stage])startupPhase(message.stage,labels[message.stage]);
        }
        if (message.type === 'ready') {
          backendReady = message;
          startupPhase('ready','资料服务已连接');
          resolveReady(message);
          if (windowRef && !windowRef.isDestroyed()) windowRef.webContents.send('service:ready', message.url);
        }
      } catch { console.log('[service]', line); }
    }
  });
  backend.stderr.on('data', (chunk) => console.error('[service]', chunk.toString().trim()));
  backend.on('exit', (code) => {
    resolveExit();
    if(!backendReady)startupPhase('failed','资料服务未能启动，请检查资料目录或其他运行实例');
    if(!backendReady)rejectReady(Error('资料服务启动失败，请检查资料目录或运行中的其他实例'));
    if (code && !app.isQuitting && !workspaceChanging) dialog.showErrorBox('本地服务已退出', '资料服务意外关闭，请检查资料目录与程序版本。');
  });
  backend.on('error',()=>{resolveExit();startupPhase('failed','资料服务无法启动');rejectReady(Error('资料服务无法启动'));});
}

function apiPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/api/') || value.includes('..') || value.includes('://')) throw new Error('无效的本地接口');
  return value;
}

function registerApi() {
  const knowledgeStreams = new Map();
  const trustedSender = (event) => {
    const source = event.senderFrame?.url || '';
    return dev ? source.startsWith(developmentOrigin) : source.startsWith('file://') && source.includes('/dist/index.html');
  };
  ipcMain.handle('service:status',event=>{if(!trustedSender(event))throw Error('不受信任的工作台窗口');return {...startupStatus,elapsed:Math.round((Date.now()-startupStarted)/1000)};});
  ipcMain.handle('window:theme',(event,theme)=>{if(!trustedSender(event)||!['light','dark'].includes(theme))throw Error('无效主题');if(windowRef&&!windowRef.isDestroyed()){const color=theme==='dark'?'#181818':'#f7f7f5';windowRef.setBackgroundColor(color);if(process.platform==='win32')windowRef.setTitleBarOverlay({color,symbolColor:theme==='dark'?'#dedede':'#454545',height:44});}});
  ipcMain.handle('api:request', async (event, request) => {
    if (!trustedSender(event)) throw new Error('不受信任的工作台窗口');
    if(workspaceChanging)throw Error('正在切换资料目录，请等待完成');
    const config = backendReady || await ready;
    const method = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(request?.method) ? request.method : 'GET';
    const headers = { 'X-App-Token': token };
    let body;
    if (request?.body instanceof ArrayBuffer) {
      body = Buffer.from(request.body);
      headers['Content-Type'] = request.contentType || 'application/octet-stream';
    } else if (request?.body !== undefined) {
      body = JSON.stringify(request.body);
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(`${config.url}${apiPath(request.path)}`, { method, headers, body });
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/pdf') || contentType.includes('application/octet-stream')) return { status: response.status, contentType, body: await response.arrayBuffer() };
    return { status: response.status, contentType, body: await response.json().catch(() => ({})) };
  });
  ipcMain.handle('api:knowledge-stream', async (event, request) => {
    if (!trustedSender(event) || !/^[a-zA-Z0-9_-]{8,80}$/.test(request?.id || '')) throw new Error('无效请求');
    const key = `${event.sender.id}:${request.id}`;
    if (knowledgeStreams.has(key)) throw new Error('请求已存在');
    const controller = new AbortController(); knowledgeStreams.set(key, controller);
    try {
      const config = backendReady || await ready;
      const response = await fetch(`${config.url}/api/knowledge/ask-stream`, { method: 'POST', headers: { 'X-App-Token': token, 'Content-Type': 'application/json' }, body: JSON.stringify(request.body), signal: controller.signal });
      if (!response.ok || !response.body) throw new Error('流式请求未完成');
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, boundary).trim(); buffer = buffer.slice(boundary + 1);
          if (line.startsWith('data:') && !event.sender.isDestroyed()) event.sender.send(`knowledge:stream:${request.id}`, JSON.parse(line.slice(5)));
        }
      }
      return { completed: true };
    } finally { knowledgeStreams.delete(key); }
  });
  ipcMain.on('api:knowledge-stream-cancel', (event, id) => {
    if (trustedSender(event)) knowledgeStreams.get(`${event.sender.id}:${id}`)?.abort();
  });
  ipcMain.handle('file:pick-documents', async event => {
    if (!trustedSender(event)) throw new Error('不受信任的工作台窗口');
    const images = ['png', 'jpg', 'jpeg', 'tif', 'tiff'];
    const filters = [{ name: '研究资料（含扫描图片）', extensions: ['pdf', 'docx', 'doc', 'txt', ...images] }, { name: 'PDF', extensions: ['pdf'] }, { name: 'Word', extensions: ['docx', 'doc'] }, { name: '文字', extensions: ['txt'] }, { name: '扫描图片', extensions: images }];
    const result = await dialog.showOpenDialog(windowRef, { properties: ['openFile', 'multiSelections'], filters });
    if (result.canceled) return [];
    return result.filePaths.map((filePath) => { const bytes = Uint8Array.from(fs.readFileSync(filePath)); return { name: path.basename(filePath), data: bytes.buffer }; });
  });
  ipcMain.handle('library:choose-source',async(event,mode)=>{
    if(!trustedSender(event)||!['files','directory'].includes(mode)||workspaceChanging)throw Error('无法选择文献来源');
    const picked=await dialog.showOpenDialog(windowRef,{title:mode==='directory'?'选择文献目录（保留原目录结构）':'选择一个或多个文献文件',
      properties:mode==='directory'?['openDirectory']:['openFile','multiSelections'],
      ...(mode==='files'?{filters:[{name:'文献',extensions:['pdf','docx','doc','txt','md','png','jpg','jpeg','tif','tiff']}]}:{})});
    if(picked.canceled)return null;
    const config=backendReady||await ready;
    const response=await fetch(`${config.url}/api/knowledge/document-imports/preview`,{method:'POST',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:JSON.stringify({mode,paths:picked.filePaths})});
    const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'文献来源预览失败');return value;
  });
  ipcMain.handle('file:save', async (event, { filename, data }) => {
    if (!trustedSender(event)) throw new Error('不受信任的工作台窗口');
    const result = await dialog.showSaveDialog(windowRef, { defaultPath: filename });
    if (result.canceled || !result.filePath) return false;
    fs.writeFileSync(result.filePath, Buffer.from(data));
    return true;
  });
  ipcMain.handle('app:open-external', async (event, url) => {
    if (!trustedSender(event)) throw new Error('不受信任的工作台窗口');
    if (typeof url !== 'string' || url.length > 2000 || /[\s\\\x00-\x1f]/.test(url)) return;
    let target;
    try { target = new URL(url); } catch { return; }
    if (!['http:', 'https:'].includes(target.protocol) || !target.hostname || target.username || target.password) return;
    await shell.openExternal(url);
  });
  ipcMain.handle('storage:info',event=>{if(!trustedSender(event))throw Error('不受信任的窗口');return {dataDir:dataDirectory,configFile:bootstrapFile,changing:workspaceChanging,versionDependent:/[\\/]release[\\/]/i.test(dataDirectory)};});
  ipcMain.handle('storage:open',async event=>{if(!trustedSender(event))throw Error('不受信任的窗口');await shell.openPath(dataDirectory);});
  ipcMain.handle('storage:choose',async(event,mode)=>{
    if(!trustedSender(event)||!['migrate','switch'].includes(mode)||workspaceChanging)throw Error('无法切换资料目录');
    const picked=await dialog.showOpenDialog(windowRef,{title:mode==='migrate'?'选择空的固定资料目录（原目录保留）':'选择已有完整资料目录',properties:['openDirectory','createDirectory']});
    if(picked.canceled)return {changed:false};const destination=path.resolve(picked.filePaths[0]),previous=dataDirectory;
    if(destination===previous)return {changed:false};
    if(mode==='switch')await storageCommand(['--inspect-workspace',destination]);
    else if(fs.readdirSync(destination).length)throw Error('迁移目标必须为空，请新建一个空文件夹');
    const confirmation=await dialog.showMessageBox(windowRef,{type:'question',title:mode==='migrate'?'迁移完整工作区':'切换工作区',message:mode==='migrate'?'将校验复制全部资料到新位置，原目录保留。':'将打开所选目录中的资料，两个工作区不自动合并。',detail:`当前：${previous}\n目标：${destination}`,buttons:['继续','取消'],defaultId:1,cancelId:1});
    if(confirmation.response!==0)return {changed:false};
    workspaceChanging=true;
    try{
      const response=await fetch(`${backendReady.url}/api/service/exit`,{method:'POST',headers:{'X-App-Token':token}});if(!response.ok)throw Error('请先停止后台研究、导入和点校任务，再迁移或切换');
      await Promise.race([backendExited,new Promise((_,reject)=>setTimeout(()=>reject(Error('资料数据库尚未关闭')),60000))]);
      if(mode==='migrate')await storageCommand(['--migrate-workspace',previous,'--workspace-target',destination]);
      dataDirectory=destination;startBackend(await findFreePort());await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('目标资料服务未就绪')),90000))]);
      const health=await fetch(`${backendReady.url}/api/knowledge/health`,{headers:{'X-App-Token':token}}).then(r=>r.json());if(!health.ready)throw Error(health.message||'目标数据库尚未就绪');
      storagePaths.writeConfig(bootstrapFile,dataDirectory);return {changed:true,dataDir:dataDirectory};
    }catch(error){
      if(dataDirectory!==previous){try{if(backendReady)await fetch(`${backendReady.url}/api/service/exit`,{method:'POST',headers:{'X-App-Token':token}});await backendExited;}catch{}dataDirectory=previous;}
      if(!backend||backend.exitCode!==null){startBackend(await findFreePort());await ready;}
      throw error;
    }finally{workspaceChanging=false;}
  });
  ipcMain.handle('library:restore-backup',async event=>{
    if(!trustedSender(event))throw new Error('不受信任的工作台窗口');
    const bundle=await dialog.showOpenDialog(windowRef,{title:'选择文献库备份',properties:['openFile'],filters:[{name:'文献库备份',extensions:['zip']}]});
    if(bundle.canceled||!bundle.filePaths[0])return {restored:false};
    const parent=await dialog.showOpenDialog(windowRef,{title:'选择恢复副本的存放位置',properties:['openDirectory']});
    if(parent.canceled||!parent.filePaths[0])return {restored:false};
    const destination=path.join(parent.filePaths[0],'文献库恢复-'+new Date().toISOString().replace(/[:.]/g,'-'));
    const environment={...process.env};delete environment.SHIXUE_KB_RESTORE_URL;delete environment.SHIXUE_KB_DATABASE_URL;
    const binary=app.isPackaged?path.join(process.resourcesPath,'backend','ShixueWorkbenchService.exe'):path.join(__dirname,'..','.venv','Scripts','python.exe');
    const args=[...(app.isPackaged?[]:[path.join(__dirname,'..','backend','app.py')]),'--restore-bundle',bundle.filePaths[0],'--restore-target',destination];
    await new Promise((resolve,reject)=>{
      const recovery=spawn(binary,args,{env:environment,windowsHide:true,stdio:'ignore'});
      recovery.on('error',()=>reject(new Error('恢复服务无法启动')));
      recovery.on('exit',code=>code===0?resolve():reject(new Error('恢复未完成，请核验备份和目标位置。现有文献库保持可用。')));
    });
    return {restored:true,destination};
  });
}

function createWindow() {
  windowRef = new BrowserWindow({
    show: process.env.SHIXUE_HEADLESS !== '1',
    width: 1500, height: 950, minWidth: 1060, minHeight: 700,
    titleBarStyle:'hidden',titleBarOverlay:{color:'#f7f7f5',symbolColor:'#454545',height:44},
    backgroundColor: '#f7f7f5', title: '史学工作台',autoHideMenuBar:true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, offscreen:process.env.SHIXUE_HEADLESS==='1' }
  });
  windowRef.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  windowRef.webContents.once('did-finish-load',()=>{
    startupPhases.push({stage:'window',ms:Date.now()-startupStarted});
    try{fs.writeFileSync(path.join(app.getPath('userData'),'startup-timing.json'),JSON.stringify({version:app.getVersion(),phases:startupPhases},null,2));}catch{}
  });
  windowRef.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.error(`[renderer] load failed (${code}): ${description}; ${url}`);
  });
  windowRef.webContents.on('render-process-gone', (_event, details) => {
    console.error('[renderer] process stopped:', details);
  });
  windowRef.webContents.on('will-prevent-unload', (event) => {
    const choice = dialog.showMessageBoxSync(windowRef, { type: 'question', title: '工作稿尚未保存', message: '仍有未保存的修改。返回工作台保存，或放弃这些修改并退出。', buttons: ['返回保存', '放弃修改并退出'], defaultId: 0, cancelId: 0 });
    if (choice === 1) event.preventDefault();
  });
  windowRef.webContents.on('will-navigate', (event, url) => {
    if (dev && url.startsWith(developmentOrigin)) return;
    if (!dev && url.startsWith('file://')) return;
    event.preventDefault();
  });
  if (dev) windowRef.loadURL(developmentOrigin);
  else windowRef.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
}

async function findFreePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

app.whenReady().then(async () => {
  if(!app.requestSingleInstanceLock()){quitDrained=true;app.quit();return;}
  app.isQuitting = false;
  Menu.setApplicationMenu(null);
  registerApi();
  dataDirectory=await resolveDataDirectory();
  startBackend(await findFreePort());
  createWindow();
  await ready;storagePaths.writeConfig(bootstrapFile,dataDirectory);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
}).catch(error=>{dialog.showErrorBox('工作台未启动',error.message||String(error));quitDrained=true;app.quit();});

let quitDrained = false;
let quitPending = false;
app.on('before-quit', (event) => {
  if(workspaceChanging){event.preventDefault();return;}
  app.isQuitting = true;
  if (quitDrained) return;
  event.preventDefault();
  if (quitPending) return;
  quitPending = true;
  (async () => {
    try {
      // Closing the early window must still drain a service that is starting.
      if(backend&&backend.exitCode===null&&!backendReady)await Promise.race([ready,backendExited]);
      if (backendReady) {
        const stopBatch = async () => {
          const response = await fetch(`${backendReady.url}/api/collation/shutdown`, { method: 'POST', headers: { 'X-App-Token': token }, signal: AbortSignal.timeout(5000) });
          if (!response.ok) throw new Error('无法保存退出状态');
          return response.json();
        };
        let state = await stopBatch();
        const deadline = Date.now() + 185000;
        while (state.draining && Date.now() < deadline) { await new Promise(r => setTimeout(r, 1000)); state = await stopBatch(); }
        if(state.draining)throw new Error('批次仍在保存，请稍后退出');
        const shutdown=await fetch(`${backendReady.url}/api/service/exit`, { method: 'POST', headers: { 'X-App-Token': token }, signal: AbortSignal.timeout(5000) });
        if(!shutdown.ok)throw new Error('任务尚未保存，资料服务未退出');
        await Promise.race([backendExited,new Promise((resolve,reject)=>setTimeout(()=>reject(new Error('数据库尚未完成正常关闭')),55000))]);
      }
      quitDrained=true;app.quit();
    } catch (error) {
      quitPending=false;app.isQuitting=false;
      dialog.showErrorBox('暂时无法退出',String(error.message||error));
      if(!windowRef||windowRef.isDestroyed())createWindow();
    }
  })();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('second-instance',()=>{if(windowRef){if(windowRef.isMinimized())windowRef.restore();windowRef.focus();}});
