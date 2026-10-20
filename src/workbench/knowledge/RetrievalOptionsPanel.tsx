import { useEffect, useState } from 'react';
import { request } from '../api';

type Options={rerank_enabled:boolean;rerank_candidates:number;rerank_min_score:number|null;parent_enabled:boolean;parent_characters:number;neighbor_blocks:number};
type Model={ready:boolean;model:string;model_key:string|null;download_megabytes:number};
type Settings={version:number;options:Options;model:Model};
type Job={id:string;state:string;stage:string;error:string};

export default function RetrievalOptionsPanel({onSaved}:{onSaved():void}) {
  const [settings,setSettings]=useState<Settings|null>(null),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false);
  const [job,setJob]=useState<Job|null>(null),[error,setError]=useState(''),[message,setMessage]=useState('');
  useEffect(()=>{let live=true;void request<Settings>('/api/knowledge/retrieval-settings').then(v=>{if(live)setSettings(v);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  useEffect(()=>{if(!job||!['queued','running'].includes(job.state))return;let live=true;const timer=setInterval(()=>{void request<Job>(`/api/knowledge/jobs/${job.id}`).then(async value=>{if(!live)return;setJob(value);if(value.state==='succeeded'){const current=await request<Settings>('/api/knowledge/retrieval-settings');if(live){setSettings(s=>s?{...s,model:current.model}:current);setMessage('本地模型已准备。勾选重排并保存配置后生效。');}}else if(value.state==='failed')setError(value.error);}).catch(e=>{if(live)setError(e.message);});},1500);return()=>{live=false;clearInterval(timer);};},[job?.id,job?.state]);
  function change(value:Partial<Options>){if(settings)setSettings({...settings,options:{...settings.options,...value}});setDirty(true);setError('');setMessage('');}
  async function save(){if(!settings)return;setBusy(true);setError('');try{const v=await request<Settings>('/api/knowledge/retrieval-settings','PUT',{version:settings.version,options:settings.options});setSettings(v);setDirty(false);setMessage('已保存。后续检索与新评测使用此配置。');onSaved();}catch(e){setError(e instanceof Error?e.message:'保存失败');}finally{setBusy(false);}}
  async function prepare(){setBusy(true);setError('');try{setJob(await request<Job>('/api/knowledge/rerank-model/prepare','POST'));setMessage('正在准备公开模型权重，文献不会发送到下载服务。可在处理任务中查看状态。');}catch(e){setError(e instanceof Error?e.message:'模型准备失败');}finally{setBusy(false);}}
  return <details className="kb-quality"><summary>重排模型与父子上下文</summary>{error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}{settings&&<>
    <p>本地模型：BGE 多语言重排 INT8 · {settings.model.ready?'已就绪':'尚未准备'}。首次下载约 {settings.model.download_megabytes} MB；推理在本机执行。</p>
    <fieldset disabled={busy} style={{border:0,padding:0}}><div className="kb-toolbar"><label className="check-label"><input aria-label="启用本地重排" type="checkbox" checked={settings.options.rerank_enabled} onChange={e=>change({rerank_enabled:e.target.checked})}/>启用本地重排</label><label>候选数 <input aria-label="重排候选数" type="number" min={5} max={80} value={settings.options.rerank_candidates} onChange={e=>change({rerank_candidates:Number(e.target.value)})}/></label><label>最低原始分值（可留空） <input aria-label="重排最低分值" type="number" min={-100} max={100} step="any" value={settings.options.rerank_min_score??''} onChange={e=>change({rerank_min_score:e.target.value===''?null:Number(e.target.value)})}/></label><button className="button-secondary" disabled={!!job&&['queued','running'].includes(job.state)} onClick={()=>void prepare()}>{job&&['queued','running'].includes(job.state)?'正在准备模型…':'准备本地模型'}</button></div>
    <p className="scope-note">重排用于关键词、语义和混合检索。原句检索保持原序。模型失败时回退；成功执行阈值过滤后，低分候选不再进入证据。分值不是概率。</p>
    <div className="kb-toolbar"><label className="check-label"><input aria-label="启用父子上下文" type="checkbox" checked={settings.options.parent_enabled} onChange={e=>change({parent_enabled:e.target.checked})}/>启用父子上下文</label><label>每组字符上限 <input aria-label="父级上下文字符上限" type="number" min={256} max={4000} value={settings.options.parent_characters} onChange={e=>change({parent_characters:Number(e.target.value)})}/></label><label>每侧相邻段落数 <input aria-label="相邻上下文段落数" type="number" min={0} max={4} value={settings.options.neighbor_blocks} onChange={e=>change({neighbor_blocks:Number(e.target.value)})}/></label></div>
    <p className="scope-note">小分块用于命中；父级上下文补充同版、同修订的同类原文，按已识别的章节边界约束扩展。每段分别保留出处，展开仍限定到项目附件与页段，并受回答预算限制。</p>
    <div className="action-row"><button className="button-primary" disabled={!dirty} onClick={()=>void save()}>保存检索配置</button><button className="button-secondary" onClick={()=>{void request<Settings>('/api/knowledge/retrieval-settings').then(v=>{setSettings(v);setDirty(false);setError('');}).catch(e=>setError(e.message));}}>重新读取配置</button></div></fieldset>
  </>}</details>;
}
