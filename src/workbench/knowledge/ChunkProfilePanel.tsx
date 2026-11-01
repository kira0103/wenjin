import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import type {Chunking,ChunkAnalysis} from './pipelineTypes';
import ChunkStrategyFields,{StrategyAnalysis} from './ChunkStrategyFields';

type Config=Chunking;
type Preview={analysis?:ChunkAnalysis;revision_id:string;config:Config;chunks:{block_id:string;page_index:number;kind:string;section_path:string;start:number;end:number;body:string}[];total:number;truncated:boolean;warnings:string[]};

export default function ChunkProfilePanel({editionId,revision,onApplied}:{editionId:string;revision:string|null;onApplied():Promise<void>}) {
  const [config,setConfig]=useState<Config>({profile:'legacy',mode:'recursive',size:100,overlap:15,parent_size:2000,separator:'\n\n',cleaning:{remove_control_characters:false,normalize_whitespace:false,remove_urls:false,remove_emails:false,deduplicate_lines:false}}),[preview,setPreview]=useState<Preview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const epoch=useRef(0);
  useEffect(()=>{epoch.current++;setPreview(null);setMessage('');setError('');setBusy(false);return()=>{epoch.current++;};},[editionId,revision]);
  function change(next:Config){epoch.current++;setConfig(next);setPreview(null);setMessage('');}
  async function inspect(current=false){const ticket=++epoch.current;setBusy(true);setError('');setPreview(null);try{const v=await request<Preview>(`/api/knowledge/editions/${editionId}/chunk-preview`,'POST',{expected_revision:revision,...(current?{}:{config})});if(ticket===epoch.current){setPreview(v);if(current){const {profile,mode,size,overlap,parent_size,separator,cleaning,parent_mode,child_mode,semantic_threshold,semantic_min_characters,semantic_window,semantic_budget}=v.config;setConfig({profile,mode,size,overlap,parent_size,separator,cleaning,parent_mode,child_mode,semantic_threshold,semantic_min_characters,semantic_window,semantic_budget});}}}catch(e){if(ticket===epoch.current)setError(e instanceof Error?e.message:'预览失败');}finally{if(ticket===epoch.current)setBusy(false);}}
  async function apply(){if(!preview)return;setBusy(true);setError('');try{await request(`/api/knowledge/editions/${editionId}/reindex`,'POST',{expected_revision:preview.revision_id,config});setPreview(null);setMessage('已加入索引任务。完成后使用新配置，原文修订和旧引用保留。');await onApplied();}catch(e){setError(e instanceof Error?e.message:'建立索引失败');}finally{setBusy(false);}}
  return <details className="kb-quality"><summary>检索分块预览与配置</summary><p>配置只调整检索单元。先查看原文分块，再采用；复杂版面仍需核对原文。</p>
    <ChunkStrategyFields prefix="文献" value={config} onChange={change} disabled={busy}/><div className="kb-toolbar"><button className="button-secondary" disabled={busy||!revision} onClick={()=>void inspect(true)}>读取当前分块规则</button><button className="button-secondary" disabled={busy||!revision} onClick={()=>void inspect()}>预览分块</button></div>{error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {preview&&<>{preview.analysis&&<StrategyAnalysis value={preview.analysis}/>}<p>共 {preview.total} 块{preview.truncated?'，展示前 200 块':''}。{preview.warnings.join(' ')}</p><div className="kb-chunk-preview">{preview.chunks.map((c,i)=><article key={`${c.block_id}-${c.start}`}><small>块 {i+1} · 第 {c.page_index} 页 / 段 · {c.section_path} · 原文字符 {c.start}–{c.end}</small><pre>{c.body}</pre></article>)}</div><button className="button-primary" disabled={busy} onClick={()=>void apply()}>采用此配置并重建索引</button></>}
  </details>;
}
