import { workspaceStorage as localStorage } from '../storage';
import { readQuestion } from './assistantDrafts';
import { useEffect, useState } from 'react';
import { request } from '../api';
import LibraryScopePicker,{hasLibraryScope} from './LibraryScopePicker';
import type { LibraryScope } from './documentTypes';
import MarkdownPreview from './MarkdownPreview';
import RetrievalDiagnostics from './RetrievalDiagnostics';
import CollaborationResearch from './CollaborationResearch';
import type { AssistantDefinition } from './AssistantManagement';

type Edition = {id:string;title:string;label:string};
type Source = {evidence_id:string;anchor_id:string;quote:string;locator:{title:string;edition_label:string;asset_id:string;page_index:number};context_role:string};
type Run = {id:string;job_id:string;question:string;state:string;stage:string;progress:number;error:string;answer:string;verified:boolean;created_at:string;
  warnings?:string[];evidence?:Source[];usage?:{calls?:number;reserved_tokens?:number;provider_total_tokens?:number;provider_reported_calls?:number;elapsed_ms?:number};
  steps?:{sequence:number;action:string;arguments:Record<string,unknown>;summary:string;state:string;retrieval_run_id?:string}[]};
const labels:Record<string,string>={queued:'排队中',running:'研究中',succeeded:'已完成',cancelled:'已停止',failed:'研究失败',
  research_plan:'确定查证步骤',research_search:'检索原文',research_read:'阅读原文',research_write:'整理研究草稿',starting:'准备研究',complete:'完成',
  model:'请求模型',search:'检索',read:'阅读',list:'查找文献',finish:'结束补查',completed:'已完成',interrupted:'已中断',skills:'加载角色与研究方法'};

type Props={embedded?:boolean;editions:Edition[];projectId:string;onOpenSource(id:string,asset?:string,page?:number):Promise<void>;onSaveNote(question:string,body:string,sources:Source[],run?:{kind:'research'|'collaboration';id:string}):void;onSettings():void;
  initialQuery?:string;initialEdition?:string;initialFilter?:LibraryScope;trial?:{id:string;version:string;editVersion?:number;runId?:string}|null;onClearTrial?():void};
export default function ResearchAssistant(props:Props){
  const [mode,setMode]=useState<'single'|'team'>('single');
  useEffect(()=>{if(props.trial)setMode('single');},[props.trial]);
  return <><div className="kb-toolbar"><button className={mode==='single'?'button-primary':'button-secondary'} onClick={()=>setMode('single')}>单助手研究</button><button className={mode==='team'?'button-primary':'button-secondary'} onClick={()=>{props.onClearTrial?.();setMode('team');}}>协同研究</button></div>
    {mode==='team'?<CollaborationResearch {...props}/>:<SingleResearchAssistant {...props}/>}</>;
}

function SingleResearchAssistant({editions,projectId,onOpenSource,onSaveNote,onSettings,trial,onClearTrial,initialQuery,initialEdition,initialFilter,embedded}:Props) {
  const [query,setQuery]=useState(initialQuery||((embedded&&readQuestion('assistant-question-'+projectId))||'')),[scope,setScope]=useState(embedded?'project':'edition'),[edition,setEdition]=useState(initialEdition||editions[0]?.id||'');
  const [libraryFilter,setLibraryFilter]=useState<LibraryScope>(initialFilter||{});
  const [rounds,setRounds]=useState(8),[budget,setBudget]=useState(60000),[task,setTask]=useState('answer');
  const [runs,setRuns]=useState<Run[]>([]),[selected,setSelected]=useState(''),[run,setRun]=useState<Run|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[trace,setTrace]=useState<string|null>(null);
  const [agents,setAgents]=useState<AssistantDefinition[]>([]),[agent,setAgent]=useState(''),[trialName,setTrialName]=useState('');
  useEffect(()=>{if(initialQuery!==undefined)setQuery(initialQuery);if(initialFilter)setLibraryFilter(initialFilter);if(hasLibraryScope(initialFilter)){setScope('library');if(initialEdition)setEdition(initialEdition);}else if(initialEdition){setEdition(initialEdition);setScope('edition');}else if(initialFilter){setScope(initialFilter.project_id?'project':'all');}},[initialQuery,initialEdition,initialFilter]);
  useEffect(()=>{let alive=true;void request<AssistantDefinition[]>('/api/knowledge/agents').then(v=>{if(alive)setAgents(v);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[]);
  useEffect(()=>{let alive=true;if(trial){if(trial.runId)setSelected(trial.runId);void request<AssistantDefinition>('/api/knowledge/skills/'+trial.id).then(v=>{if(alive)setTrialName(v.draft.name);}).catch(e=>{if(alive)setError(e.message);});}return()=>{alive=false;};},[trial]);
  useEffect(()=>{if(embedded)try{localStorage.setItem('assistant-question-'+projectId,query);}catch{}},[embedded,projectId,query]);
  const active=run?.state==='running'||run?.state==='queued';
  useEffect(()=>{let alive=true;const refresh=async()=>{try{const list=await request<Run[]>('/api/knowledge/research-runs'+(embedded?'?project_id='+encodeURIComponent(projectId):''));if(alive){setRuns(list);if(!selected&&list[0])setSelected(list[0].id);}
    if(selected){const detail=await request<Run>('/api/knowledge/research-runs/'+selected);if(alive)setRun(detail);}
  }catch(e){if(alive)setError(e instanceof Error?e.message:'研究过程无法读取');}};void refresh();const timer=setInterval(()=>void refresh(),2000);
    return()=>{alive=false;clearInterval(timer);};},[selected]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'研究请求未完成');}finally{setBusy(false);}}
  async function start(){const value=await request<Run>(trial?`/api/knowledge/skills/${trial.id}/tests`:'/api/knowledge/research-runs','POST',{query,mode:'hybrid',task,source_kinds:['document'],request_key:crypto.randomUUID(),context_project_id:embedded?projectId:null,
    scope:{...(scope==='library'?libraryFilter:{}),include_core:true,edition_ids:scope==='edition'?[edition]:[],project_id:scope==='project'?projectId:null},max_rounds:rounds,token_budget:budget,
    ...(trial?{version_id:trial.version||null,edit_version:trial.editVersion||0}:{agent_version_id:agent||null})});
    setRun(value);setSelected(value.id);setTrace(null);}
  async function open(source:Source){const value=await request<{edition_id:string}>('/api/knowledge/anchors/'+source.anchor_id);
    await onOpenSource(value.edition_id,source.locator.asset_id,source.locator.page_index);}
  return <section className="kb-research" aria-label="研究助手">
    {!embedded&&<><h2>研究助手</h2><p>提出研究问题，助手会在选定文献中检索、阅读并补查证据，保存带出处的研究草稿。</p></>}
    {trial?<p className="scope-note">技能测试：{trialName} · {trial.version?'固定历史版本':'已保存草稿'}。请选择样本文献和研究问题。<button className="button-quiet" onClick={onClearTrial}>退出技能测试</button></p>:
      <label>研究角色<select aria-label="单助手角色" value={agent} onChange={e=>setAgent(e.target.value)}><option value="">通用研究助手</option>{agents.filter(a=>a.enabled&&a.active_version).map(a=><option key={a.id} value={a.active_version!}>{a.draft.name} · v{a.active_number}</option>)}</select></label>}
    <form onSubmit={e=>{e.preventDefault();void act(start);}}>
      <textarea aria-label="研究问题" required maxLength={1000} value={query} onChange={e=>setQuery(e.target.value)} placeholder="例如：书中“望云”条引用了哪些典籍？请对照原文解释，并指出证据不足之处。"/>
      <div className="kb-toolbar"><label>资料范围 <select aria-label="研究资料范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="library">按分类、目录或文件选择</option><option value="edition">指定版本</option><option value="project">当前项目</option><option value="all">全部文献</option></select></label>
        {scope==='library'&&<LibraryScopePicker value={libraryFilter} onChange={setLibraryFilter}/>}{scope==='edition'&&<select aria-label="研究指定版本" required value={edition} onChange={e=>setEdition(e.target.value)}><option value="">选择版本</option>{editions.map(e=><option key={e.id} value={e.id}>{e.title} · {e.label}</option>)}</select>}
        <select aria-label="研究成果类型" value={task} onChange={e=>setTask(e.target.value)}><option value="answer">研究问答</option><option value="outline">带出处提纲</option><option value="paragraph">段落草稿</option><option value="overview">资料概述</option></select>
        <button className="button-primary" disabled={busy||!query.trim()||(scope==='edition'&&!edition)||(scope==='library'&&!hasLibraryScope(libraryFilter))} type="submit">开始研究</button><button className="button-quiet" type="button" onClick={onSettings}>模型与设置</button></div>
      <details className="kb-quality"><summary>研究预算</summary><div className="kb-toolbar"><label>补查轮次上限 <input aria-label="研究轮次上限" type="number" min={2} max={12} value={rounds} onChange={e=>setRounds(Number(e.target.value))}/></label>
        <label>输入与输出保守预算 <input aria-label="研究用量预算" type="number" min={8000} max={120000} step={1000} value={budget} onChange={e=>setBudget(Number(e.target.value))}/></label></div>
        <p>一次研究会多次调用您配置的问答模型；云端模型会接收问题和用于查证的原文片段。预算按 UTF-8 字节和输出上限保守计算，实际计费以服务商为准。</p></details>
    </form>
    {error&&<p className="persistent-error" role="alert">{error}</p>}
    <div className="kb-toolbar"><label>研究记录 <select aria-label="选择研究记录" value={selected} onChange={e=>{setSelected(e.target.value);setTrace(null);}}><option value="">选择记录</option>{runs.map(r=><option key={r.id} value={r.id}>{r.question.slice(0,50)} · {labels[r.state]||r.state}</option>)}</select></label></div>
    {run&&<><div className="kb-card"><div><strong>{run.question}</strong><p role="status">{labels[run.state]||run.state} · {labels[run.stage]||run.stage} · {run.progress}%</p>{run.error&&<p className="persistent-error">{run.error}</p>}</div>
      {active?<button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>setRun(await request<Run>(`/api/knowledge/research-runs/${run.id}/cancel`,'POST')))}>停止研究</button>:
        <button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>{const next=await request<Run>(`/api/knowledge/research-runs/${run.id}/retry`,'POST');setRun(next);setSelected(next.id);})}>重新研究</button>}</div>
      <details className="kb-quality" open={active}><summary>研究过程 · {run.steps?.length||0} 条记录</summary><ol className="kb-research-steps">{run.steps?.map(s=><li key={s.sequence}><strong>{labels[s.action]||s.action} · {labels[s.state]||s.state}</strong><p>{s.summary}</p>
        {s.arguments.query!==undefined&&<small>查询：{String(s.arguments.query)}</small>}{s.retrieval_run_id&&<button className="button-quiet" onClick={()=>setTrace(s.retrieval_run_id!)}>查看本次检索依据</button>}</li>)}</ol></details>
      <RetrievalDiagnostics runId={trace}/>{run.warnings?.map(w=><p className="scope-note" key={w}>{w}</p>)}
      {run.state==='succeeded'&&run.verified&&<div className="kb-editor"><h3>研究草稿</h3><MarkdownPreview text={run.answer}/><p className="scope-note">已核验引用格式与原文摘录；论断是否得到支持仍需人工复核。</p>
        <button className="button-secondary" disabled={!run.evidence?.length} onClick={()=>onSaveNote(run.question,run.answer,run.evidence||[],{kind:'research',id:run.id})}>保存到项目成果</button></div>}
      {!!run.evidence?.length&&<><h3>已收集原文证据</h3>{run.evidence.map(s=><blockquote className="kb-research-evidence" key={s.evidence_id}><strong>[{s.evidence_id}] {s.locator.title} · {s.locator.edition_label} · 第 {s.locator.page_index} 页 / 段</strong><pre>{s.quote}</pre><button className="button-quiet" onClick={()=>void act(()=>open(s))}>核验原文</button></blockquote>)}</>}
      {run.usage&&<p className="scope-note">模型请求 {run.usage.calls||0} 次 · 已预留预算 {run.usage.reserved_tokens||0} · {run.usage.provider_reported_calls?`服务商已返回 ${run.usage.provider_reported_calls} 次请求的用量，共 ${run.usage.provider_total_tokens||0} token`:'服务商尚未返回用量'}</p>}
    </>}
  </section>;
}
