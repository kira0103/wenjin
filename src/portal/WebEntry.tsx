import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { BookOpen, ArrowUpRight, ShieldCheck, FolderOpen, LogIn, LoaderCircle } from 'lucide-react';
import ServiceConnection from '../workbench/ServiceConnection';
import { call } from '../workbench/transport';
import { clearStorageAccount, setStorageAccount } from '../workbench/storage';
import { applyTheme, readTheme } from '../workbench/appearance';
import AccountSettings from './AccountSettings';
import type { PortalSession, PortalUser } from './types';
import './portal.css';
import { PortalVisibility } from './visibility';

const AdminConsole=lazy(()=>import('./AdminConsole'));
const Workbench=lazy(()=>import('../workbench/App'));
const loading=<div className="portal-loading"><LoaderCircle size={24} className="spin"/><span>正在连接问津…</span></div>;

export default function WebEntry() {
  const [session,setSession]=useState<PortalSession|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  useEffect(()=>{if(window.desktop)return;let live=true;void call<PortalSession>({path:'/api/session',silent:true}).then(value=>{if(live)setSession(value);}).catch(()=>{if(live)setError('暂时无法连接资料服务，请检查服务是否启动');});return()=>{live=false;};},[attempt]);
  if(window.desktop)return <Suspense fallback={loading}><ServiceConnection><Workbench/></ServiceConnection></Suspense>;
  if(!session)return error?<div className="portal-loading"><p role="alert">{error}</p><button className="portal-primary" onClick={()=>{setError('');setAttempt(value=>value+1);}}>重新连接</button></div>:loading;
  if(session.mode!=='portal')return <Suspense fallback={loading}><ServiceConnection><Workbench/></ServiceConnection></Suspense>;
  return <PortalRoot initial={session}/>;
}

function PortalRoot({initial}:{initial:PortalSession}) {
  const [user,setUser]=useState<PortalUser|null>(initial.user),[connected,setConnected]=useState(initial.authenticated),[notice,setNotice]=useState(''),[gateOk,setGateOk]=useState(()=>{try{return sessionStorage.getItem('wenjin:admin-workspace')==='1';}catch{return false;}});
  if(initial.authenticated&&initial.user&&!['/app','/admin'].includes(location.pathname.replace(/\/$/,'')))history.replaceState(null,'','/app/');
  const adminPage=location.pathname.replace(/\/$/,'')==='/admin';
  useEffect(()=>{const expired=()=>{setConnected(false);setNotice('登录已过期或账号状态已更新，请重新登录');};window.addEventListener('wenjin:unauthorized',expired);return()=>window.removeEventListener('wenjin:unauthorized',expired);},[]);
  if(user)setStorageAccount(user.id);
  async function logout() {
    try { await call({path:'/api/session',method:'DELETE',silent:true}); } catch { /* A revoked or changed account still leaves this page signed out. */ }
    if(user)clearStorageAccount(user.id);
    try{sessionStorage.removeItem('wenjin:admin-workspace');}catch{/* storage may be unavailable */}
    setGateOk(false);setUser(null);setConnected(false);setStorageAccount('');setNotice('已安全退出，请重新登录');
  }
  function loggedIn(next:PortalUser) {
    if(user&&user.id!==next.id)clearStorageAccount(user.id);
    setStorageAccount(next.id);applyTheme(readTheme());setUser(next);setConnected(true);setNotice('');
    if(!['/app','/admin'].includes(location.pathname.replace(/\/$/,'')))history.replaceState(null,'','/app/');
    window.dispatchEvent(new Event('wenjin:connected'));
  }
  let content:ReactNode=null;
  if(user) {
    if(user.password_change_required)content=<div className="portal-security-page"><Brand/><AccountSettings key={user.id} user={user} required onLogout={logout}/></div>;
    else if(adminPage&&user.role!=='admin')content=<div className="portal-access-denied"><ShieldCheck size={40}/><h1>这里是管理员工作区</h1><p>当前账号 @{user.username} 是研究用户，没有管理权限；需要管理后台请联系管理员</p><a className="portal-primary" href="/app/">进入研究工作区</a><button className="portal-link-button" onClick={()=>void logout()}>返回登录</button></div>;
    else if(adminPage)content=<Suspense fallback={loading}><AdminConsole key={user.id} user={user} onLogout={logout}/></Suspense>;
    else if(user.role==='admin'&&!gateOk)content=<div className="portal-access-denied"><ShieldCheck size={40}/><h1>研究工作区是独立空间</h1><p>当前账号 @{user.username} 是管理员。进入研究工作区将使用同一账号的个人工作区，文献、项目与研究成果只属于你，与后台管理数据分开保存。</p><div style={{display:'flex',gap:'var(--sp-3)',flexWrap:'wrap',justifyContent:'center'}}><button className="portal-primary" onClick={()=>{try{sessionStorage.setItem('wenjin:admin-workspace','1');}catch{/* storage may be unavailable */}setGateOk(true);}}>以管理员账号进入</button><button className="portal-link-button" onClick={()=>void logout()}>退出并切换研究账号</button></div></div>;
    else content=<Suspense fallback={loading}><Workbench key={user.id} portalUser={user} onLogout={logout}/></Suspense>;
  }
  return <PortalVisibility.Provider value={connected}>{connected&&content}{!connected&&<Login onLogin={loggedIn} notice={notice} administrator={adminPage}/>}</PortalVisibility.Provider>;
}

export function Brand(){return <a className="portal-brand" href="/"><span><BookOpen size={22}/></span><strong>问津<small>WENJIN</small></strong></a>;}

function Login({onLogin,notice,administrator}:{onLogin(user:PortalUser):void;notice:string;administrator:boolean}) {
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function login(){setBusy(true);setError('');try{const value=await call<PortalSession>({path:'/api/session',method:'POST',body:{username,password},silent:true});if(!value.user)throw Error('登录未完成');setPassword('');onLogin(value.user);}catch(e){setError(e instanceof Error?e.message:'登录未完成');}finally{setBusy(false);}}
  return <main className="portal-root portal-login"><section className="portal-login-story"><Brand/><div className="portal-story-copy"><div className="portal-story-title">循文献之线，<br/>抵达研究深处。</div><p>让文献、阅读与写作在同一处衔接。<br/>从一份原文，走向有出处的研究。</p><div className="portal-story-features"><span><FolderOpen size={18}/>共享文献，独立项目</span><span><BookOpen size={18}/>持续的阅读与整理</span><span><ShieldCheck size={18}/>属于你的研究工作区</span></div></div><footer>问津 · 史学研究工作台</footer></section><section className="portal-login-panel"><div className="portal-login-card"><h1 className="portal-login-title">{administrator?'登录管理后台':'登录研究工作区'}</h1><p>{administrator?'维护公共文献与账号配置':'使用管理员提供的账号登录'}</p><form className="portal-form" onSubmit={event=>{event.preventDefault();void login();}}><label>用户名<input autoFocus autoComplete="username" required maxLength={48} placeholder="例如 admin" value={username} onChange={event=>setUsername(event.target.value)} disabled={busy}/></label><label>密码<input type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={event=>setPassword(event.target.value)} disabled={busy}/></label>{error&&<p className="portal-alert" role="alert">{error}</p>}{notice&&!error&&<p className="portal-notice" role="status">{notice}</p>}<button className="portal-primary portal-login-submit" disabled={busy}>{busy?<LoaderCircle size={18} className="spin"/>:<LogIn size={18}/>}<span>{busy?'正在登录…':administrator?'进入管理后台':'进入研究工作区'}</span></button></form><p className="portal-login-help">没有账号或忘记密码？请联系管理员</p><a className="portal-entry-switch" href={administrator?'/app/':'/admin/'}>{administrator?'前往用户工作区':'管理员入口'}<ArrowUpRight size={16}/></a></div><span className="portal-login-footnote">系统文献大家共享；个人文献、项目、笔记和任务分别保存。</span></section></main>;
}
