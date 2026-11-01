const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const results = [];

async function documentRows(c) {
  for(const [width,height,label] of [[1480,1000,'desktop'],[2372,1200,'wide'],[3299,1709,'ultrawide'],[430,900,'mobile']]) {
    c.admin.setSize(width,height);await c.delay(180);
    const rows=await c.js(c.admin,`[...document.querySelectorAll('.rag-document-row')].map(row=>{
      const bounds=e=>{const r=e.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width,top:r.top,bottom:r.bottom,height:r.height};};
      return {row:bounds(row),label:bounds(row.querySelector('.rag-document-checkbox')),checkbox:bounds(row.querySelector('input[type=checkbox]')),icon:bounds(row.querySelector('svg')),text:bounds(row.querySelector('.rag-document-checkbox>span')),action:bounds(row.querySelector('.document-strategy-button')),busy:!!row.querySelector('.rag-job-action')};
    })`);
    assert.ok(rows.length>=4,'document row alignment needs realistic titles');
    for(const row of rows) {
      assert.ok(row.checkbox.width<=18,'checkbox must not inherit full input width');
      assert.ok(Math.abs(row.checkbox.x-row.row.x)<2,'checkbox aligned to row start');
      assert.ok(row.icon.x-row.checkbox.right>=8&&row.icon.x-row.checkbox.right<=16,'icon stays beside checkbox');
      assert.ok(row.text.x-row.icon.right>=8&&row.text.x-row.icon.right<=16,'document text stays beside icon');
      assert.ok(row.text.width>row.label.width*.65,'document information uses available width');
      if(width>760)assert.ok(Math.abs(row.action.right-row.row.right)<2,'strategy action aligned to right edge');
      else assert.ok(row.action.top>=row.label.bottom-1,'mobile actions sit below document information');
    }
    assert.ok(Math.max(...rows.map(r=>r.text.x))-Math.min(...rows.map(r=>r.text.x))<2,'titles share one left alignment');
    await c.js(c.admin,"document.querySelector('.rag-documents').scrollIntoView({block:'start'})");
    await capture(c,c.admin,'document-rows-'+label);
    results.push({page:'document-row-geometry-'+label,rows});
  }
  c.admin.setSize(1480,1000);await c.delay(100);
}

async function capture(c, window, name) {
  await c.delay(220);
  const geometry = await c.js(window, `(()=>{
    const root=document.querySelector('.content-scroll')||document.querySelector('.portal-admin-content');
    const visible=element=>element.getBoundingClientRect().width>0&&element.getBoundingClientRect().height>0&&getComputedStyle(element).visibility!=='hidden';
    const headings=[...root.querySelectorAll('h1')].filter(visible).map(element=>({text:element.textContent,x:element.getBoundingClientRect().x}));
    return {headings,rootX:root.getBoundingClientRect().x,viewport:innerWidth,bodyWidth:document.documentElement.scrollWidth,
      alerts:[...document.querySelectorAll('[role=alert],.toast')].filter(visible).map(element=>element.textContent)};
  })()`);
  assert.ok(geometry.headings.length<=1,name+': duplicate page headings '+JSON.stringify(geometry.headings));
  assert.ok(geometry.bodyWidth<=geometry.viewport,name+': page horizontal overflow');
  assert.deepEqual(geometry.alerts,[],name+': page errors');
  await c.screenshot(window,'audit-'+name);
  results.push({page:name,...geometry});
}

async function assistant(c, mode, suffix) {
  await c.click(c.user,mode,'.assistant-mode-tabs button');
  const fields=await c.js(c.user,`[...document.querySelectorAll('.project-assistant-panel .assistant-source-fields select')].map(element=>{const r=element.getBoundingClientRect();return {top:r.top,height:r.height};})`);
  assert.equal(fields.length,2);
  assert.ok(Math.abs(fields[0].top-fields[1].top)<=1,'assistant select alignment');
  assert.ok(fields.every(field=>field.height===40),'assistant control height');
  await c.screenshot(c.user,'audit-assistant-'+suffix);
  results.push({page:'assistant-'+suffix,fields});
}

async function auditEmpty(c) {
  for(const [label,title] of [['项目材料','项目材料'],['研究工作','研究工作'],['工作成果','工作成果'],['执行记录','任务记录'],['论文投稿管理','论文投稿管理']]) {
    await c.click(c.user,label,'aside button');
    await c.until(()=>c.js(c.user,`[...document.querySelectorAll('.content-scroll h1')].some(item=>item.textContent===${JSON.stringify(title)})`),'empty '+label);
    await capture(c,c.user,'empty-'+label);
  }
  await c.click(c.user,'新建论文');await c.until(()=>c.js(c.user,"!!document.querySelector('.modal-panel')"),'paper dialog');
  const panel=await c.js(c.user,"(()=>{const r=document.querySelector('.modal-panel').getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,viewport:innerWidth};})()");
  assert.ok(Math.abs((panel.left+panel.right)/2-panel.viewport/2)<2,'dialog must center on viewport');
  await c.screenshot(c.user,'audit-new-paper-dialog');results.push({page:'new-paper-dialog',...panel});
  await c.js(c.user,"fetch('/api/session',{method:'DELETE'}).then(()=>window.dispatchEvent(new Event('wenjin:unauthorized')))");
  await c.until(()=>c.js(c.user,"!!document.querySelector('input[autocomplete=username]')&&!document.querySelector('.modal-panel')"),'login masks portalled dialog');
  await c.screenshot(c.user,'audit-dialog-login-mask');
  await c.field(c.user,'用户名','alice');await c.field(c.user,'密码',c.password);await c.click(c.user,'进入研究工作区');
  await c.until(()=>c.js(c.user,"!!document.querySelector('.modal-panel')&&!document.querySelector('input[autocomplete=username]')"),'restore same-account dialog');
  results.push({page:'dialog-login-mask',verified:true});

  await c.click(c.user,'取消','.modal-actions button');
  await c.click(c.user,'项目概览','aside button');
  await c.until(()=>c.js(c.user,"!!document.querySelector('.assistant-mode-tabs')"),'assistant');
  await assistant(c,'单助手研究','single');
  await c.js(c.user,"document.querySelector('.project-assistant-panel details summary').click()");await c.screenshot(c.user,'audit-assistant-single-budget');
  await assistant(c,'协同研究','team');
  await c.js(c.user,"document.querySelector('.project-assistant-panel details summary').click()");await c.screenshot(c.user,'audit-assistant-team-budget');
  await assistant(c,'单助手研究','single-restored');
}

async function auditPopulated(c, group) {
  const project=await c.js(c.user,`(async()=>{
    const api=async(path,method='GET',body)=>{const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(path+' '+r.status+' '+await r.text());return r.json();};
    const project=(await api('/api/projects')).find(item=>item.name==='明清地方社会研究');
    const entry=(await api('/api/system-library/knowledge/document-tree?group_id='+${JSON.stringify(group)})).files[0];
    await api('/api/knowledge/projects/'+project.id+'/sources/'+entry.edition_id,'PUT',{roles:['reference']});
    const data=await api('/api/bootstrap?project_id='+project.id);
    await api('/api/projects/'+project.id+'/units','POST',{title:'界面验收稿件',kind:'reading',material_id:data.materials[0].id});
    return project;
  })()`);
  fs.writeFileSync(path.join(c.output,'data','gui.seed.json'),JSON.stringify({project_id:project.id}));
  await c.until(()=>fs.existsSync(path.join(c.output,'data','gui.seed-result.json')),'seed completed result');
  assert.equal(JSON.parse(fs.readFileSync(path.join(c.output,'data','gui.seed-result.json'),'utf8')).ok,true);
  await c.click(c.user,'研究工作','aside button');
  await c.until(()=>c.js(c.user,"document.querySelector('.unit-list')?.textContent.includes('界面验收稿件')"),'populated research');
  await capture(c,c.user,'research-list');
  await c.click(c.user,'界面验收稿件','.unit-list button');
  await c.until(()=>c.js(c.user,"!!document.querySelector('.research-editor')||!!document.querySelector('.unit-editor')"),'research editor');
  await capture(c,c.user,'research-editor');
  await c.click(c.user,'项目材料','aside button');await capture(c,c.user,'project-materials');
  await c.click(c.user,'设置用途与范围');await capture(c,c.user,'project-source-edit');await c.click(c.user,'取消','.kb-editor button');
  await c.click(c.user,'公共测试文献.txt','.entry-file-open');await capture(c,c.user,'material-reader');
}

async function auditResults(c) {
  await c.click(c.user,'工作成果','aside button');
  await c.until(()=>c.js(c.user,"document.body.innerText.includes('界面验收研究成果')"),'populated results');
  await capture(c,c.user,'results-populated');
  await c.click(c.user,'界面验收研究成果','.artifact-open');await capture(c,c.user,'result-detail');
  await c.click(c.user,'原文与结果对照');await capture(c,c.user,'result-compare');
  await c.click(c.user,'执行记录','aside button');await capture(c,c.user,'task-list');
  await c.js(c.user,"document.querySelector('.task-open-row').click()");await c.until(()=>c.js(c.user,"!!document.querySelector('.detail-page .welcome-row h1')"),'task detail loaded');await capture(c,c.user,'task-detail');
}

async function auditSettings(c) {
  await c.click(c.user,'个人设置');
  for(const label of ['账号与安全','外观']) {
    await c.click(c.user,label,'.settings-hub-tabs button');
    await capture(c,c.user,'settings-'+label);
  }
  const tabs=await c.js(c.user,"[...document.querySelectorAll('.settings-hub-tabs button')].map(item=>item.textContent.trim())");
  assert.deepEqual(tabs,['外观','账号与安全']);
}

async function auditCollation(c, group) {
  await c.js(c.user,`(async()=>{
    const api=async(path,method='GET',body)=>{const response=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(path+' '+response.status);return response.json();};
    const project=await api('/api/projects','POST',{name:'古籍点校界面验收',project_type:'collation'});
    const edition=(await api('/api/system-library/knowledge/document-tree?group_id='+${JSON.stringify(group)})).files[0].edition_id;
    await api('/api/knowledge/projects/'+project.id+'/sources/'+edition,'PUT',{roles:['base']});
  })()`);
  await c.click(c.user,'明清地方社会研究','.project-switch');
  await c.until(()=>c.js(c.user,"document.querySelector('.project-menu')?.textContent.includes('古籍点校界面验收')"),'collation project menu');
  await c.click(c.user,'古籍点校界面验收','.project-menu button');
  await c.until(()=>c.js(c.user,"document.querySelector('.project-switch')?.textContent.includes('古籍点校界面验收')"),'collation project');
  await c.click(c.user,'古籍点校','aside .nav-item');
  await c.until(()=>c.js(c.user,"!!document.querySelector('.collation-steps')"),'collation flow');
  for(const [label,name] of [['2 准备卷文本','collation-prepare'],['3 批量点校','collation-batch'],['4 点校稿','collation-drafts']]) {
    await c.click(c.user,label,'.collation-steps button');await capture(c,c.user,name);
  }
  await c.click(c.user,'点校调试');
  await c.until(()=>c.js(c.user,"!!document.querySelector('.collation-debug')"),'collation debug');
  await capture(c,c.user,'collation-debug');
  await c.click(c.user,'古籍点校界面验收','.project-switch');
  await c.click(c.user,'明清地方社会研究','.project-menu button');
}

async function auditAdmin(c) {
  for(const label of (c.processingOnly?['系统文献库','检索配置']:['运行概览','用户管理','工作区运行','模型服务','操作记录','账号与安全','系统文献库','期刊库管理','检索配置','助手与 Skill'])) {
    await c.click(c.admin,label,'nav button');
    await capture(c,c.admin,'admin-'+label);
    if(label==='系统文献库') {
      await c.click(c.admin,'导入文献','.kb-workbench button');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.kb-import-preview')||!!document.querySelector('[aria-label=导入分类名称]')"),'import strategy dialog');
      await c.js(c.admin,`(()=>{const original=HTMLInputElement.prototype.click;HTMLInputElement.prototype.click=function(){if(this.type!=='file')return original.call(this);HTMLInputElement.prototype.click=original;const transfer=new DataTransfer();transfer.items.add(new File(['# 第一章\\n\\n财政制度。'.repeat(35)+'\\n# 第二章\\n礼制沿革。'.repeat(35)],'自动切分样本.md',{type:'text/markdown'}));transfer.items.add(new File(['税制文献原文。'.repeat(60)],'独立规则样本.txt',{type:'text/plain'}));this.files=transfer.files;this.dispatchEvent(new Event('change',{bubbles:true}));};})()`);
      await c.click(c.admin,'选择文档（可多选）');
      await c.until(()=>c.js(c.admin,"document.querySelectorAll('.kb-import-strategy select').length===2"),'per-upload document strategy choices');
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label="上传文档切分策略 独立规则样本.txt"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'fixed');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await capture(c,c.admin,'admin-upload-document-strategies');
      c.admin.setSize(430,900);await capture(c,c.admin,'admin-upload-strategies-mobile');c.admin.setSize(1480,1000);
      await c.click(c.admin,'确认导入');
      await c.until(()=>c.js(c.admin,`fetch('/api/admin/library/knowledge/rag-documents').then(r=>r.json()).then(v=>v.items.some(d=>d.title==='独立规则样本'&&d.active_revision&&d.processing_config?.chunking.mode==='fixed')&&v.items.some(d=>d.title==='自动切分样本'&&d.active_revision))`),'mixed batch parsed with independent strategy');
    }
    if(label==='检索配置') {
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.rag-documents')&&!document.querySelector('[aria-label=入库预览文本]')"),'existing document processing is the default page');
      await c.js(c.admin,`(async()=>{for(const title of ['一乘佛性究竟論','一乘決疑論','一切如來大祕密王未曾有最上微妙大曼拏羅經']){const r=await fetch('/api/admin/library/knowledge/catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,item_type:'ancient'})});if(!r.ok)throw Error('Document title fixture');}document.querySelector('.rag-document-filter button[type=button]').click();})()`);
      await c.until(()=>c.js(c.admin,"document.querySelectorAll('.rag-document-row').length>=6"),'long document titles listed');
      assert.equal(await c.js(c.admin,"!!document.querySelector('[aria-label=整体加工进度]')"),true);
      assert.equal(await c.js(c.admin,"[...document.querySelectorAll('button')].some(b=>b.textContent==='全库入库')"),true);
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=每页文献数]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'100');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await c.until(()=>c.js(c.admin,"document.querySelectorAll('.rag-document-row').length>=6"),'100-document page size loaded');
      assert.ok(await c.js(c.admin,"document.querySelectorAll('[aria-label=文献加工进度]').length>0"),'individual document progress');
      await documentRows(c);
      await c.js(c.admin,"document.querySelector('.rag-documents').scrollIntoView({block:'start'})");
      await capture(c,c.admin,'existing-document-processing');
      await c.js(c.admin,`(()=>{window.confirm=()=>true;const original=window.fetch;window.__processingRequests=[];window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/rag-process'))window.__processingRequests.push({request:JSON.parse(args[1].body),status:response.status,result:await response.clone().json()});return response;};document.querySelector('[aria-label="加工入向量库 自动切分样本"]').click();})()`);
      await c.until(()=>c.js(c.admin,"window.__processingRequests?.length===1"),'single existing document submitted');
      const submission=await c.js(c.admin,"window.__processingRequests[0]");
      assert.equal(submission.status,200);assert.equal(submission.request.operation,'process');assert.equal(submission.request.documents.length,1);assert.equal(submission.result.jobs[0].kind,'index');
      await c.until(()=>c.js(c.admin,"document.querySelector('.rag-feedback[role=status]')?.textContent.includes('已提交 1 项后台任务')"),'processing submission feedback');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=当前批次进度]')"),'batch progress after submission');
      const progressScope=await c.js(c.admin,"document.querySelector('[aria-label=选择加工批次]').value");
      await c.js(c.admin,"document.querySelector('[aria-label=全选本页可加工文献]').click()");
      assert.ok(await c.js(c.admin,"[...document.querySelectorAll('.rag-document-checkbox input:checked')].length>0"),'eligible documents can be selected in bulk');
      await c.js(c.admin,"document.querySelector('[aria-label=全选本页可加工文献]').click()");
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=筛选向量加工状态]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'pending');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await c.until(()=>c.js(c.admin,"document.querySelectorAll('.rag-document-row').length>0&&[...document.querySelectorAll('.rag-document-row')].every(r=>r.dataset.vectorState==='pending')"),'pending vector filter');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=选择加工批次]').value"),progressScope,'filter preserves processing batch');
      await c.js(c.admin,"document.querySelector('.rag-overall-progress').scrollIntoView({block:'start'})");
      await capture(c,c.admin,'batch-progress');
      c.admin.setSize(430,900);await capture(c,c.admin,'batch-progress-mobile');c.admin.setSize(1480,1000);
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=筛选向量加工状态]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'all');s.dispatchEvent(new Event('change',{bubbles:true}));const q=document.querySelector('[aria-label=筛选加工文献]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(q,'公共测试文献');q.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await c.click(c.admin,'筛选','.rag-document-filter button');
      await c.until(()=>c.js(c.admin,"document.querySelectorAll('.rag-document-row').length===1"),'whole-library filtered target');
      await c.js(c.admin,"[...document.querySelectorAll('button')].find(b=>b.textContent.startsWith('全选所有匹配文献')).click()");
      await c.js(c.admin,"[...document.querySelectorAll('.rag-document-submit button')].find(b=>b.textContent.startsWith('加工全部已选文献')).click()");
      await c.until(()=>c.js(c.admin,"document.querySelector('.rag-feedback[role=status]')?.textContent.includes('已提交全量批次：1 份文献')"),'filtered bulk submission');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=分批入队进度]')"),'durable bulk dispatch progress');
      await capture(c,c.admin,'whole-library-processing');
      if(c.processingOnly)return;
      await c.click(c.admin,'入库与切分配置');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=入库预览文本]')&&!document.querySelector('.rag-documents')"),'separate ingestion settings');
      await c.js(c.admin,"(()=>{const box=document.querySelector('[aria-label=移除网址]');if(!box.checked)box.click();const e=document.querySelector('[aria-label=入库预览文本]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'古籍原文  https://example.org 赋税制度。'.repeat(8));e.dispatchEvent(new Event('input',{bubbles:true}));})()");
      await c.click(c.admin,'预览清洗与拆分');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.rag-processing-preview')"),'server ingestion preview');
      assert.equal(await c.js(c.admin,"document.querySelector('.rag-processing-preview details pre').textContent.includes('https://')"),false);
      await capture(c,c.admin,'admin-rag-ingestion-preview');
      c.admin.setSize(430,900);await capture(c,c.admin,'admin-rag-ingestion-mobile');c.admin.setSize(1480,1000);
      await c.click(c.admin,'文献加工','.rag-tabs button');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=\"切分策略 独立规则样本\"]')"),'individual document strategy entry');
      await c.js(c.admin,"document.querySelector('[aria-label=\"切分策略 独立规则样本\"]').click()");
      await c.until(()=>c.js(c.admin,"document.querySelector('[aria-label=文档分块模式]')?.value==='fixed'"),'persisted document override');
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=文档分块模式]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'parent_child');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=文档父块策略]')"),'parent strategy settings');
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=文档父块策略]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'semantic');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await c.click(c.admin,'预览此文档切分');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.document-strategy-dialog .chunk-analysis')"),'actual document processing preview');
      await capture(c,c.admin,'admin-document-semantic-parent-preview');
      c.admin.setSize(430,900);await capture(c,c.admin,'admin-document-strategy-mobile');c.admin.setSize(1480,1000);
      await c.click(c.admin,'保存此文档策略');
      await c.until(()=>c.js(c.admin,"document.querySelector('.document-strategy-dialog')?.textContent.includes('此文档策略已保存')"),'document strategy save');
      await c.click(c.admin,'关闭','.modal-panel button');
      await c.until(()=>c.js(c.admin,"!document.querySelector('.document-strategy-dialog')"),'strategy editor closes');
      assert.equal(await c.js(c.admin,`fetch('/api/admin/library/knowledge/rag-documents').then(r=>r.json()).then(v=>v.items.find(d=>d.title==='独立规则样本').processing_config.chunking.parent_mode)`),'semantic');
      await c.click(c.admin,'向量模型','.rag-tabs button');
      await c.until(()=>c.js(c.admin,"document.querySelector('[aria-label=向量模型方案]')?.options.length>=9"),'embedding model presets');
      assert.equal(await c.js(c.admin,"document.querySelectorAll('.embedding-cache-row').length"),4);
      assert.equal(await c.js(c.admin,"[...document.querySelectorAll('.embedding-cache-row')].every(r=>r.dataset.state==='missing')"),true,'isolated fixture has no downloaded local weights');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量模型方案] option[value=minilm]').textContent.startsWith('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"),true);
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=向量模型方案]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'bge-small-zh');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量输出维度]').value"),'512');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量输出维度]').disabled"),true);
      await c.click(c.admin,'刷新模型状态');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量模型方案]').value"),'bge-small-zh','inventory refresh preserves unsaved selection');
      await c.js(c.admin,"document.querySelector('.embedding-inventory').scrollIntoView({block:'start'})");
      await capture(c,c.admin,'embedding-cache-inventory');
      c.admin.setSize(430,900);await capture(c,c.admin,'embedding-cache-inventory-mobile');c.admin.setSize(1480,1000);
      await c.js(c.admin,`(()=>{const original=window.fetch;window.__downloadRequests=[];window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/embedding-prepare'))window.__downloadRequests.push({request:JSON.parse(args[1].body),status:response.status,result:await response.clone().json()});return response;};document.querySelector('[aria-label="下载模型 intfloat/multilingual-e5-large"]').click();})()`);
      await c.until(()=>c.js(c.admin,"window.__downloadRequests?.length===1"),'row model download submitted');
      const downloaded=await c.js(c.admin,"window.__downloadRequests[0]");
      assert.equal(downloaded.status,200);assert.equal(downloaded.request.config.model,'intfloat/multilingual-e5-large');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量模型方案]').value"),'bge-small-zh','row download preserves current draft selection');
      assert.equal(await c.js(c.admin,`fetch('/api/admin/library/knowledge/embedding-info').then(r=>r.json()).then(v=>v.status.model)`),'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','download does not switch saved model');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=\"取消下载 intfloat/multilingual-e5-large\"]')||document.querySelector('.embedding-cache-row[data-model=\"intfloat/multilingual-e5-large\"]')?.textContent.includes('下载或校验失败')"),'download task controls');
      if(await c.js(c.admin,"!!document.querySelector('[aria-label=\"取消下载 intfloat/multilingual-e5-large\"]')")){
        await c.js(c.admin,"document.querySelector('[aria-label=\"取消下载 intfloat/multilingual-e5-large\"]').click()");
        await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=\"重试下载 intfloat/multilingual-e5-large\"]')"),'cancelled download can retry');
      }
      await capture(c,c.admin,'embedding-download-controls');
      await c.click(c.admin,'保存向量模型配置');
      await c.until(()=>c.js(c.admin,`fetch('/api/admin/library/knowledge/embedding-info').then(r=>r.json()).then(v=>v.status.model==='BAAI/bge-small-zh-v1.5'&&v.status.dimensions===512)`),'embedding default saved without downloading');
      await capture(c,c.admin,'embedding-local-presets');
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=向量模型方案]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'openai-large');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量输出维度]').value"),'3072');
      assert.equal(await c.js(c.admin,"document.querySelector('[aria-label=向量输出维度]').disabled"),false);
      await capture(c,c.admin,'embedding-compatible-settings');
      c.admin.setSize(430,900);await capture(c,c.admin,'embedding-settings-mobile');c.admin.setSize(1480,1000);
      await c.js(c.admin,`(()=>{const s=document.querySelector('[aria-label=向量模型方案]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'minilm');s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await c.click(c.admin,'保存向量模型配置');
      await c.until(()=>c.js(c.admin,`fetch('/api/admin/library/knowledge/embedding-info').then(r=>r.json()).then(v=>v.status.dimensions===384)`),'original embedding restored');
      await c.click(c.admin,'Query 处理','.rag-tabs button');
      await c.js(c.admin,"(()=>{const e=document.querySelector('[aria-label=\"Query 处理模式\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'multi_query');e.dispatchEvent(new Event('change',{bubbles:true}));})()");
      await capture(c,c.admin,'admin-rag-query-processing');
      await c.js(c.admin,"(()=>{const e=document.querySelector('[aria-label=\"Query 处理模式\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'off');e.dispatchEvent(new Event('change',{bubbles:true}));})()");
      await c.click(c.admin,'参数配置','.rag-tabs button');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=返回结果数]')"),'admin retrieval loaded');
      await c.js(c.admin,"(()=>{const e=document.querySelector('[aria-label=返回结果数]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'7');e.dispatchEvent(new Event('input',{bubbles:true}));})()");
      await c.click(c.admin,'保存检索配置');
      await c.until(()=>c.js(c.admin,"document.body.innerText.includes('已保存。后续检索')"),'global retrieval saved');
      await capture(c,c.admin,'admin-retrieval-saved');
      await c.click(c.admin,'检索测试','.rag-tabs button');
      await c.js(c.admin,"(()=>{const e=document.querySelector('[aria-label=检索测试问题]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'共享');e.dispatchEvent(new Event('input',{bubbles:true}));const s=document.querySelector('[aria-label=测试检索方式]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'exact');s.dispatchEvent(new Event('change',{bubbles:true}));})()");
      await c.click(c.admin,'开始检索测试');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.rag-test-summary')"),'backend retrieval test');
      assert.ok(await c.js(c.admin,"document.querySelectorAll('.rag-hit').length>0"),'retrieval test returns real sources');
      await capture(c,c.admin,'admin-rag-test-results');
      await c.click(c.admin,'评测记录','.rag-tabs button');await capture(c,c.admin,'admin-rag-evaluation');
      await c.click(c.admin,'参数配置','.rag-tabs button');
      c.admin.setSize(430,900);await capture(c,c.admin,'admin-rag-mobile');c.admin.setSize(1480,1000);
      await c.js(c.admin,"document.querySelector('button[aria-label=\"切换深色外观\"]').click()");await capture(c,c.admin,'admin-rag-dark');
      await c.js(c.admin,"document.querySelector('button[aria-label=\"切换浅色外观\"]').click()");
    }
    if(label==='助手与 Skill') {
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.kb-note-row')"),'admin skills loaded');
      await c.click(c.admin,'智能体管理','.kb-assistant-management button');
      await c.until(()=>c.js(c.admin,"!!document.querySelector('.kb-note-row')"),'admin agents loaded');
      await capture(c,c.admin,'admin-agent-list');
      await c.js(c.admin,"document.querySelector('.kb-note-row').click()");
      await c.until(()=>c.js(c.admin,"!!document.querySelector('[aria-label=配置名称]')"),'admin agent editor');
      await capture(c,c.admin,'admin-agent-editor');
    }
  }
}

module.exports={auditEmpty,auditPopulated,auditResults,auditSettings,auditCollation,auditAdmin,capture,results};
