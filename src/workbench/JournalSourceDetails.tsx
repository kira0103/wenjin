import { useEffect, useRef, type ReactNode } from 'react';

/** 目录来源弹层：位置由 CSS 锚定在摘要下方，脚本只负责关闭。 */
export default function JournalSourceDetails({children}:{children:ReactNode}) {
  const ref=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{
    const dismiss=(event:PointerEvent)=>{if(ref.current&&!ref.current.contains(event.target as Node))ref.current.open=false;};
    document.addEventListener('pointerdown',dismiss);
    return ()=>document.removeEventListener('pointerdown',dismiss);
  },[]);
  return <details ref={ref} className="journal-source-info" onKeyDown={event=>{if(event.key==='Escape'&&event.currentTarget.open){event.currentTarget.open=false;event.currentTarget.querySelector('summary')?.focus();event.preventDefault();}}}>
    <summary>目录来源</summary><div className="journal-source-body">{children}</div>
  </details>;
}
