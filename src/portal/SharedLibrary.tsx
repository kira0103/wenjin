import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { setLibraryAdminApi } from '../workbench/transport';
import KnowledgeWorkbench from '../workbench/knowledge/KnowledgeWorkbench';

export default function SharedLibrary({onRegisterLeave}:{onRegisterLeave(callback:(()=>Promise<boolean>)|null):void}) {
  const [ready,setReady]=useState(false),[maintenance,setMaintenance]=useState(false);
  const leave=useRef<(()=>Promise<boolean>)|null>(null);
  const register=useCallback((callback:(()=>Promise<boolean>)|null)=>{leave.current=callback;onRegisterLeave(callback);},[onRegisterLeave]);
  useLayoutEffect(()=>{setLibraryAdminApi(true);setReady(true);return()=>setLibraryAdminApi(false);},[]);
  const changed=useCallback(async()=>{},[]);
  return <section className="portal-shared-library"><p className="portal-notice">这里发布的文献、原件与修订向所有研究账号开放。个人项目、笔记和研究成果仍分别保存。</p><div className="portal-actions"><button className="portal-secondary" onClick={()=>void (async()=>{if(!leave.current||await leave.current())setMaintenance(value=>!value);})()}>{maintenance?'返回公共资料目录':'书目与原文维护'}</button></div>{ready&&<KnowledgeWorkbench adminLibrary key={String(maintenance)} section={maintenance?'management':'library'} initialTab={maintenance?'unclassified':'files'} projects={[]} projectId="" onChanged={changed} onSettings={()=>{}} onRegisterLeave={register}/>}</section>;
}
