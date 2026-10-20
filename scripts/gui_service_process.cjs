const {spawn}=require('node:child_process');

// PyInstaller --onefile has a bootloader parent and a service child. Killing
// just the parent leaves the tested executable locked on Windows.
async function stopServiceTree(service){
  if(!service||service.exitCode!==null)return;
  if(process.platform==='win32'){
    await new Promise((resolve,reject)=>{
      const killer=spawn('taskkill',['/PID',String(service.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
      killer.once('error',reject);killer.once('exit',resolve);
    });
  }else service.kill();
}
module.exports={stopServiceTree};
