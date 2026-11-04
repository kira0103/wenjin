import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';
import {appDialogQueue,validDialogInput,type DialogEntry,type TextDialogOptions} from './dialogQueue';
import './app-dialogs.css';

function DialogWindow({entry}:{entry:DialogEntry}){
  const element=useRef<HTMLDialogElement>(null);
  const input=entry.kind==='input'?entry.options as TextDialogOptions:null;
  const [value,setValue]=useState(input?.defaultValue??'');
  const options=entry.options,valid=!input||validDialogInput(value,input);
  useEffect(()=>{const dialog=element.current;dialog?.showModal();dialog?.querySelector<HTMLElement>(input?'.app-dialog-input input':'.app-dialog-cancel')?.focus({preventScroll:true});return()=>{dialog?.close();};},[]);
  function finish(result:boolean|string|null){const dialog=element.current;if(appDialogQueue.settle(result))dialog?.close();}
  const cancel=()=>finish(input?null:false);
  return createPortal(<dialog ref={element} className={`app-dialog${document.querySelector('.portal-workspace,.portal-admin')?' portal-modal':''}`} data-tone={options.tone||'normal'} aria-labelledby="app-dialog-title" aria-describedby={options.description?'app-dialog-description':undefined} onCancel={e=>{e.preventDefault();cancel();}}>
    <form onSubmit={e=>{e.preventDefault();if(valid)finish(input?value:true);}}>
      <header className="app-dialog-heading"><h2 id="app-dialog-title">{options.title}</h2><button type="button" className="app-dialog-close" aria-label={options.tone==='danger'?'取消并关闭':'关闭对话框'} onClick={cancel}><X size={18}/></button></header>
      <div className="app-dialog-body">{options.description&&<p id="app-dialog-description">{options.description}</p>}{!!options.details?.length&&<dl className="app-dialog-facts">{options.details.map((row,i)=><div key={i}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>}{options.notes?.map((note,i)=><p className="app-dialog-note" key={i}>{note}</p>)}{input&&<label className="app-dialog-input">{input.label}<input autoFocus value={value} maxLength={input.maxLength} required={input.required} onChange={e=>setValue(e.target.value)} autoComplete="off" spellCheck={false}/>{input.match!==undefined&&<small>请输入完整题名：<strong>{input.match}</strong></small>}</label>}</div>
      <footer className="app-dialog-actions"><button type="button" autoFocus={!input} className="app-dialog-cancel" onClick={cancel}>{options.cancelLabel||'取消'}</button><button type="submit" className="app-dialog-confirm" disabled={!valid}>{options.confirmLabel||'确认继续'}</button></footer>
    </form>
  </dialog>,document.body);
}

export default function AppDialogs(){
  const current=useSyncExternalStore(appDialogQueue.subscribe,appDialogQueue.snapshot,()=>null);
  useEffect(()=>()=>appDialogQueue.cancelAll(),[]);
  return current?<DialogWindow key={current.id} entry={current}/>:null;
}
