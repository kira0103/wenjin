import { useState } from 'react';
import { KeyRound, LogOut, ShieldCheck } from 'lucide-react';
import { call } from '../workbench/transport';
import type { PortalUser } from './types';

export default function AccountSettings({user,onLogout,required=false,embedded=false}:{user:PortalUser;onLogout():Promise<void>;required?:boolean;embedded?:boolean}) {
  const [current,setCurrent]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[updated,setUpdated]=useState(false);
  async function change() {
    setError('');
    if(password!==confirm){setError('两次输入的新密码不一致');return;}
    setBusy(true);
    try {
      await call({path:'/api/me/password',method:'POST',body:{current_password:current,password},silent:true});
      setCurrent('');setPassword('');setConfirm('');setUpdated(true);
      await new Promise(resolve=>setTimeout(resolve,1200));
      await onLogout();
    } catch(e) {setError(e instanceof Error?e.message:'密码更新未完成');}
    finally {setBusy(false);}
  }
  return <section className="portal-account-settings">{!embedded&&<div className="portal-section-heading"><div><h2>{required?'设置你的新密码':'账号与安全'}</h2><p>{required?'首次登录请修改管理员提供的临时密码，再进入个人工作区':'系统文献由大家共享；个人文献、项目、笔记与任务只有你可见'}</p></div><ShieldCheck size={28}/></div>}<div className="portal-profile"><span className="portal-avatar">{user.display_name.slice(0,1)}</span><div><strong>{user.display_name}</strong><span>@{user.username} · {user.role==='admin'?'管理员':'研究用户'}</span></div></div><form className="portal-form" onSubmit={event=>{event.preventDefault();void change();}}><h3><KeyRound size={18}/>修改密码</h3><label>当前密码<input type="password" autoComplete="current-password" required maxLength={256} value={current} onChange={event=>setCurrent(event.target.value)} disabled={busy}/></label><label>新密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={password} onChange={event=>setPassword(event.target.value)} disabled={busy}/><small>使用至少 12 个字符</small></label><label>再次输入新密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={confirm} onChange={event=>setConfirm(event.target.value)} disabled={busy}/></label>{error&&<p role="alert" className="portal-alert">{error}</p>}{updated&&<p role="status" className="portal-notice">密码已更新，正在退出…</p>}<div className="portal-actions"><button className="portal-primary" disabled={busy||updated}>{busy?'正在保存…':updated?'正在退出…':'更新密码并重新登录'}</button></div></form><button className="portal-link-button" onClick={()=>void onLogout()} disabled={busy}><LogOut size={16}/>退出登录</button></section>;
}
