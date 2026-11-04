import { useEffect, useRef, type ReactNode } from 'react';

export default function JournalSourceDetails({children}:{children:ReactNode}) {
  const ref=useRef<HTMLDetailsElement>(null);
  function place() {
    const panel=ref.current?.querySelector<HTMLElement>('.journal-source-body');
    if(!ref.current?.open||!panel)return;
    panel.style.translate='0';
    const bounds=panel.getBoundingClientRect();
    const shift=bounds.left<12?12-bounds.left:bounds.right>innerWidth-12?innerWidth-12-bounds.right:0;
    panel.style.translate=`${shift}px 0`;
  }
  useEffect(()=>{
    const dismiss=(event:PointerEvent)=>{if(ref.current&&!ref.current.contains(event.target as Node))ref.current.open=false;};
    document.addEventListener('pointerdown',dismiss);
    window.addEventListener('resize',place);
    return ()=>{document.removeEventListener('pointerdown',dismiss);window.removeEventListener('resize',place);};
  },[]);
  return <details ref={ref} className="journal-source-info" onToggle={place} onKeyDown={event=>{if(event.key==='Escape'){event.currentTarget.open=false;event.currentTarget.querySelector('summary')?.focus();}}}>
    <summary>来源与版本</summary><div className="journal-source-body">{children}</div>
  </details>;
}
