import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import MarkdownPreview from './MarkdownPreview';

export type AssistantDraft={slug:string;name:string;description:string;instructions:string;tools:string[];skill_version_ids:string[];files:Record<string,string>;edit_version?:number};
export type AssistantDefinition={id:string;slug:string;draft:AssistantDraft;edit_version:number;enabled:boolean;active_version:string|null;active_number:number;
  versions?:{id:string;number:number;sha256:string;content:AssistantDraft;revoked:boolean}[];used_by?:{id:string;name:string}[]};
const blank=():AssistantDraft=>({slug:'',name:'',description:'',instructions:'',tools:['search','read','list'],skill_version_ids:[],files:{}});
const toolNames:Record<string,string>={search:'检索原文',read:'阅读原文',list:'查找文献'};

export default function AssistantManagement({onDirtyChange,onTest}:{onDirtyChange(value:boolean):void;onTest(id:string,version:string,editVersion?:number,runId?:string):void}) {
  const [kind,setKind]=useState<'skills'|'agents'>('skills'),[list,setList]=useState<AssistantDefinition[]>([]),[skills,setSkills]=useState<AssistantDefinition[]>([]);
  const [item,setItem]=useState<AssistantDefinition|null>(null),[draft,setDraft]=useState<AssistantDraft|null>(null),[files,setFiles]=useState('{}');
  const [version,setVersion]=useState(''),[filter,setFilter]=useState(''),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const [trials,setTrials]=useState<{id:string;research_run_id:string;skill_version_id:string;question:string;state:string;error:string}[]>([]);
  const folderInput=useRef<HTMLInputElement>(null);
  useEffect(()=>{folderInput.current?.setAttribute('webkitdirectory','');},[]);
  useEffect(()=>{onDirtyChange(dirty);return()=>onDirtyChange(false);},[dirty,onDirtyChange]);
  useEffect(()=>{let alive=true;setTrials([]);if(kind==='skills'&&item)void request<typeof trials>(`/api/knowledge/skills/${item.id}/tests`).then(v=>{if(alive)setTrials(v);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[kind,item?.id]);
  async function refresh(){const [values,available]=await Promise.all([request<AssistantDefinition[]>('/api/knowledge/'+kind),request<AssistantDefinition[]>('/api/knowledge/skills')]);setList(values);setSkills(available);}
  useEffect(()=>{let alive=true;void request<AssistantDefinition[]>('/api/knowledge/'+kind).then(v=>{if(alive)setList(v);}).catch(e=>{if(alive)setError(String(e.message));});
    void request<AssistantDefinition[]>('/api/knowledge/skills').then(v=>{if(alive)setSkills(v);}).catch(()=>{});return()=>{alive=false;};},[kind]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作未完成');}finally{setBusy(false);}}
  function load(value:AssistantDefinition){setItem(value);setDraft({...value.draft,edit_version:value.edit_version});setFiles(JSON.stringify(value.draft.files,null,2));setVersion(value.active_version||value.versions?.[0]?.id||'');setDirty(false);}
  function leave(){return !dirty||window.confirm('放弃未保存的助手配置修改？');}
  function update(value:Partial<AssistantDraft>){if(draft){setDraft({...draft,...value});setDirty(true);}}
  async function save(){if(!draft)return null;const parsed:unknown=JSON.parse(files);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('参考文件需为路径与文本组成的对象');
    const value=await request<AssistantDefinition>('/api/knowledge/'+kind+(item?'/'+item.id:''),item?'PUT':'POST',{...draft,files:parsed,edit_version:item?.edit_version||0});load(value);await refresh();setMessage('草稿已保存');return value;}
  async function command(action:string,body:unknown){if(!item)return;load(await request<AssistantDefinition>(`/api/knowledge/${kind}/${item.id}/${action}`,'POST',body));await refresh();}
  async function importFiles(selected:FileList|null){if(!selected?.length)return;if(!leave())return;
    let body:unknown;
    if(selected.length===1&&selected[0].name.toLowerCase().endsWith('.zip')){
      if(selected[0].size>256*1024)throw new Error('技能包超过 256 KB');const bytes=new Uint8Array(await selected[0].arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);body={data_base64:btoa(binary)};
    }else{if([...selected].reduce((n,f)=>n+f.size,0)>256*1024)throw new Error('技能目录超过 256 KB');const entries=await Promise.all([...selected].map(async f=>[f.webkitRelativePath||f.name,await f.text()]));body={files:Object.fromEntries(entries)};}
    const value=await request<AssistantDefinition>('/api/knowledge/skills/import','POST',body);setKind('skills');load(value);setList(await request<AssistantDefinition[]>('/api/knowledge/skills'));setMessage('已导入为独立草稿，请核对后测试与发布');}
  const chosen=item?.versions?.find(v=>v.id===version);
  return <section aria-label="助手配置" className="kb-assistant-management"><h2>助手配置</h2>
    <div className="kb-toolbar">{(['skills','agents'] as const).map(k=><button className={kind===k?'button-primary':'button-secondary'} key={k} onClick={()=>{if(leave()){setKind(k);setDraft(null);setItem(null);setDirty(false);setError('');}}}>{k==='skills'?'Skill 中心':'智能体管理'}</button>)}</div>
    <p>{kind==='skills'?'管理可复用的研究方法。新增技能后可用样本文献测试，再发布并绑定助手。':'为专业助手配置职责、工具和技能。发布版本供单助手或协同研究选择。'}</p>
    {error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p className="scope-note" role="status">{message}</p>}
    <div className="kb-toolbar"><input aria-label="筛选助手配置" placeholder="搜索名称或描述" value={filter} onChange={e=>setFilter(e.target.value)}/>
      <button className="button-primary" disabled={busy} onClick={()=>{if(leave()){setItem(null);setDraft(blank());setFiles('{}');setDirty(true);}}}>{kind==='skills'?'新增技能':'新增智能体'}</button>
      {kind==='skills'&&<><label className="button-secondary">导入 ZIP<input aria-label="导入技能包" type="file" accept=".zip" disabled={busy} onChange={e=>{const f=e.currentTarget.files;void act(()=>importFiles(f));e.currentTarget.value='';}}/></label>
        <label className="button-secondary">导入技能目录<input ref={folderInput} aria-label="导入技能目录" type="file" multiple disabled={busy} onChange={e=>{const f=e.currentTarget.files;void act(()=>importFiles(f));e.currentTarget.value='';}}/></label></>}
    </div>
    <div className="kb-config-layout"><div className="kb-list">{list.filter(v=>(v.draft.name+' '+v.draft.description).includes(filter)).map(value=><button key={value.id} className="kb-card kb-note-row" disabled={busy} onClick={()=>{if(leave())void act(async()=>load(await request<AssistantDefinition>(`/api/knowledge/${kind}/${value.id}`)));}}>
      <strong>{value.draft.name}</strong><span>{value.enabled?'已启用':'已停用'} · {value.active_version?'默认 v'+value.active_number:'未发布默认版本'}</span><small>{value.draft.description}</small></button>)}</div>
    {draft&&<div className="kb-editor"><h3>{item?'编辑':'新增'}{kind==='skills'?'技能':'智能体'}</h3>
      <label>名称<input aria-label="配置名称" value={draft.name} maxLength={100} onChange={e=>update({name:e.target.value})}/></label>
      <label>稳定标识<input aria-label="配置标识" disabled={!!item} value={draft.slug} placeholder="例如 paper-review" maxLength={64} onChange={e=>update({slug:e.target.value})}/></label>
      <label>适用场景<textarea aria-label="配置描述" value={draft.description} maxLength={1024} onChange={e=>update({description:e.target.value})}/></label>
      <label>方法与步骤<textarea aria-label="配置方法" className="kb-method-editor" value={draft.instructions} maxLength={12000} onChange={e=>update({instructions:e.target.value})}/></label>
      <div className="kb-toolbar">{Object.entries(toolNames).map(([key,label])=><label key={key}><input type="checkbox" checked={draft.tools.includes(key)} onChange={e=>update({tools:e.target.checked?[...draft.tools,key]:draft.tools.filter(v=>v!==key)})}/>{label}</label>)}</div>
      {kind==='agents'&&<fieldset><legend>绑定已发布技能</legend>{skills.filter(s=>s.enabled&&s.active_version).map(s=><label className="kb-binding" key={s.id}><input type="checkbox" checked={draft.skill_version_ids.includes(s.active_version!)} onChange={e=>update({skill_version_ids:e.target.checked?[...draft.skill_version_ids,s.active_version!]:draft.skill_version_ids.filter(v=>v!==s.active_version)})}/>{s.draft.name} · v{s.active_number}</label>)}
        <p className="scope-note">绑定固定技能版本；技能发布新版后，可在此重新绑定并发布角色。已绑定的旧版本保留在高级配置中。</p>
        <details><summary>固定版本绑定</summary><textarea aria-label="技能版本绑定" value={draft.skill_version_ids.join('\n')} onChange={e=>update({skill_version_ids:e.target.value.split(/\s+/).filter(Boolean)})}/></details></fieldset>}
      <details><summary>参考文件与模板</summary><p>填写相对路径与文本组成的 JSON，例如 references/guide.md。支持文本文件，单文件最多 64 KB，总包最多 256 KB。</p><textarea aria-label="配置参考文件" className="kb-method-editor" value={files} onChange={e=>{setFiles(e.target.value);setDirty(true);}}/></details>
      <div className="kb-toolbar"><button className="button-primary" disabled={busy} onClick={()=>void act(async()=>{await save();})}>保存草稿</button>
        {kind==='skills'&&<button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>{await request('/api/knowledge/skills/validate','POST',{...draft,files:JSON.parse(files),edit_version:item?.edit_version||0});setMessage('静态校验通过；请继续使用样本文献测试研究效果');})}>校验技能</button>}
        {kind==='skills'&&item&&<button className="button-secondary" disabled={busy||dirty} onClick={()=>onTest(item.id,'',item.edit_version)}>用文献测试已保存草稿</button>}
        <button className="button-secondary" disabled={busy||dirty||!item} onClick={()=>void act(()=>command('publish',{edit_version:item!.edit_version}))}>发布版本</button>
        {item&&<button className="button-secondary" disabled={busy||dirty} onClick={()=>void act(()=>command('state',{enabled:!item.enabled}))}>{item.enabled?'停用':'启用'}</button>}
        <button className="button-quiet" disabled={busy} onClick={()=>{setItem(null);setDraft({...draft,slug:draft.slug.slice(0,45)+'-copy-'+Date.now().toString(36),name:draft.name+'（副本）',edit_version:0});setDirty(true);}}>复制为新项目</button>
        {item&&!item.versions?.length&&<button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>{await request(`/api/knowledge/${kind}/${item.id}`,'DELETE');setDraft(null);setItem(null);setDirty(false);await refresh();})}>删除未发布草稿</button>}</div>
      <details><summary>方法预览</summary><MarkdownPreview text={draft.instructions}/></details>
      {!!item?.used_by?.length&&<p>被这些角色版本引用：{item.used_by.map(v=>v.name).join('、')}</p>}
      {item&&kind==='skills'&&<details><summary>技能测试记录 · {trials.length} 次</summary><button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>setTrials(await request<typeof trials>(`/api/knowledge/skills/${item.id}/tests`)))}>刷新测试记录</button>
        {trials.map(t=><button className="kb-card kb-note-row" key={t.id} disabled={dirty||busy} onClick={()=>onTest(item.id,t.skill_version_id,undefined,t.research_run_id)}><strong>{t.question}</strong><span>{({queued:'排队中',running:'测试中',succeeded:'已完成',failed:'测试失败',cancelled:'已停止'} as Record<string,string>)[t.state]||t.state}</span>{t.error&&<small>{t.error}</small>}</button>)}</details>}
      {!!item?.versions?.length&&<section><h3>版本与历史</h3><select aria-label="配置历史版本" value={version} onChange={e=>setVersion(e.target.value)}>{item.versions.map(v=><option key={v.id} value={v.id}>v{v.number}{v.id===item.active_version?' · 当前默认':''}{v.revoked?' · 已撤销':''}</option>)}</select>
        {chosen&&<><p className="scope-note">内容校验 {chosen.sha256.slice(0,16)}… · {chosen.id}</p><div className="kb-toolbar">
          <button className="button-secondary" disabled={busy||chosen.revoked||dirty} onClick={()=>void act(()=>command('activate',{version_id:chosen.id}))}>设为默认版本</button>
          <button className="button-secondary" disabled={busy} onClick={()=>{if(leave()){setDraft({...chosen.content,edit_version:item.edit_version});setFiles(JSON.stringify(chosen.content.files,null,2));setDirty(true);}}}>从此版本编辑草稿</button>
          {kind==='skills'&&<><button className="button-secondary" disabled={chosen.revoked||dirty} onClick={()=>onTest(item.id,chosen.id)}>用文献测试此版本</button>
            <button className="button-secondary" disabled={busy||chosen.revoked} onClick={()=>void act(async()=>{const response=await window.desktop.request({path:`/api/knowledge/skills/${item.id}/versions/${chosen.id}/export`});if(response.status!==200||!(response.body instanceof ArrayBuffer))throw new Error('技能包导出失败');await window.desktop.saveFile({filename:item.slug+'-v'+chosen.number+'.zip',data:response.body});})}>导出技能包</button></>}
          <button className="button-quiet" disabled={busy||chosen.revoked} onClick={()=>{if(window.confirm('撤销后将停止使用此版本的运行，并禁止后续调用。继续？'))void act(()=>command(`versions/${chosen.id}/revoke`,{}));}}>紧急撤销版本</button></div>
          <details><summary>历史版本与当前草稿对照</summary><div className="kb-version-diff"><div><h4>所选历史版本</h4><pre>{JSON.stringify(chosen.content,null,2)}</pre></div><div><h4>当前草稿</h4><pre>{JSON.stringify(draft,null,2)}{'\n参考文件草稿：\n'+files}</pre></div></div></details></>}
      </section>}
    </div>}</div>
  </section>;
}
