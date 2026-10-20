const fs=require('node:fs'),path=require('node:path');
function readConfig(file){
  if(!fs.existsSync(file))return null;
  const value=JSON.parse(fs.readFileSync(file,'utf8'));
  if(value.format!==1||typeof value.dataDir!=='string'||!path.isAbsolute(value.dataDir))throw Error('资料目录配置无效，请恢复配置后重试');
  if(!fs.existsSync(path.join(value.dataDir,'library.sqlite')))throw Error('已配置的资料目录不可用：'+value.dataDir+'。请连接原磁盘或选择现有资料，未建立空库。');
  if(fs.existsSync(path.join(value.dataDir,'.workspace-copy-incomplete')))throw Error('资料目录迁移尚未完成');
  return path.resolve(value.dataDir);
}
function writeConfig(file,dataDir){
  if(!path.isAbsolute(dataDir)||!fs.existsSync(path.join(dataDir,'library.sqlite')))throw Error('资料目录尚未完成初始化');
  fs.mkdirSync(path.dirname(file),{recursive:true});const pending=file+'.'+process.pid+'.tmp';
  try{fs.writeFileSync(pending,JSON.stringify({format:1,dataDir:path.resolve(dataDir)},null,2));fs.renameSync(pending,file);}finally{if(fs.existsSync(pending))fs.unlinkSync(pending);}
}
function defaultDirectory({portableDir,projectDir,appData}){
  if(projectDir)return path.join(projectDir,'工作台数据');
  if(portableDir){const parent=path.dirname(portableDir);return path.basename(parent).toLowerCase()==='release'?path.join(path.dirname(parent),'工作台数据'):path.join(parent,'工作台数据');}
  return path.join(appData,'shixue-workbench','工作台数据');
}
function legacyDirectories({portableDir,profileDir}){
  const values=[];
  if(portableDir){const parent=path.dirname(portableDir);if(fs.existsSync(parent))for(const entry of fs.readdirSync(parent,{withFileTypes:true}))if(entry.isDirectory()&&/^v?\d+\.\d+/.test(entry.name))values.push(path.join(parent,entry.name,'史学工作台验证数据'));
    values.push(path.join(portableDir,'史学工作台验证数据'),path.join(portableDir,'史学工作台数据'));}
  values.push(profileDir);
  return [...new Set(values)].filter(v=>fs.existsSync(path.join(v,'library.sqlite'))).sort((a,b)=>fs.statSync(path.join(b,'library.sqlite')).mtimeMs-fs.statSync(path.join(a,'library.sqlite')).mtimeMs);
}
module.exports={readConfig,writeConfig,defaultDirectory,legacyDirectories};
