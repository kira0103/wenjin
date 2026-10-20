import { useEffect, useState } from 'react';
import { request } from '../api';

type Stage={name:string;elapsed_ms:number;candidates?:number|unknown[];returned?:number;duplicate_candidates?:number;unique_blocks?:number;discarded_by_limit?:number;possibly_truncated?:boolean;outcome?:string;scored_count?:number;discarded_by_cap?:number;discarded_by_threshold?:number;truncated_pairs?:number;model_key?:string};
type Run={id:string;query:string;mode:string;diagnostics:{pipeline:string;outcome:string;elapsed_ms:number;stages:Stage[];answer_outcome?:string;context?:{used?:number;dropped_by_prompt_budget?:number;truncated_blocks?:number}};snapshot:unknown[];results:{block_id?:string;score?:number;channels?:{name:string;rank:number}[]}[]};
const labels:Record<string,string>={scope:'范围与版本',exact:'原句召回',keyword:'关键词召回',normalized_substring:'规范化原句召回',semantic:'语义召回',fusion:'融合与去重',selection:'结果选择',rerank:'模型重排',parent_context:'父级上下文'};
const outcomes:Record<string,string>={disabled:'未启用',exact_bypass:'原句检索保持原序',no_candidates:'无候选',model_not_ready:'模型未就绪，保留原排序',model_error:'推理未完成，保留原排序',completed:'重排完成',below_threshold:'所有候选低于阈值'};

function describe(stage:Stage){
  if(stage.name==='rerank')return `${outcomes[stage.outcome||'']||stage.outcome}${stage.scored_count!==undefined?` · 评分 ${stage.scored_count} 条`:''}${stage.discarded_by_cap?` · ${stage.discarded_by_cap} 条未进入重排`:''}${stage.discarded_by_threshold?` · ${stage.discarded_by_threshold} 条低于阈值`:''}${stage.truncated_pairs?` · ${stage.truncated_pairs} 个输入受 token 限制`:''}`;
  if(stage.name==='parent_context')return `保留 ${stage.returned} 组原文上下文`;
  if(typeof stage.candidates==='number')return `${stage.candidates} 个候选`;
  if(stage.returned!==undefined)return `返回 ${stage.returned} 条`;
  if(stage.duplicate_candidates!==undefined)return `保留 ${stage.unique_blocks} 段，合并 ${stage.duplicate_candidates} 个重复候选`;
  return '记录所选范围';
}

export default function RetrievalDiagnostics({runId}:{runId:string|null}) {
  const [run,setRun]=useState<Run|null>(null),[error,setError]=useState('');
  useEffect(()=>{let live=true;setRun(null);setError('');if(runId)void request<Run>(`/api/knowledge/retrieval-runs/${runId}`).then(v=>{if(live)setRun(v);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[runId]);
  if(!runId)return null;
  return <details className="kb-quality"><summary>查看本次检索依据与处理情况</summary>{error&&<p role="alert">{error}</p>}{!run&&!error&&<p>读取记录…</p>}{run&&<>
    <p>查询：{run.query} · 总耗时 {Math.round(run.diagnostics.elapsed_ms)} ms</p>
    <table><thead><tr><th>步骤</th><th>处理情况</th><th>耗时</th></tr></thead><tbody>{run.diagnostics.stages.map((s,i)=><tr key={i}><td>{labels[s.name]||s.name}</td><td>{describe(s)}{s.possibly_truncated&&'；达到召回上限，结果可能不完整'}{!!s.discarded_by_limit&&`；${s.discarded_by_limit} 条未进入结果`}{s.outcome==='no_index'&&'；无可用向量索引'}</td><td>{Math.round(s.elapsed_ms)} ms</td></tr>)}</tbody></table>
    {run.diagnostics.context?.used!==undefined&&<p>回答使用 {run.diagnostics.context.used} 条证据；{run.diagnostics.context.truncated_blocks||0} 个段落受长度限制；{run.diagnostics.context.dropped_by_prompt_budget||0} 条证据因模型预算移除。</p>}
    <p className="scope-note">原文引用与格式检查不能代替论断审核。{(()=>{const stage=run.diagnostics.stages.find(s=>s.name==='rerank');return stage?`重排：${outcomes[stage.outcome||'']||stage.outcome}。`:'';})()}</p>
    <details><summary>版本、候选分数与技术记录</summary><pre>{JSON.stringify({snapshot:run.snapshot,results:run.results,diagnostics:run.diagnostics},null,2)}</pre></details>
  </>}</details>;
}
