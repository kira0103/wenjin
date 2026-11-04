import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';

const HeadingToolbar=createContext<{target:HTMLDivElement|null;setTarget:(target:HTMLDivElement|null)=>void}|null>(null);

/** Let a nested feature keep its toolbar state while sharing the page title row. */
export function PageHeadingProvider({children}:{children:ReactNode}) {
  const [target,setTarget]=useState<HTMLDivElement|null>(null);
  const value=useMemo(()=>({target,setTarget}),[target]);
  return <HeadingToolbar.Provider value={value}>{children}</HeadingToolbar.Provider>;
}

export function PageHeadingActions({children}:{children:ReactNode}) {
  const toolbar=useContext(HeadingToolbar);
  return toolbar?.target?createPortal(children,toolbar.target):<>{children}</>;
}

/** A single title/action row. Persistent instructions belong beside their fields. */
export default function PageHeading({title,description,navigation,actions,level=1}: {
  title:ReactNode; description?:ReactNode; navigation?:ReactNode; actions?:ReactNode; level?:1|2;
}) {
  const id=useId();
  const toolbar=useContext(HeadingToolbar);
  const help=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{
    function dismiss(event:PointerEvent) { if(help.current&&!help.current.contains(event.target as Node))help.current.open=false; }
    document.addEventListener('pointerdown',dismiss);
    return ()=>document.removeEventListener('pointerdown',dismiss);
  },[]);
  const Heading=level===1?'h1':'h2';
  return <header className="page-heading">
    <div className="page-heading-title"><Heading>{title}</Heading>{description&&<details ref={help} className="page-heading-help" onKeyDown={event=>{if(event.key==='Escape'&&help.current?.open){help.current.open=false;help.current.querySelector('summary')?.focus();event.preventDefault();}}}><summary aria-label={typeof title==='string'?`${title}页面说明`:'页面说明'} aria-controls={id}><Info size={16}/></summary><div id={id} className="page-heading-description">{description}</div></details>}{navigation}</div>
    {(actions||toolbar)&&<div className="page-heading-actions" ref={toolbar?.setTarget}>{actions}</div>}
  </header>;
}
