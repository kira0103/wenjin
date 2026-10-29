import { platform } from '../platform';
import { useState } from 'react';
import { request } from '../api';
import { Modal } from '../Pages';
import type { SourcePreview } from './documentTypes';

export default function ImportLiterature({categoryName='',onClose,onImported}:{categoryName?:string;onClose():void;onImported(group:string):Promise<void>}){
  const [preview,setPreview]=useState<SourcePreview|null>(null),[name,setName]=useState(categoryName==='未分类'?'':categoryName),[kind,setKind]=useState('article'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function choose(mode:'files'|'directory'){setBusy(true);setError('');try{const value=await platform.chooseLiteratureSource(mode);if(value){setPreview(value);if(value.reuse_existing||value.preview.every(f=>f.relative_path.endsWith('.md')))setKind('ancient');}}catch(e){setError(e instanceof Error?e.message:'文献来源读取失败');}finally{setBusy(false);}}
  async function start(){if(!preview)return;setBusy(true);setError('');try{const result=await request<{group_id:string|null}>(`/api/knowledge/document-imports/${preview.id}/start`,'POST',{category_name:name,item_type:kind});await onImported(result.group_id||'uncategorized');onClose();}catch(e){setError(e instanceof Error?e.message:'导入未完成');}finally{setBusy(false);}}
  return <Modal title="导入文献" onClose={()=>{if(!busy)onClose();}}><p>选择一个或多个文档，也可以导入整个目录。</p><div className="action-row"><button className="button-secondary" disabled={busy} onClick={()=>void choose('files')}>选择文档（可多选）</button><button className="button-secondary" disabled={busy} onClick={()=>void choose('directory')}>选择目录</button></div>
    <label className="form-label">分类名称（可不填）<input aria-label="导入分类名称" maxLength={100} placeholder="未分类，例如：daizhige、类书研究" value={name} onChange={e=>setName(e.target.value)}/></label><label className="form-label">文献类型<select aria-label="导入文献类型" value={kind} onChange={e=>setKind(e.target.value)}><option value="article">论文</option><option value="ancient">古籍</option><option value="thesis">学位论文</option></select></label>
    {preview&&<div className="kb-import-preview"><strong>{preview.source_label} · {preview.files.toLocaleString()} 个文件</strong><p className="scope-note">{preview.mode==='directory'?'保留原始目录层级和文件名。':'所选文档放入同一个分类。'}{preview.ignored?` 跳过 ${preview.ignored} 个不支持的文件。`:''}{preview.reuse_existing?' 已有的古籍资料将直接复用。':''}</p><pre>{preview.preview.map(f=>f.relative_path).join('\n')}{preview.files>preview.preview.length?'\n…':''}</pre></div>}
    <p className="scope-note">分类名称留空时放入“未分类”，后续可修改。入库后在后台解析正文和建立索引。</p>{error&&<p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions"><button className="button-secondary" disabled={busy} onClick={onClose}>取消</button><button className="button-primary" disabled={busy||!preview} onClick={()=>void start()}>{busy?'正在处理…':'确认导入'}</button></div></Modal>;
}
