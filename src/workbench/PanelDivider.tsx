import { useEffect, useRef, type PointerEvent } from 'react';
export default function PanelDivider({side,width,max,onWidth}:{side:'left'|'right';width:number;max:number;onWidth(v:number):void}){
  const cleanup=useRef<(()=>void)|null>(null),minimum=side==='left'?184:320;
  useEffect(()=>()=>cleanup.current?.(),[]);
  function start(e:PointerEvent<HTMLDivElement>){if(e.button!==0)return;e.preventDefault();cleanup.current?.();const x=e.clientX,initial=width;
    document.body.classList.add('is-resizing');
    const move=(event:globalThis.PointerEvent)=>onWidth(Math.round(Math.max(minimum,Math.min(max,initial+(event.clientX-x)*(side==='left'?1:-1)))));
    const stop=()=>{document.body.classList.remove('is-resizing');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop);window.removeEventListener('pointercancel',stop);cleanup.current=null;};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop);window.addEventListener('pointercancel',stop);cleanup.current=stop;
  }
  return <div role="separator" aria-orientation="vertical" aria-label={side==='left'?'调整导航宽度':'调整研究助手宽度'} aria-valuemin={minimum} aria-valuemax={max} aria-valuenow={width} tabIndex={0} className={'panel-divider panel-divider-'+side} onPointerDown={start} onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();onWidth(Math.max(minimum,Math.min(max,width+(e.key==='ArrowRight'?16:-16)*(side==='left'?1:-1))));}}}/>;
}
