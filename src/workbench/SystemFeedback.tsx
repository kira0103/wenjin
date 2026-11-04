import {Component,useEffect,useRef,useState,type ErrorInfo,type ReactNode} from 'react';
import {AlertCircle,X} from 'lucide-react';
import {ApiError,type FailureNotice} from './errors';
import './systemFeedback.css';
type Item=FailureNotice&{count:number;time:string;key:number};
const MAX_NOTICES=3;
/** 全局反馈的唯一出口：只接未捕获异常与 transport 判定的全局错误（网络/超时/5xx）。 */
export function SystemFeedback(){
  const [notices,setNotices]=useState<Item[]>([]),[paused,setPaused]=useState(false),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),sequence=useRef(0);
  useEffect(()=>{
    const push=(notice:FailureNotice)=>{
      setNotices(previous=>{
        const same=previous.find(item=>item.message===notice.message&&item.operation===notice.operation);
        if(same)return previous.map(item=>item===same?{...item,count:item.count+1,time:new Date().toLocaleTimeString()}:item);
        return [...previous,{...notice,count:1,time:new Date().toLocaleTimeString(),key:++sequence.current}].slice(-MAX_NOTICES);
      });
    };
    const show=(event:Event)=>push((event as CustomEvent<FailureNotice>).detail);
    const unhandled=(event:Event)=>{if((event as PromiseRejectionEvent).reason instanceof ApiError)return;push({operation:'操作未完成',message:'操作未完成，刷重试或刷新页面',action:'后台已提交的任务会继续执行',code:'UNHANDLED_OPERATION'});};
    const clearAll=()=>setNotices([]);
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')clearAll();};
    window.addEventListener('wenjin:connected',clearAll);window.addEventListener('wenjin:unauthorized',clearAll);
    window.addEventListener('wenjin:request-error',show);window.addEventListener('unhandledrejection',unhandled);window.addEventListener('error',unhandled);window.addEventListener('keydown',escape);
    return()=>{window.removeEventListener('wenjin:connected',clearAll);window.removeEventListener('wenjin:unauthorized',clearAll);window.removeEventListener('wenjin:request-error',show);window.removeEventListener('unhandledrejection',unhandled);window.removeEventListener('error',unhandled);window.removeEventListener('keydown',escape);};
  },[]);
  // 8s 后自动收起；悬停或聚焦时暂停，错误详情可随时重新展开。
  useEffect(()=>{
    clearTimeout(timer.current);
    if(!notices.length||paused)return;
    timer.current=setTimeout(()=>setNotices([]),8000);
    return()=>clearTimeout(timer.current);
  },[notices,paused]);
  if(!notices.length)return null;
  return <div className="system-feedback-stack">{notices.map(notice=><aside key={notice.key} className="system-feedback" role="alert" aria-live="assertive" onMouseEnter={()=>setPaused(true)} onMouseLeave={()=>setPaused(false)} onFocus={()=>setPaused(true)} onBlur={()=>setPaused(false)}><AlertCircle size={18}/><div><strong>{notice.operation}{notice.count>1?` · 已发生 ${notice.count} 次`:''}</strong><p>{notice.message}</p>{notice.action&&<small>{notice.action}</small>}<details><summary>错误详情</summary><dl><div><dt>时间</dt><dd>{notice.time}</dd></div>{notice.status&&<div><dt>状态</dt><dd>{notice.status}</dd></div>}{notice.code&&<div><dt>错误类型</dt><dd>{notice.code}</dd></div>}{notice.requestId&&<div><dt>排查编号</dt><dd>{notice.requestId}</dd></div>}</dl></details></div><button type="button" aria-label="关闭提示" onClick={()=>setNotices(previous=>previous.filter(item=>item.key!==notice.key))}><X size={18}/></button></aside>)}</div>;
}
export class PageErrorBoundary extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('页面渲染异常',error,info.componentStack);}
  render(){return this.state.failed?<main className="system-page-error" role="alert"><AlertCircle size={28}/><h1>页面显示异常</h1><p>刷刷新页面重新打开，后台已提交的任务会继续执行。</p><button type="button" className="button-primary" onClick={()=>window.location.reload()}>刷新页面</button></main>:this.props.children;}
}
