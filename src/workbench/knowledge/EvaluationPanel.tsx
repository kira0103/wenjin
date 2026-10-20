import { useCallback, useEffect, useState } from 'react';
import { request } from '../api';
import RetrievalDiagnostics from './RetrievalDiagnostics';

export type Gold={block_id:string;start:number;end:number;relevance:number};
type Suite={id:string;name:string;case_count:number};
type Report={k:number;evaluated:number;stale:number;scope_leaks:number;metrics:Record<string,number|null>;limitations:string[];cases:{case_id:string;query:string;status:string;retrieval_run_id:string;metrics:Record<string,number|null>|null}[]};
type Detail={id:string;name:string;cases:{id:string;search:{query:string};expected:Gold[];expect_empty:boolean}[];runs:{id:string;state:string;progress:number;error:string;report:Partial<Report>}[]};

export default function EvaluationPanel({search,gold}:{search:Record<string,unknown>|null;gold:Gold[]}) {
  const [suites,setSuites]=useState<Suite[]>([]),[selected,setSelected]=useState(''),[detail,setDetail]=useState<Detail|null>(null),[name,setName]=useState('古籍与论文检索');
  const [expectEmpty,setExpectEmpty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[trace,setTrace]=useState<string|null>(null);
  const load=useCallback(async()=>{const values=await request<Suite[]>('/api/knowledge/evaluation/suites');setSuites(values);},[]);
  useEffect(()=>{void load().catch(e=>setError(e.message));},[load]);
  useEffect(()=>{let live=true;setDetail(null);setTrace(null);if(!selected)return;const refresh=()=>{void request<Detail>(`/api/knowledge/evaluation/suites/${selected}`).then(v=>{if(live)setDetail(v);}).catch(e=>{if(live)setError(e.message);});};refresh();const timer=setInterval(refresh,3000);return()=>{live=false;clearInterval(timer);};},[selected]);
  useEffect(()=>{setExpectEmpty(false);setMessage('');},[search]);
  async function act(action:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await action();await load();if(selected)setDetail(await request<Detail>(`/api/knowledge/evaluation/suites/${selected}`));}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  const percent=(value:number|null|undefined)=>value==null?'未评测':`${(value*100).toFixed(1)}%`;
  return <details className="kb-quality"><summary>检索评测与人工标注</summary><p>在检索结果中勾选应命中的原文，保存为固定修订的金标准。这里评测检索效果，回答的论断仍需人工审核。</p>
    <div className="kb-toolbar"><select aria-label="选择检索评测集" value={selected} disabled={busy} onChange={e=>setSelected(e.target.value)}><option value="">选择评测集</option>{suites.map(s=><option key={s.id} value={s.id}>{s.name} · {s.case_count} 个问题</option>)}</select><input aria-label="新评测集名称" value={name} onChange={e=>setName(e.target.value)}/><button className="button-secondary" disabled={busy||!name.trim()} onClick={()=>void act(async()=>{const v=await request<Suite>('/api/knowledge/evaluation/suites','POST',{name});setSelected(v.id);})}>新建评测集</button></div>
    {search&&<><p>标注问题：{String(search.query)}。使用该次检索保存的范围，后续编辑查询框不会改变这条标注。</p><div className="kb-toolbar"><label className="check-label"><input type="checkbox" checked={expectEmpty} onChange={e=>setExpectEmpty(e.target.checked)}/>应无检索结果</label><span>已选 {gold.length} 段原文</span><button className="button-secondary" disabled={busy||!selected||(!expectEmpty&&!gold.length)} onClick={()=>void act(async()=>{await request(`/api/knowledge/evaluation/suites/${selected}/cases`,'POST',{search:{...search,source_kinds:['document']},expected:expectEmpty?[]:gold,expect_empty:expectEmpty});setMessage('标注已保存。来源更新后会显示过期，不计入平均分。');})}>保存问题与金标准</button></div></>}
    {error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {detail&&<><h3>{detail.name}</h3>{detail.cases.map(c=><div className="kb-toolbar" key={c.id}><span>{c.search.query} · {c.expect_empty?'应无结果':`${c.expected.length} 处原文标注`}</span><button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>{await request(`/api/knowledge/evaluation/suites/${selected}/cases/${c.id}`,'DELETE');})}>移除标注</button></div>)}
      <button className="button-primary" disabled={busy||!detail.cases.length||detail.runs.some(r=>['queued','running'].includes(r.state))} onClick={()=>void act(async()=>{await request(`/api/knowledge/evaluation/suites/${selected}/runs`,'POST');})}>运行评测并保存结果</button>
      {detail.runs.map(run=><article className="kb-evaluation-run" key={run.id}><h4>{run.state==='succeeded'?'评测完成':run.state==='running'?`正在评测 ${run.progress}%`:run.state==='queued'?'排队中':run.state==='cancelled'?'已取消':'评测失败'}</h4>{run.error&&<p role="alert">{run.error}</p>}{run.report.metrics&&<><p>有效 {run.report.evaluated} 个 · 过期 {run.report.stale} 个 · 范围越界 {run.report.scope_leaks} 条</p><p>Recall@10 {percent(run.report.metrics.recall)} · NDCG@10 {percent(run.report.metrics.ndcg)} · MRR@10 {percent(run.report.metrics.mrr)} · 应无结果通过率 {percent(run.report.metrics.negative_pass)}</p>{run.report.limitations?.map(v=><p className="scope-note" key={v}>{v}</p>)}{run.report.cases?.map(c=><div className="kb-toolbar" key={c.case_id}><span>{c.query} · {c.status==='stale'?'原文已变化，标注过期':`Recall ${percent(c.metrics?.recall)}`}</span><button className="button-quiet" onClick={()=>setTrace(c.retrieval_run_id)}>查看检索过程</button></div>)}</>}</article>)}
    </>}
    <RetrievalDiagnostics runId={trace}/>
  </details>;
}
