import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import KnowledgeWorkbench from '../workbench/knowledge/KnowledgeWorkbench';
import { setSystemLibraryApi } from '../workbench/transport';

type Props = React.ComponentProps<typeof KnowledgeWorkbench> & {
  scope:'system'|'personal'; onScope(scope:'system'|'personal'):void;
};

export default function UserLibrary({scope,onScope,onRegisterLeave,...props}:Props) {
  const [active,setActive]=useState('');
  const leave=useRef<(()=>Promise<boolean>)|null>(null);
  const register=useCallback((callback:(()=>Promise<boolean>)|null)=>{leave.current=callback;onRegisterLeave(callback);},[onRegisterLeave]);
  useLayoutEffect(()=>{setSystemLibraryApi(scope==='system');setActive(scope);return()=>setSystemLibraryApi(false);},[scope]);
  async function change(next:'system'|'personal') {if(scope===next)return;if(leave.current&&!await leave.current())return;onScope(next);}
  return <><div className="kb-tabs" role="tablist" aria-label="文献归属"><button role="tab" className={scope==='system'?'kb-tab-active':''} aria-selected={scope==='system'} onClick={()=>void change('system')}>系统文献库</button><button role="tab" className={scope==='personal'?'kb-tab-active':''} aria-selected={scope==='personal'} onClick={()=>void change('personal')}>我的文献库</button></div>{active===scope&&<KnowledgeWorkbench {...props} key={scope} libraryVisibility={scope} readOnlyLibrary={scope==='system'} onRegisterLeave={register}/>}</>;
}
