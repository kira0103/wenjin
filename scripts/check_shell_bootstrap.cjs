// Exercise the actual packaged Electron main process without touching the user's profile.
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const project=path.resolve(__dirname,'..'),output=path.join(project,'.local','shell-rc12');
const guiFile=path.join(output,'packaged-gui-report.json');const gui=JSON.parse(fs.readFileSync(guiFile,'utf8'));
const root=process.env.SHIXUE_TEST_DATA_ROOT?path.resolve(process.env.SHIXUE_TEST_DATA_ROOT):gui.root,binary=path.join(project,'release','v0.5.0-rc.12','win-unpacked','史学工作台候选版.exe');
const scratch=path.join(output,'bootstrap-'+crypto.randomUUID());fs.mkdirSync(scratch);
const config=path.join(scratch,'bootstrap.json');fs.writeFileSync(config,JSON.stringify({format:1,dataDir:root}));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label,seconds=90){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await delay(100);}throw Error('Timed out: '+label);}
async function port(){return new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const value=server.address().port;server.close(()=>resolve(value));});});}
async function connect(url){const ws=new WebSocket(url);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});let id=0;const pending=new Map();ws.addEventListener('message',event=>{const value=JSON.parse(event.data);if(!value.id)return;const promise=pending.get(value.id);if(promise){pending.delete(value.id);value.error?promise.reject(Error(value.error.message)):promise.resolve(value.result);}});return {send:(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));}),close:()=>ws.close()};}
async function main(){const reports=[];
  for(const version of ['v-next-first','v-next-second']){
    const debugPort=await port(),portable=path.join(scratch,version);fs.mkdirSync(portable);
    const started=Date.now();const child=spawn(binary,[`--remote-debugging-port=${debugPort}`,'--remote-debugging-address=127.0.0.1'],{windowsHide:true,stdio:'ignore',env:{...process.env,SHIXUE_BOOTSTRAP_FILE:config,SHIXUE_DESKTOP_PROFILE:path.join(scratch,'profile'),SHIXUE_HEADLESS:'1',PORTABLE_EXECUTABLE_DIR:portable,HF_HUB_OFFLINE:'1'}});
    let protocol;
    try{let target;await until(async()=>{if(child.exitCode!==null)throw Error('Packaged app exited early');try{target=(await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(r=>r.json())).find(v=>v.type==='page'&&v.url.includes('index.html'));return !!target;}catch{return false;}},'packaged window');protocol=await connect(target.webSocketDebuggerUrl);const windowMs=Date.now()-started;
      const evaluate=async expression=>{const result=await protocol.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.text);return result.result.value;};
      await until(()=>evaluate("typeof window.desktop?.serviceStatus==='function'"),'preload bridge');const earlyStatus=await evaluate('window.desktop.serviceStatus()');
      await until(()=>evaluate("!!document.querySelector('button[aria-label=\"文献库\"]') && document.body.innerText.includes('资料服务已连接')"),'app navigation');
      const chrome=await evaluate("({titlebar:!!document.querySelector('.app-titlebar'),drag:getComputedStyle(document.querySelector('.app-titlebar')).getPropertyValue('-webkit-app-region'),theme:document.documentElement.dataset.theme})");if(!chrome.titlebar||chrome.drag!=='drag'||chrome.theme!=='light')throw Error('Custom title bar or default theme missing');
      const info=await evaluate('window.desktop.workspaceInfo()');if(path.resolve(info.dataDir)!==path.resolve(root))throw Error('Configured workspace changed');
      await evaluate("document.querySelector('button[aria-label=\"文献库\"]').click()");
      await until(()=>evaluate("!!document.querySelector('.kb-file-groups')"),'library startup');
      const tabs=await evaluate("[...document.querySelectorAll('.kb-tabs button')].map(b=>b.textContent.trim())");if(JSON.stringify(tabs)!==JSON.stringify(['资料目录','统一检索']))throw Error('Unexpected tabs');
      if(fs.existsSync(path.join(portable,'史学工作台验证数据'))||fs.existsSync(path.join(portable,'工作台数据')))throw Error('Version directory received a duplicate workspace');
      reports.push({portableDir:portable,dataDir:info.dataDir,tabs,no_version_data_copy:true,window_ms:windowMs,connected_ms:Date.now()-started,early_stage:earlyStatus.stage,custom_titlebar:chrome.titlebar,titlebar_draggable:chrome.drag==='drag',default_theme:chrome.theme});
      await until(()=>evaluate("!!document.querySelector('.kb-group-card') && document.querySelectorAll('.kb-file-groups strong').length>0"),'catalog records');const screenshot=await protocol.send('Page.captureScreenshot');fs.writeFileSync(path.join(output,'packaged-main-window.png'),Buffer.from(screenshot.data,'base64'));
      const groupResult=await evaluate("window.desktop.request({path:'/api/knowledge/document-groups'})");if(groupResult.status!==200)throw Error('Document group API failed');
      const daizhige=groupResult.body.items.find(g=>g.name==='daizhige');if(!daizhige)throw Error('Core source group missing');
      await evaluate("[...document.querySelectorAll('.kb-file-groups button')].find(b=>b.querySelector('strong')?.textContent==='daizhige').click()");
      await until(()=>evaluate("document.querySelectorAll('.kb-directory-row').length>0"),'original core folders');
      const directoryScreenshot=await protocol.send('Page.captureScreenshot');fs.writeFileSync(path.join(output,'packaged-daizhige-directory.png'),Buffer.from(directoryScreenshot.data,'base64'));
      void protocol.send('Browser.close').catch(()=>{});await until(()=>child.exitCode!==null,'graceful application exit',70);
      if(fs.existsSync(path.join(root,'knowledge-db','postmaster.pid')))throw Error('Database did not stop');
    }catch(error){fs.writeFileSync(path.join(output,'bootstrap-cause.txt'),String(error.stack));if(protocol){void protocol.send('Browser.close').catch(()=>{});await until(()=>child.exitCode!==null,'cleanup after failed check',70);}throw error;}finally{if(protocol)protocol.close();if(child.exitCode===null){child.kill();throw Error('Fixture app did not close normally');}}
  }
  fs.writeFileSync(path.join(output,'bootstrap-gui-report.json'),JSON.stringify({packaged_main_verified:true,launches:reports,stable_workspace:true},null,2));process.stdout.write(JSON.stringify({packaged_main_verified:true,launches:reports.length,stable_workspace:true}));
}
main().catch(error=>{fs.writeFileSync(path.join(output,'bootstrap-error.txt'),String(error.stack));process.exitCode=1;});
