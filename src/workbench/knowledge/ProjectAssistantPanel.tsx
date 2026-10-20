import { useEffect, useState } from 'react';
import { request, type Project } from '../api';
import { Modal } from '../Pages';
import ResearchAssistant from './ResearchAssistant';
import type { LibraryScope } from './documentTypes';

export default function ProjectAssistantPanel({project,draft,onDraftUsed,onOpenSource,onSettings,onChanged}:{project:Project;draft:{query:string;id?:string;filter?:LibraryScope}|null;onDraftUsed():void;onOpenSource(id:string,asset?:string,page?:number):Promise<void>;onSettings():void;onChanged():Promise<void>}){
  const [editions,setEditions]=useState<{id:string;title:string;label:string}[]>([]),[error,setError]=useState(''),[message,setMessage]=useState('');
  const [saving,setSaving]=useState<{title:string;kind:'research'|'collaboration';id:string}|null>(null),[busy,setBusy]=useState(false);
  const [requestedEdition,setRequestedEdition]=useState(draft?.id||'');
  useEffect(()=>{if(draft){if(draft.id)setRequestedEdition(draft.id);onDraftUsed();}},[draft,onDraftUsed]);
  useEffect(()=>{let live=true;void request<{items:typeof editions}>('/api/knowledge/catalog?limit=200').then(v=>{if(live)setEditions(previous=>[...v.items,...previous.filter(p=>!v.items.some(e=>e.id===p.id))]);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  useEffect(()=>{if(!requestedEdition)return;let live=true;void request<(typeof editions)[number]>('/api/knowledge/editions/'+requestedEdition).then(value=>{if(live)setEditions(v=>v.some(e=>e.id===value.id)?v:[value,...v]);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[requestedEdition]);
  return <><div className="project-assistant-content"><div className="assistant-project-context">{project.name}</div>{error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p role="status" className="scope-note">{message}</p>}
    <ResearchAssistant embedded editions={editions} projectId={project.id} initialQuery={draft?.query} initialEdition={draft?.id} initialFilter={draft?.filter} onOpenSource={onOpenSource} onSettings={onSettings} onSaveNote={(title,_body,_sources,run)=>{if(run)setSaving({title,...run});}}/>
  </div>{saving&&<Modal title="保存研究成果" onClose={()=>{if(!busy)setSaving(null);}}><p>{saving.title}</p><p>保存到项目：{project.name}</p>{error&&<p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions"><button className="button-secondary" disabled={busy} onClick={()=>setSaving(null)}>取消</button><button className="button-primary" disabled={busy} onClick={()=>void(async()=>{setBusy(true);setError('');try{await request('/api/research-results','POST',{project_id:project.id,kind:saving.kind,run_id:saving.id});setSaving(null);setMessage('已保存到项目的“工作成果”');await onChanged();}catch(e){setError(e instanceof Error?e.message:'保存失败');}finally{setBusy(false);}})()}>确认保存成果</button></div></Modal>}</>;
}
