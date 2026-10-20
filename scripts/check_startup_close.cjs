// Close the real packaged window while its service is starting; no user data.
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const project=path.resolve(__dirname,'..'),output=path.join(project,'.local','reader-rc11');
const root=JSON.parse(fs.readFileSync(path.join(output,'packaged-gui-report.json'),'utf8')).root;
const scratch=path.join(output,'startup-close-'+crypto.randomUUID());fs.mkdirSync(scratch);
const config=path.join(scratch,'bootstrap.json');fs.writeFileSync(config,JSON.stringify({format:1,dataDir:root}));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label,seconds=60){const deadline=Date.now()+seconds*1000;while(Date.now()<deadline){if(await check())return;await delay(50);}throw Error('Timeout: '+label);}
async function main(){
  const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
  const binary=path.join(project,'release','v0.5.0-rc.11','win-unpacked','史学工作台候选版.exe');
  const child=spawn(binary,[`--remote-debugging-port=${port}`,'--remote-debugging-address=127.0.0.1'],{windowsHide:true,stdio:'ignore',env:{...process.env,SHIXUE_BOOTSTRAP_FILE:config,SHIXUE_DESKTOP_PROFILE:path.join(scratch,'profile'),SHIXUE_HEADLESS:'1',HF_HUB_OFFLINE:'1'}});
  let socket;
  try{
    let target;await until(async()=>{try{target=(await fetch(`http://127.0.0.1:${port}/json/list`).then(r=>r.json())).find(t=>t.type==='page'&&t.url.includes('index.html'));return !!target;}catch{return false;}},'window');
    socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.addEventListener('open',r,{once:true});socket.addEventListener('error',j,{once:true});});
    let id=0;const pending=new Map();socket.addEventListener('message',e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});
    const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
    let status;await until(async()=>{const result=await send('Runtime.evaluate',{expression:'window.desktop?.serviceStatus?.()',awaitPromise:true,returnByValue:true});status=result.result.value;return !!status;},'preload');
    if(status.stage==='ready')throw Error('Service finished before the early-close scenario could be exercised');
    void send('Browser.close').catch(()=>{});await until(()=>child.exitCode!==null,'normal early exit');
    if(fs.existsSync(path.join(root,'knowledge-db','postmaster.pid')))throw Error('Database left running');
    const report={early_stage:status.stage,closed_during_startup:true,normal_exit:child.exitCode===0,database_stopped:true,isolated_data:true};
    if(!report.normal_exit)throw Error('Unexpected exit status');
    fs.writeFileSync(path.join(output,'startup-close-report.json'),JSON.stringify(report,null,2));process.stdout.write(JSON.stringify(report));
  }finally{socket?.close();if(child.exitCode===null)throw Error('Test app still running; no forced database termination performed');}
}
main().catch(e=>{fs.writeFileSync(path.join(output,'startup-close-error.txt'),String(e.stack));process.exitCode=1;});
