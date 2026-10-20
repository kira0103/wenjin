const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const storage=require('../electron/workspace-path.cjs');
const parent=path.resolve('.local/temporary');fs.mkdirSync(parent,{recursive:true});
function fixture(fn){const root=fs.mkdtempSync(path.join(parent,'bootstrap-'));try{fn(root);}finally{fs.rmSync(root,{recursive:true,force:true});}}
test('fixed default is identical across portable release versions',()=>fixture(root=>{
  const one=storage.defaultDirectory({portableDir:path.join(root,'release','v0.5.0-rc.7'),appData:root});
  const two=storage.defaultDirectory({portableDir:path.join(root,'release','v0.5.0-rc.8'),appData:root});
  assert.equal(one,two);assert.equal(one,path.join(root,'工作台数据'));
}));
test('configured directory survives version changes and is atomically replaced',()=>fixture(root=>{
  const file=path.join(root,'profile','bootstrap.json'),data=path.join(root,'固定 资料');fs.mkdirSync(data);fs.writeFileSync(path.join(data,'library.sqlite'),'fixture');
  storage.writeConfig(file,data);assert.equal(storage.readConfig(file),data);storage.writeConfig(file,data);assert.equal(storage.readConfig(file),data);
  assert.deepEqual(fs.readdirSync(path.dirname(file)),['bootstrap.json']);
}));
test('missing configured database raises without creating an empty directory',()=>fixture(root=>{
  const file=path.join(root,'bootstrap.json'),data=path.join(root,'missing');fs.writeFileSync(file,JSON.stringify({format:1,dataDir:data}));
  assert.throws(()=>storage.readConfig(file),/不可用/);assert.equal(fs.existsSync(data),false);
}));
test('unfinished copy and malformed config are rejected',()=>fixture(root=>{
  const file=path.join(root,'bootstrap.json');fs.writeFileSync(file,'{"format":1,"dataDir":"relative"}');assert.throws(()=>storage.readConfig(file));
  fs.writeFileSync(path.join(root,'library.sqlite'),'fixture');fs.writeFileSync(path.join(root,'.workspace-copy-incomplete'),'{}');storage.writeConfig(file,root);assert.throws(()=>storage.readConfig(file),/尚未完成/);
}));
