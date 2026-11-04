import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { setLibraryAdminApi } from '../workbench/transport';
import KnowledgeWorkbench from '../workbench/knowledge/KnowledgeWorkbench';

export default function SharedLibrary({onRegisterLeave,mode='library',onOpenLibrary,onOpenProcessing,incomingSource,onIncomingUsed}:{mode?:'library'|'retrieval'|'assistants';incomingSource?:{id:string}|null;onIncomingUsed?():void;onOpenLibrary?(id?:string):void;onOpenProcessing?():void;onRegisterLeave(callback:(()=>Promise<boolean>)|null):void}) {
  const [ready,setReady]=useState(false),[maintenance,setMaintenance]=useState(false);
  const leave=useRef<(()=>Promise<boolean>)|null>(null);
  const register=useCallback((callback:(()=>Promise<boolean>)|null)=>{leave.current=callback;onRegisterLeave(callback);},[onRegisterLeave]);
  useLayoutEffect(()=>{setLibraryAdminApi(true);setReady(true);return()=>setLibraryAdminApi(false);},[]);
  const changed=useCallback(async()=>{},[]);
  const maintenanceAction=mode==='library'?<>{onOpenProcessing&&<button className="button-secondary" onClick={onOpenProcessing}>文献加工与向量化</button>}<button className="button-secondary" onClick={()=>void (async()=>{if(!leave.current||await leave.current())setMaintenance(value=>!value);})()}>{maintenance?'返回公共资料目录':'编辑文献书目'}</button></>:null;
  return <section className={`portal-shared-library ${mode!=='library'?'portal-knowledge-settings':''}`}>
    {ready&&<KnowledgeWorkbench incomingSource={mode==='library'?incomingSource:null} onIncomingUsed={onIncomingUsed} onOpenLibrary={onOpenLibrary} libraryActions={maintenanceAction} embedded adminLibrary key={mode+String(maintenance)} section={mode!=='library'||maintenance?'management':'library'} initialTab={mode==='retrieval'?'retrieval-settings':mode==='assistants'?'assistant-settings':maintenance?'unclassified':'files'} projects={[]} projectId="" onChanged={changed} onSettings={()=>{}} onRegisterLeave={register}/>}</section>;
}
