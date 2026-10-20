import { useEffect, useRef, useState } from 'react';
import { request } from '../api';

type Config={profile:string;size:number;overlap:number};
type Preview={revision_id:string;config:Config;chunks:{block_id:string;page_index:number;kind:string;section_path:string;start:number;end:number;body:string}[];total:number;truncated:boolean;warnings:string[]};

export default function ChunkProfilePanel({editionId,revision,onApplied}:{editionId:string;revision:string|null;onApplied():Promise<void>}) {
  const [config,setConfig]=useState<Config>({profile:'legacy',size:100,overlap:15}),[preview,setPreview]=useState<Preview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const epoch=useRef(0);
  useEffect(()=>{epoch.current++;setPreview(null);setMessage('');setError('');setBusy(false);return()=>{epoch.current++;};},[editionId,revision]);
  function change(next:Config){epoch.current++;setConfig(next);setPreview(null);setMessage('');}
  async function inspect(){const ticket=++epoch.current;setBusy(true);setError('');setPreview(null);try{const v=await request<Preview>(`/api/knowledge/editions/${editionId}/chunk-preview`,'POST',{expected_revision:revision,config});if(ticket===epoch.current)setPreview(v);}catch(e){if(ticket===epoch.current)setError(e instanceof Error?e.message:'预览失败');}finally{if(ticket===epoch.current)setBusy(false);}}
  async function apply(){if(!preview)return;setBusy(true);setError('');try{await request(`/api/knowledge/editions/${editionId}/reindex`,'POST',{expected_revision:preview.revision_id,config:{profile:preview.config.profile,size:preview.config.size,overlap:preview.config.overlap}});setPreview(null);setMessage('已加入索引任务。完成后使用新配置，原文修订和旧引用保留。');await onApplied();}catch(e){setError(e instanceof Error?e.message:'建立索引失败');}finally{setBusy(false);}}
  return <details className="kb-quality"><summary>检索分块预览与配置</summary><p>配置只调整检索单元。先查看原文分块，再采用；复杂版面仍需核对原文。</p>
    <div className="kb-toolbar"><select aria-label="分块配置类型" value={config.profile} disabled={busy} onChange={e=>change({...config,profile:e.target.value})}><option value="legacy">兼容现有分块</option><option value="ancient">古籍句界与卷篇标题</option><option value="paper">论文句界与章节标题</option><option value="general">通用段落与句界</option></select>
      <label>目标字符数 <input aria-label="分块目标字符数" type="number" min={40} max={500} value={config.size} disabled={busy} onChange={e=>change({...config,size:Number(e.target.value)})}/></label>
      <label>重叠字符数 <input aria-label="分块重叠字符数" type="number" min={0} max={100} value={config.overlap} disabled={busy} onChange={e=>change({...config,overlap:Number(e.target.value)})}/></label>
      <button className="button-secondary" disabled={busy||!revision} onClick={()=>void inspect()}>预览分块</button>
    </div>{error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {preview&&<><p>共 {preview.total} 块{preview.truncated?'，展示前 200 块':''}。{preview.warnings.join(' ')}</p><div className="kb-chunk-preview">{preview.chunks.map((c,i)=><article key={`${c.block_id}-${c.start}`}><small>块 {i+1} · 第 {c.page_index} 页 / 段 · {c.section_path} · 原文字符 {c.start}–{c.end}</small><pre>{c.body}</pre></article>)}</div><button className="button-primary" disabled={busy} onClick={()=>void apply()}>采用此配置并重建索引</button></>}
  </details>;
}
