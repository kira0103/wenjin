import {Component,useEffect,useState,type ErrorInfo,type ReactNode} from 'react';
import {AlertCircle,X} from 'lucide-react';
import {ApiError,type FailureNotice} from './errors';
import './systemFeedback.css';
type Item=FailureNotice&{count:number;time:string};
export function SystemFeedback(){
  const [notice,setNotice]=useState<Item|null>(null);
  useEffect(()=>{
    const show=(event:Event)=>{const value=(event as CustomEvent<FailureNotice>).detail;setNotice(previous=>({...value,count:previous?.message===value.message&&previous?.operation===value.operation?previous.count+1:1,time:new Date().toLocaleTimeString()}));};
    const unhandled=(event:Event)=>{if((event as PromiseRejectionEvent).reason instanceof ApiError)return;setNotice({operation:'页面操作未完成',message:'操作发生异常，请检查当前页面的提示。',action:'可重新打开当前页面；后台已提交的任务会继续执行。',code:'UNHANDLED_OPERATION',count:1,time:new Date().toLocaleTimeString()});};
    const clear=()=>setNotice(null);
    window.addEventListener('wenjin:connected',clear);window.addEventListener('wenjin:unauthorized',clear);
    window.addEventListener('wenjin:request-error',show);window.addEventListener('unhandledrejection',unhandled);window.addEventListener('error',unhandled);
    return()=>{window.removeEventListener('wenjin:connected',clear);window.removeEventListener('wenjin:unauthorized',clear);window.removeEventListener('wenjin:request-error',show);window.removeEventListener('unhandledrejection',unhandled);window.removeEventListener('error',unhandled);};
  },[]);
  if(!notice)return null;
  return <aside className="system-feedback" role="alert" aria-live="assertive"><AlertCircle size={19}/><div><strong>{notice.operation}{notice.count>1?` · 已发生 ${notice.count} 次`:''}</strong><p>{notice.message}</p><small>{notice.action}</small><details><summary>错误详情</summary><dl><div><dt>时间</dt><dd>{notice.time}</dd></div>{notice.status&&<div><dt>状态</dt><dd>{notice.status}</dd></div>}{notice.code&&<div><dt>错误类型</dt><dd>{notice.code}</dd></div>}{notice.requestId&&<div><dt>排查编号</dt><dd>{notice.requestId}</dd></div>}</dl></details></div><button type="button" aria-label="关闭异常提示" onClick={()=>setNotice(null)}><X size={17}/></button></aside>;
}
export class PageErrorBoundary extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  componentDidCatch(_error:Error,_info:ErrorInfo){/* The error screen stays visible until the user reloads. */}
  render(){return this.state.failed?<main className="system-page-error" role="alert"><AlertCircle size={28}/><h1>页面显示异常</h1><p>请刷新页面重新打开。后台已提交的任务会继续执行。</p><button type="button" className="button-primary" onClick={()=>window.location.reload()}>刷新页面</button></main>:this.props.children;}
}
