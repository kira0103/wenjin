import {useEffect,useRef,useState} from 'react';
import {request} from '../api';
import {Modal} from '../Pages';
import ChunkStrategyFields,{StrategyAnalysis} from './ChunkStrategyFields';
import type {ChunkAnalysis,Ingestion} from './pipelineTypes';

type Policy={version:number;override:Ingestion|null;processing:Ingestion;default_processing:Ingestion};
type Preview={revision_id:string;analysis:ChunkAnalysis;chunks:{body:string;index_text:string;page_index:number;start:number;end:number;parent_characters:number}[];total:number;notice:string};
export default function DocumentStrategyDialog({document,onClose,onSaved}:{document:{id:string;title:string;active_revision:string|null};onClose():void;onSaved():Promise<void>}){
  const [policy,setPolicy]=useState<Policy|null>(null),[value,setValue]=useState<Ingestion|null>(null),[inherit,setInherit]=useState(true);
  const [busy,setBusy]=useState(false),[preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[message,setMessage]=useState('');
  const epoch=useRef(0);
  useEffect(()=>{let live=true;void request<Policy>(`/api/knowledge/rag-documents/${document.id}/policy`).then(p=>{if(live){setPolicy(p);setValue(p.processing);setInherit(!p.override);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;epoch.current++;};},[document.id]);
  const dirty=!!policy&&(inherit!==!policy.override||!inherit&&JSON.stringify(value)!==JSON.stringify(policy.override));
  const active=inherit?policy?.default_processing:value;
  function close(){if(!busy&&(!dirty||window.confirm('此文档策略尚未保存，放弃修改？')))onClose();}
  function change(next:Ingestion){epoch.current++;setPreview(null);setValue(next);setMessage('');}
  async function inspect(){if(!active)return;const ticket=++epoch.current;setBusy(true);setError('');try{const data=await request<Preview>(`/api/knowledge/rag-documents/${document.id}/preview`,'POST',{expected_revision:document.active_revision,processing:active});if(ticket===epoch.current)setPreview(data);}catch(e){if(ticket===epoch.current)setError(e instanceof Error?e.message:'预览失败');}finally{if(ticket===epoch.current)setBusy(false);}}
  async function save(rebuild=false){if(!policy||!active)return;setBusy(true);setError('');setMessage('');try{
    const saved=await request<Policy>(`/api/knowledge/rag-documents/${document.id}/policy`,'PUT',{version:policy.version,processing:inherit?null:active});
    setPolicy({...policy,...saved});setValue(saved.processing);
    if(rebuild)await request('/api/knowledge/rag-process','POST',{operation:'index',use_document_policies:true,documents:[{id:document.id,revision_id:document.active_revision}]});
    setMessage(rebuild?'策略已保存，后台重建任务已提交。':'此文档策略已保存，将用于后续加工。原有索引保持到重新处理完成。');await onSaved();
  }catch(e){setError(e instanceof Error?e.message:'保存失败');}finally{setBusy(false);}}
  return <Modal title="文档切分策略" onClose={close}><div className="document-strategy-dialog"><p><strong>{document.title}</strong></p><p className="scope-note">单独配置只影响这份文献。预览使用已解析录文，保存后可显式重建索引。</p>
    {error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p className="scope-note" role="status">{message}</p>}
    {!policy||!active?<p>正在读取文档策略…</p>:<><label className="document-strategy-source">策略来源<select aria-label="文档策略来源" disabled={busy} value={inherit?'inherit':'custom'} onChange={e=>{setInherit(e.target.value==='inherit');setPreview(null);epoch.current++;}}><option value="inherit">跟随已保存的全局默认</option><option value="custom">此文档单独配置</option></select></label>
      <ChunkStrategyFields prefix="文档" value={active.chunking} disabled={busy||inherit} onChange={chunking=>change({...active,chunking})}/>
      <div className="modal-actions"><button className="button-secondary" type="button" disabled={busy||!document.active_revision} onClick={()=>void inspect()}>预览此文档切分</button><button className="button-primary" type="button" disabled={busy} onClick={()=>void save(false)}>保存此文档策略</button><button className="button-secondary" type="button" disabled={busy||!document.active_revision} onClick={()=>void save(true)}>保存并重建索引</button></div>
      {preview&&<><StrategyAnalysis value={preview.analysis}/><p className="scope-note">{preview.notice} 共 {preview.total} 个逻辑块，展示前 {preview.chunks.length} 个。</p><div className="document-strategy-preview">{preview.chunks.map((c,n)=><article key={n}><small>块 {n+1} · 第 {c.page_index} 页／段{c.parent_characters?` · 父块 ${c.parent_characters} 字`:''}</small><pre>{c.index_text}</pre></article>)}</div></>}
    </>}<div className="modal-actions"><button className="button-secondary" type="button" disabled={busy} onClick={close}>关闭</button></div>
  </div></Modal>;
}
