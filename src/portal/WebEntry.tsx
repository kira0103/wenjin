import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { BookOpen, ArrowUpRight, ShieldCheck, FolderOpen, LogIn, LoaderCircle } from 'lucide-react';
import ServiceConnection from '../workbench/ServiceConnection';
import { call } from '../workbench/transport';
import { clearStorageAccount, setStorageAccount } from '../workbench/storage';
import { applyTheme, readTheme } from '../workbench/appearance';
import AccountSettings from './AccountSettings';
import type { PortalSession, PortalUser } from './types';
import './portal.css';

const AdminConsole=lazy(()=>import('./AdminConsole'));
const Workbench=lazy(()=>import('../workbench/App'));
const loading=<div className="portal-loading"><LoaderCircle size={24} className="spin"/><span>正在连接问津…</span></div>;

export default function WebEntry() {
  const [session,setSession]=useState<PortalSession|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  useEffect(()=>{if(window.desktop)return;let live=true;void call<PortalSession>({path:'/api/session'}).then(value=>{if(live)setSession(value);}).catch(()=>{if(live)setError('暂时无法连接服务，请检查服务是否启动。');});return()=>{live=false;};},[attempt]);
  if(window.desktop)return <Suspense fallback={loading}><ServiceConnection><Workbench/></ServiceConnection></Suspense>;
  if(!session)return error?<div className="portal-loading"><p role="alert">{error}</p><button className="portal-primary" onClick={()=>{setError('');setAttempt(value=>value+1);}}>重新连接</button></div>:loading;
  if(session.mode!=='portal')return <Suspense fallback={loading}><ServiceConnection><Workbench/></ServiceConnection></Suspense>;
  return <PortalRoot initial={session}/>;
}

function PortalRoot({initial}:{initial:PortalSession}) {
  const [user,setUser]=useState<PortalUser|null>(initial.user),[connected,setConnected]=useState(initial.authenticated),[notice,setNotice]=useState('');
  if(initial.authenticated&&initial.user&&!['/app','/admin'].includes(location.pathname.replace(/\/$/,'')))history.replaceState(null,'',initial.user.role==='admin'?'/admin/':'/app/');
  const adminPage=location.pathname.replace(/\/$/,'')==='/admin';
  useEffect(()=>{const expired=()=>{setConnected(false);setNotice('登录已过期或账号状态已更新，请重新登录。');};window.addEventListener('wenjin:unauthorized',expired);return()=>window.removeEventListener('wenjin:unauthorized',expired);},[]);
  if(user)setStorageAccount(user.id);
  async function logout() {
    try { await call({path:'/api/session',method:'DELETE'}); } catch { /* A revoked or changed account still leaves this page signed out. */ }
    if(user)clearStorageAccount(user.id);
    setUser(null);setConnected(false);setStorageAccount('');setNotice('已安全退出，请重新登录。');
  }
  function loggedIn(next:PortalUser) {
    if(user&&user.id!==next.id)clearStorageAccount(user.id);
    setStorageAccount(next.id);applyTheme(readTheme());setUser(next);setConnected(true);setNotice('');
    if(!['/app','/admin'].includes(location.pathname.replace(/\/$/,'')))history.replaceState(null,'',next.role==='admin'?'/admin/':'/app/');
    window.dispatchEvent(new Event('wenjin:connected'));
  }
  let content:ReactNode=null;
  if(user) {
    if(user.password_change_required)content=<div className="portal-security-page"><Brand/><AccountSettings user={user} required onLogout={logout}/></div>;
    else if(adminPage&&user.role!=='admin')content=<div className="portal-access-denied"><ShieldCheck size={40}/><h1>这里是管理员工作区</h1><p>当前账号可以进入自己的研究工作区。</p><a className="portal-primary" href="/app/">进入研究工作区</a><button className="portal-link-button" onClick={()=>void logout()}>切换账号</button></div>;
    else if(adminPage)content=<Suspense fallback={loading}><AdminConsole user={user} onLogout={logout}/></Suspense>;
    else content=<Suspense fallback={loading}><Workbench key={user.id} portalUser={user} onLogout={logout}/></Suspense>;
  }
  return <><div style={{display:connected?'contents':'none'}}>{content}</div>{!connected&&<Login onLogin={loggedIn} notice={notice} administrator={adminPage}/>}</>;
}

export function Brand(){return <a className="portal-brand" href="/"><span><BookOpen size={22}/></span><strong>问津<small>WENJIN</small></strong></a>;}

function Login({onLogin,notice,administrator}:{onLogin(user:PortalUser):void;notice:string;administrator:boolean}) {
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function login(){setBusy(true);setError('');try{const value=await call<PortalSession>({path:'/api/session',method:'POST',body:{username,password}});if(!value.user)throw Error('登录未完成');setPassword('');onLogin(value.user);}catch(e){setError(e instanceof Error?e.message:'登录未完成');}finally{setBusy(false);}}
  return <main className="portal-root portal-login"><section className="portal-login-story"><Brand/><div className="portal-story-copy"><span className="portal-eyebrow">A PLACE FOR YOUR RESEARCH</span><h1>循文献之线，<br/>抵达研究深处。</h1><p>让文献、阅读与写作在同一处衔接。<br/>从一份原文，走向有出处的研究。</p><div className="portal-story-features"><span><FolderOpen size={18}/>共享文献，独立项目</span><span><BookOpen size={18}/>持续的阅读与整理</span><span><ShieldCheck size={18}/>属于你的研究工作区</span></div></div><footer>问津 · 史学研究工作台</footer></section><section className="portal-login-panel"><div className="portal-login-card"><span className="portal-entry-label">{administrator?'ADMINISTRATION':'RESEARCH WORKSPACE'}</span><h2>{administrator?'登录管理后台':'欢迎回到问津'}</h2><p>{administrator?'维护公共文献，管理账号、模型与工作区。':'登录后，继续你的文献与研究工作。'}</p><form className="portal-form" onSubmit={event=>{event.preventDefault();void login();}}><label>用户名<input autoFocus autoComplete="username" required maxLength={48} placeholder="输入用户名" value={username} onChange={event=>setUsername(event.target.value)} disabled={busy}/></label><label>密码<input type="password" autoComplete="current-password" required maxLength={256} placeholder="输入密码" value={password} onChange={event=>setPassword(event.target.value)} disabled={busy}/></label>{error&&<p className="portal-alert" role="alert">{error}</p>}{notice&&!error&&<p className="portal-notice" role="status">{notice}</p>}<button className="portal-primary portal-login-submit" disabled={busy}>{busy?<LoaderCircle size={17} className="spin"/>:<LogIn size={17}/>}<span>{busy?'正在登录…':administrator?'进入管理后台':'进入研究工作区'}</span></button></form><p className="portal-login-help">没有账号或忘记密码？请联系管理员。</p><a className="portal-entry-switch" href={administrator?'/app/':'/admin/'}>{administrator?'前往用户工作区':'管理员入口'}<ArrowUpRight size={15}/></a></div><span className="portal-login-footnote">公共文献大家共享，你的项目、笔记和任务分别保存。</span></section></main>;
}
