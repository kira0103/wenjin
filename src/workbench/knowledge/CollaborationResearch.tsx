import { readQuestion } from './assistantDrafts';
import { useEffect, useState } from 'react';
import { request } from '../api';
import type { AssistantDefinition } from './AssistantManagement';
import LibraryScopePicker,{hasLibraryScope} from './LibraryScopePicker';
import type { CategoryFilter } from './LiteratureCategories';
import MarkdownPreview from './MarkdownPreview';

type Source={evidence_id:string;anchor_id:string;quote:string;locator:{title:string;edition_label:string;asset_id:string;page_index:number};context_role:string};
type Node={id:string;node_key:string;title:string;state:string;dependencies:string[];research_run_id:string|null;answer:string;evidence:Source[];error:string;message:string;specification:{snapshot:{assistant:{agent:{name:string};skills:{name:string}[]}}}};
type Team={id:string;question:string;state:string;outcome:string;progress:number;error:string;answer:string;evidence:Source[];warnings:string[];nodes:Node[];usage:{calls:number;reserved_tokens:number;provider_total_tokens:number;provider_reported_calls:number}};
const labels:Record<string,string>={pending:'等待依赖',queued:'排队中',running:'研究中',succeeded:'已完成',failed:'执行失败',cancelled:'已停止',blocked:'上游未完成',skipped:'已跳过',partial:'部分完成',complete:'完整完成',completed:'已完成',interrupted:'已中断'};
const roles=[['classic','古籍证据助手','classic-researcher'],['paper','论文分析助手','paper-researcher'],['general','其他文献助手','research-coordinator'],['review','证据复核助手','evidence-reviewer'],['writer','综合写作助手','research-writer']];

export default function CollaborationResearch({editions,projectId,onOpenSource,onSaveNote,onSettings,initialQuery,initialEdition,initialFilter,embedded}:{embedded?:boolean;editions:{id:string;title:string;label:string}[];projectId:string;initialQuery?:string;initialEdition?:string;initialFilter?:CategoryFilter;
  onOpenSource(id:string,asset?:string,page?:number):Promise<void>;onSaveNote(question:string,body:string,sources:Source[],run?:{kind:'research'|'collaboration';id:string}):void;onSettings():void}) {
  const [query,setQuery]=useState(initialQuery||((embedded&&readQuestion('assistant-team-question-'+projectId))||'')),[scope,setScope]=useState(embedded?'project':'edition'),[edition,setEdition]=useState(editions[0]?.id||''),[workflow,setWorkflow]=useState('compare');
  const [libraryFilter,setLibraryFilter]=useState<CategoryFilter>(initialFilter||{});
  const [budget,setBudget]=useState(180000),[rounds,setRounds]=useState(6),[agents,setAgents]=useState<AssistantDefinition[]>([]),[bindings,setBindings]=useState<Record<string,string>>({});
  const [parallel,setParallel]=useState(2);
  useEffect(()=>{if(initialQuery!==undefined)setQuery(initialQuery);if(initialFilter)setLibraryFilter(initialFilter);if(hasLibraryScope(initialFilter)){setScope('library');if(initialEdition)setEdition(initialEdition);}else if(initialEdition){setEdition(initialEdition);setScope('edition');}else if(initialFilter){setScope(initialFilter.project_id?'project':'all');}},[initialQuery,initialEdition,initialFilter]);
  const [teams,setTeams]=useState<Team[]>([]),[selected,setSelected]=useState(''),[team,setTeam]=useState<Team|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [stepDetails,setStepDetails]=useState<Record<string,{steps:{sequence:number;action:string;state:string;summary:string}[]}>>({});
  useEffect(()=>{if(embedded)try{localStorage.setItem('assistant-team-question-'+projectId,query);}catch{}},[embedded,projectId,query]);
  const active=team?.state==='queued'||team?.state==='running';
  useEffect(()=>{let alive=true;void request<AssistantDefinition[]>('/api/knowledge/agents').then(v=>{if(alive)setAgents(v);}).catch(e=>{if(alive)setError(String(e.message));});return()=>{alive=false;};},[]);
  useEffect(()=>{let alive=true;const refresh=async()=>{try{const list=await request<Team[]>('/api/knowledge/collaborations'+(embedded?'?project_id='+encodeURIComponent(projectId):''));if(alive){setTeams(list);if(!selected&&list[0])setSelected(list[0].id);}if(selected){const value=await request<Team>('/api/knowledge/collaborations/'+selected);if(alive)setTeam(value);}}catch(e){if(alive)setError(e instanceof Error?e.message:'协作记录无法读取');}};
    void refresh();const timer=setInterval(()=>void refresh(),1500);return()=>{alive=false;clearInterval(timer);};},[selected]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'协作操作未完成');}finally{setBusy(false);}}
  function choose(value:Team){setSelected(value.id);setTeam(value);setStepDetails({});}
  return <section className="kb-research" aria-label="协同研究">{!embedded&&<><h2>协同研究</h2><p>专业助手独立搜集证据，再交给复核与写作助手。所有角色使用同一个文献库，研究结果保留原文引用。</p></>}
    <form onSubmit={e=>{e.preventDefault();void act(async()=>choose(await request<Team>('/api/knowledge/collaborations','POST',{
      query,workflow,mode:'hybrid',source_kinds:['document'],request_key:crypto.randomUUID(),context_project_id:embedded?projectId:null,scope:{...(scope==='library'?libraryFilter:{}),include_core:true,edition_ids:scope==='edition'?[edition]:[],project_id:scope==='project'?projectId:null},
      max_rounds:rounds,token_budget:budget,role_versions:bindings,max_parallel:parallel})));}}>
      <textarea aria-label="协作研究问题" required maxLength={1000} value={query} onChange={e=>setQuery(e.target.value)} placeholder="例如：比较所选古籍的制度记载与论文解释，指出各自依据、分歧和资料不足。"/>
      <div className="kb-toolbar"><label>协作流程<select aria-label="协作流程" value={workflow} onChange={e=>setWorkflow(e.target.value)}><option value="compare">古籍与论文比较</option><option value="review">证据搜集、复核与写作</option></select></label>
        <label>资料范围<select aria-label="协作资料范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="library">按分类、目录或文件选择</option><option value="edition">指定版本</option><option value="project">当前项目</option><option value="all">全部文献</option></select></label>
        {scope==='library'&&<LibraryScopePicker value={libraryFilter} onChange={setLibraryFilter}/>}{scope==='edition'&&<select aria-label="协作指定版本" required value={edition} onChange={e=>setEdition(e.target.value)}><option value="">选择版本</option>{editions.map(v=><option key={v.id} value={v.id}>{v.title} · {v.label}</option>)}</select>}
        <button className="button-primary" disabled={busy||!query.trim()||(scope==='edition'&&!edition)||(scope==='library'&&!hasLibraryScope(libraryFilter))}>开始协同研究</button><button className="button-quiet" type="button" onClick={onSettings}>模型与设置</button></div>
      <details className="kb-quality"><summary>角色与总预算</summary><p>古籍与论文按文献类型分工，其他类型单独搜集；复核和写作按依赖执行。默认最多并行两个只读研究任务。</p>
        <div className="kb-agent-choices">{roles.map(([key,title,slug])=><label key={key}>{title}<select aria-label={title+'配置'} value={bindings[key]||''} onChange={e=>setBindings(v=>{const next={...v};if(e.target.value)next[key]=e.target.value;else delete next[key];return next;})}><option value="">默认：{agents.find(v=>v.slug===slug)?.draft.name||title}</option>{agents.filter(v=>v.enabled&&v.active_version).map(v=><option key={v.id} value={v.active_version!}>{v.draft.name} · v{v.active_number}</option>)}</select></label>)}</div>
        <div className="kb-toolbar"><label>同时执行的子任务<select aria-label="协作并行上限" value={parallel} onChange={e=>setParallel(Number(e.target.value))}><option value={1}>1 个（适合单并发模型）</option><option value={2}>2 个</option></select></label><label>各搜集节点轮次上限<input aria-label="协作轮次上限" type="number" min={2} max={8} value={rounds} onChange={e=>setRounds(Number(e.target.value))}/></label>
          <label>全任务保守预算<input aria-label="协作总预算" type="number" min={40000} max={600000} step={10000} value={budget} onChange={e=>setBudget(Number(e.target.value))}/></label></div>
        <p>预算在子任务之间预先分配，重试继续计入。云端模型会接收研究问题及所用原文；实际计费以服务商为准。</p></details>
    </form>
    {error&&<p className="persistent-error" role="alert">{error}</p>}
    <label>协作历史<select aria-label="选择协作记录" value={selected} onChange={e=>{setSelected(e.target.value);setStepDetails({});}}><option value="">选择记录</option>{teams.map(t=><option key={t.id} value={t.id}>{t.question.slice(0,50)} · {labels[t.state]||t.state}</option>)}</select></label>
    {team&&<><div className="kb-card"><div><strong>{team.question}</strong><p role="status">{labels[team.state]||team.state} · {team.progress}% {team.outcome?' · '+(labels[team.outcome]||team.outcome):''}</p>{team.error&&<p className="persistent-error">{team.error}</p>}</div>
      {active?<button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>choose(await request<Team>(`/api/knowledge/collaborations/${team.id}/cancel`,'POST')))}>停止协作</button>:
        <button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>choose(await request<Team>(`/api/knowledge/collaborations/${team.id}/retry`,'POST')))}>重新协同研究</button>}</div>
      <div className="kb-collaboration-graph" aria-label="协作任务关系">{team.nodes.map(n=><article className={'kb-node kb-node-'+n.state} key={n.id}><strong>{n.title}</strong><span>{labels[n.state]||n.state}</span><small>{n.dependencies.length?'等待：'+n.dependencies.map(k=>team.nodes.find(v=>v.node_key===k)?.title||k).join('、'):'可独立执行'}</small><small>角色：{n.specification.snapshot.assistant.agent?.name}</small><small>技能：{n.specification.snapshot.assistant.skills.map(s=>s.name).join('、')||'未绑定'}</small>
        {(n.error||n.message)&&<p className="scope-note">{n.error||n.message}</p>}
        {n.research_run_id&&<button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>{const value=await request<{steps:{sequence:number;action:string;state:string;summary:string}[]}>(`/api/knowledge/research-runs/${n.research_run_id}`);setStepDetails(v=>({...v,[n.id]:value}));})}>查看执行步骤</button>}
        {stepDetails[n.id]&&<ol>{stepDetails[n.id].steps.map(s=><li key={s.sequence}>{s.summary} · {labels[s.state]||s.state}</li>)}</ol>}
        {n.answer&&<details><summary>此助手的成果</summary><MarkdownPreview text={n.answer}/></details>}
        {!active&&<button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>choose(await request<Team>(`/api/knowledge/collaborations/${team.id}/nodes/${n.id}/retry`,'POST')))}>重试此分支及后续任务</button>}
      </article>)}</div>
      {team.warnings.map(w=><p className="scope-note" key={w}>{w}</p>)}
      {team.state==='succeeded'&&<div className="kb-editor"><h3>协同研究草稿</h3><MarkdownPreview text={team.answer}/><p className="scope-note">引用格式与原文已核验；模型复核意见和研究论断仍需人工审核。</p><button className="button-secondary" disabled={!team.evidence.length} onClick={()=>onSaveNote(team.question,team.answer,team.evidence,{kind:'collaboration',id:team.id})}>保存到项目成果</button></div>}
      {team.evidence.map(source=><blockquote className="kb-research-evidence" key={source.evidence_id}><strong>[{source.evidence_id}] {source.locator.title} · {source.locator.edition_label} · 第 {source.locator.page_index} 页 / 段</strong><pre>{source.quote}</pre><button className="button-quiet" onClick={()=>void act(async()=>{const value=await request<{edition_id:string}>('/api/knowledge/anchors/'+source.anchor_id);await onOpenSource(value.edition_id,source.locator.asset_id,source.locator.page_index);})}>核验原文</button></blockquote>)}
      <p className="scope-note">累计模型请求 {team.usage.calls} 次 · 已预留预算 {team.usage.reserved_tokens} · 服务商返回用量 {team.usage.provider_total_tokens} token（{team.usage.provider_reported_calls} 次请求）</p>
    </>}
  </section>;
}
