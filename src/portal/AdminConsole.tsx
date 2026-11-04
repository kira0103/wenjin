import { isAppDialogOpen, confirmDialog } from '../workbench/dialogQueue';
import PageHeading, { PageHeadingProvider } from '../workbench/PageHeading';
import SubmissionWorkbench from '../workbench/SubmissionWorkbench';
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, Check, ChevronRight, Copy, Cpu, Eye, EyeOff, KeyRound, LayoutDashboard, LibraryBig, LogOut, Menu, Moon, Plus, RefreshCw, ScrollText, Search, Server, Settings2, ShieldCheck, SlidersHorizontal, Sun, Users, X } from 'lucide-react';
import { call } from '../workbench/transport';
import SharedLibrary from './SharedLibrary';
import AccountSettings from './AccountSettings';
import { applyTheme, readTheme, saveTheme } from '../workbench/appearance';
import type { ManagedUser, PortalUser } from './types';

type Section='retrieval'|'assistants'|'journals'|'library'|'overview'|'users'|'workspaces'|'models'|'audit'|'account';
type Overview={users:number;enabled_users:number;administrators:number;running_workspaces:number;active_tasks:number;failed_workspaces:number};
type Audit={id:number;actor:string|null;target:string|null;action:string;target_id:string|null;details:string;created_at:number};
type Dialog={kind:'create'}|{kind:'edit'|'password';user:ManagedUser};
type Issued={kind:'create'|'password';username:string;password:string};
const stateNames:Record<string,string>={not_started:'尚未启动',starting:'准备中',ready:'运行中',stopping:'正在停止',stopped:'已停止',offline:'已离线',failed:'需要检查'};
const actionNames:Record<string,string>={'journal.request':'维护公共期刊目录','library.request':'维护系统文献库','user.create':'建立账号','user.update':'更新账号','user.password.reset':'重置密码','user.password.change':'修改本人密码','workspace.stop':'停止工作区','model.configure':'更新模型配置'};
/** 异常优先：失败/离线的工作区排在最前，运行时状态靠后。 */
const workspaceOrder:Record<string,number>={failed:0,offline:1,starting:2,stopping:3,not_started:4,stopped:5,ready:6};
const passwordAlphabet='abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
function randomPassword(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>passwordAlphabet[byte%passwordAlphabet.length]).join('');}
const date=(value:number|null)=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'尚未登录';
const day=(value:number|null)=>value?new Date(value*1000).toLocaleDateString('zh-CN'):'尚未登录';

export default function AdminConsole({user,onLogout}:{user:PortalUser;onLogout():Promise<void>}) {
  const [mobileNav,setMobileNav]=useState(false),[theme,setTheme]=useState(readTheme);
  useEffect(()=>{applyTheme(theme);},[theme]);
  useEffect(()=>{if(!mobileNav)return;const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setMobileNav(false);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[mobileNav]);
  function toggleTheme(){const next=theme==='dark'?'light':'dark';setTheme(next);saveTheme(next);}
  const [librarySource,setLibrarySource]=useState<{id:string}|null>(null);
  const [section,setSection]=useState<Section>('overview'),[overview,setOverview]=useState<Overview|null>(null),[users,setUsers]=useState<ManagedUser[]>([]),[audit,setAudit]=useState<Audit[]>([]);
  const [loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState(''),[query,setQuery]=useState('');
  const [dialog,setDialog]=useState<Dialog|null>(null),[issued,setIssued]=useState<Issued|null>(null),[busy,setBusy]=useState(false),[copied,setCopied]=useState(false);
  const [username,setUsername]=useState(''),[displayName,setDisplayName]=useState(''),[password,setPassword]=useState(''),[confirmPassword,setConfirmPassword]=useState(''),[showPassword,setShowPassword]=useState(false),[role,setRole]=useState<'user'|'admin'>('user'),[enabled,setEnabled]=useState(true);
  const [lastRefresh,setLastRefresh]=useState<number|null>(null),[auditQuery,setAuditQuery]=useState(''),[auditAction,setAuditAction]=useState('');
  const dialogRef=useRef<HTMLElement>(null),restoreFocus=useRef<HTMLElement|null>(null);
  const refresh=useCallback(async()=>{
    try{
      const [summary,accounts,events]=await Promise.all([
        call<Overview>({path:'/api/admin/overview',silent:true}),
        call<ManagedUser[]>({path:'/api/admin/users',silent:true}),
        call<Audit[]>({path:'/api/admin/audit',silent:true}),
      ]);
      setOverview(summary);setUsers(accounts);setAudit(events);setError('');setLastRefresh(Date.now());
    }catch(e){setError(e instanceof Error?e.message:'管理信息读取失败');}
    finally{setLoading(false);}
  },[]);
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),10000);return()=>clearInterval(timer);},[refresh]);
  // 成功提示 4 秒后自动收起，避免旧提示跟着用户切换到下一个分区。
  useEffect(()=>{if(!notice)return;const timer=window.setTimeout(()=>setNotice(''),4000);return()=>window.clearTimeout(timer);},[notice]);
  useEffect(()=>{if(!issued)return;const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setIssued(null);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[issued]);
  const filtered=users.filter(item=>`${item.username} ${item.display_name}`.toLowerCase().includes(query.trim().toLowerCase()));
  const sortedUsers=[...users].sort((a,b)=>(workspaceOrder[a.workspace.state]??9)-(workspaceOrder[b.workspace.state]??9));
  const navigation:[Section,string,typeof Users][]=[['overview','运行概览',LayoutDashboard],['library','系统文献库',LibraryBig],['journals','期刊库管理',BookOpen],['retrieval','检索配置',SlidersHorizontal],['assistants','助手与 Skill',Settings2],['users','用户管理',Users],['workspaces','工作区运行',Server],['models','模型服务',Cpu],['audit','操作记录',ScrollText],['account','账号与安全',ShieldCheck]];
  const libraryLeave=useRef<(()=>Promise<boolean>)|null>(null);
  const registerLibraryLeave=useCallback((callback:(()=>Promise<boolean>)|null)=>{libraryLeave.current=callback;},[]);
  async function navigate(value:Section){if((['library','journals','retrieval','assistants'].includes(section))&&libraryLeave.current&&!await libraryLeave.current())return false;setSection(value);setMobileNav(false);setQuery('');setNotice('');setError('');return true;}
  async function openLibrary(id?:string){if(await navigate('library'))setLibrarySource(id?{id}:null);}
  const currentNavigation=navigation.find(item=>item[0]===section)!;
  const title=currentNavigation[1];
  const auditActions=[...new Set(audit.map(event=>event.action))];
  const filteredAudit=audit.filter(event=>{
    if(auditAction&&event.action!==auditAction)return false;
    const keyword=auditQuery.trim().toLocaleLowerCase();
    if(!keyword)return true;
    return `${event.actor||''} ${event.target||''} ${event.target_id||''} ${actionNames[event.action]||event.action} ${event.details||''}`.toLocaleLowerCase().includes(keyword);
  });
  const overviewReading=!overview&&loading,overviewFailed=!overview&&!!error;
  const readingDetail=(ready:string)=>overviewReading?'读取中…':overviewFailed?'读取失败':ready;
  function openDialog(value:Dialog){setPassword('');setConfirmPassword('');setShowPassword(false);setCopied(false);setError('');setDialog(value);if(value.kind==='create'){setUsername('');setDisplayName('');setRole('user');setEnabled(true);}else{setUsername(value.user.username);setDisplayName(value.user.display_name);setRole(value.user.role);setEnabled(!!value.user.enabled);}}
  function closeDialog(){if(!busy){setDialog(null);setPassword('');setConfirmPassword('');setShowPassword(false);}}
  useEffect(()=>{
    if(!dialog)return;
    restoreFocus.current=document.activeElement as HTMLElement;
    const panel=dialogRef.current;
    const focusables=()=>panel?[...panel.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')].filter(element=>element.offsetParent!==null):[];
    const frame=requestAnimationFrame(()=>{
      const firstInput=panel?.querySelector<HTMLElement>('input:not([disabled]),select:not([disabled]),textarea:not([disabled])');
      (firstInput??focusables()[0]??panel)?.focus();
    });
    const onKey=(event:KeyboardEvent)=>{
      if(isAppDialogOpen())return;
      if(event.key==='Escape'){event.preventDefault();closeDialog();return;}
      if(event.key!=='Tab'||!panel)return;
      const items=focusables();
      if(!items.length)return;
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    };
    document.addEventListener('keydown',onKey,true);
    return ()=>{cancelAnimationFrame(frame);document.removeEventListener('keydown',onKey,true);restoreFocus.current?.focus();};
  },[dialog]);
  async function copyText(value:string){try{await navigator.clipboard.writeText(value);setCopied(true);window.setTimeout(()=>setCopied(false),2000);}catch{/* 剪贴板不可用时仍可直接选中文本框复制 */}}
  async function saveUser(){
    if(!dialog)return;
    if(dialog.kind!=='edit'&&password!==confirmPassword){setError('两次输入的临时密码不一致');return;}
    setBusy(true);setError('');
    try{
      if(dialog.kind==='create')await call({path:'/api/admin/users',method:'POST',body:{username,display_name:displayName,password,role},silent:true});
      else if(dialog.kind==='edit')await call({path:`/api/admin/users/${dialog.user.id}`,method:'PATCH',body:{display_name:displayName,role,enabled},silent:true});
      else await call({path:`/api/admin/users/${dialog.user.id}/password`,method:'POST',body:{password},silent:true});
      const account=dialog.kind==='create'?username:dialog.user.username;
      const kind=dialog.kind;
      setDialog(null);setPassword('');setConfirmPassword('');
      if(kind!=='edit')setIssued({kind,username:account,password});
      setNotice(kind==='password'?'临时密码已更新，原登录已失效':kind==='create'?'账号已建立，请把临时密码交给用户':'账号信息已保存');
      await refresh();
    }catch(e){setError(e instanceof Error?e.message:'账号操作未完成');}
    finally{setBusy(false);}
  }
  async function stopWorkspace(item:ManagedUser){
    if(!await confirmDialog({title:'停止工作区？',description:`停止后 ${item.display_name} 正在运行的任务会中断，文献与研究资料继续保留`,details:[{label:'账号',value:`@${item.username}`},{label:'当前状态',value:stateNames[item.workspace.state]||item.workspace.state}],confirmLabel:'停止工作区',cancelLabel:'取消',tone:'danger'}))return;
    setBusy(true);setError('');
    try{await call({path:`/api/admin/users/${item.id}/stop-workspace`,method:'POST',silent:true});setNotice('已请求正常停止工作区，在途任务保存后退出');await refresh();}
    catch(e){setError(e instanceof Error?e.message:'工作区操作未完成');}
    finally{setBusy(false);}
  }
  return <main className="portal-root portal-admin">
    <a className="skip-link" href="#admin-content" onClick={event=>{event.preventDefault();const target=document.getElementById('admin-content');target?.focus();target?.scrollIntoView({block:'start'});}}>跳到主要内容</a>
    <aside className={`portal-admin-sidebar ${mobileNav?'is-open':''}`} aria-label="管理导航">
      <a className="portal-brand" href="/admin/"><span><BookOpen size={22}/></span><strong>问津<small>RESEARCH CONSOLE</small></strong></a>
      <button className="portal-icon-button portal-mobile-close" aria-label="关闭管理导航" onClick={()=>setMobileNav(false)}><X size={18}/></button>
      <div className="portal-sidebar-caption">内容与配置</div>
      <nav>{navigation.map(([key,label,Icon])=><Fragment key={key}>{key==='users'&&<div className="portal-sidebar-caption portal-nav-group">服务管理</div>}{key==='account'&&<div className="portal-sidebar-caption portal-nav-group">偏好设置</div>}<button aria-current={section===key?'page':undefined} className={section===key?'active':''} onClick={()=>void navigate(key)}><Icon size={18}/>{label}{key==='users'&&overview&&<small>{overview.users}</small>}</button></Fragment>)}</nav>
      <div className="portal-sidebar-bottom"><div><ShieldCheck size={18}/><span>公共资料，独立研究</span></div><a href="/app/">进入研究工作区<ArrowUpRight size={16}/></a></div>
    </aside>
    {mobileNav&&<button className="portal-nav-backdrop" aria-label="关闭导航遮罩" onClick={()=>setMobileNav(false)}/>}
    <div className="portal-admin-main">
      <header className="portal-admin-topbar"><div className="portal-topbar-location"><button className="portal-icon-button portal-mobile-toggle" aria-label="打开管理导航" aria-expanded={mobileNav} onClick={()=>setMobileNav(value=>!value)}><Menu size={18}/></button><span className="portal-breadcrumb"><span>管理后台</span><ChevronRight size={16}/><strong>{title}</strong></span></div><div><button className="portal-icon-button" aria-label={theme==='dark'?'切换浅色外观':'切换深色外观'} onClick={toggleTheme}>{theme==='dark'?<Sun size={18}/>:<Moon size={18}/>}</button><button onClick={()=>void navigate('account')} className="portal-account-chip"><span className="portal-avatar small">{user.display_name.slice(0,1)}</span>{user.display_name}</button><button className="portal-icon-button" aria-label="退出登录" onClick={()=>void onLogout()}><LogOut size={18}/></button></div></header>
      <div className="portal-admin-content" id="admin-content" tabIndex={-1}>
        <PageHeadingProvider>
          <PageHeading title={title} description={
            section==='retrieval'?<><p>文献入库、切分、向量化与检索配置</p><p>加工流程：文档解析、清洗与分块、向量化入库、按权限召回、重排与上下文</p></>
            :section==='assistants'?'统一维护研究技能、智能体与发布版本'
            :section==='library'?'维护大家共享的文献、原件与录文修订'
            :section==='journals'?'查看中英文期刊目录，维护公共期刊资料'
            :section==='users'?'为研究者建立账号，管理访问权限与登录安全'
            :section==='workspaces'?'查看独立工作区与后台任务的运行状态'
            :section==='models'?'统一配置研究模型，用户工作区会自动同步'
            :section==='audit'?'查看账号与服务配置的管理操作'
            :section==='account'?'管理当前管理员的账号安全'
            :'账号、资源与研究任务，尽在掌握'} actions={<>{['overview','users','workspaces','audit'].includes(section)&&<button className="portal-secondary" disabled={loading} onClick={()=>void refresh()}><RefreshCw size={16}/>刷新</button>}{section==='users'&&<button className="portal-primary" onClick={()=>openDialog({kind:'create'})}><Plus size={16}/>建立用户</button>}</>}/>
          {error&&<p className="portal-alert" role="alert">{error}</p>}
          {notice&&<p className="portal-notice" role="status"><Check size={16}/>{notice}</p>}
          {(['library','retrieval','assistants'] as Section[]).includes(section)&&<SharedLibrary onOpenProcessing={()=>void navigate('retrieval')} incomingSource={librarySource} onIncomingUsed={()=>setLibrarySource(null)} onOpenLibrary={id=>void openLibrary(id)} key={section} mode={section as 'library'|'retrieval'|'assistants'} onRegisterLeave={registerLibraryLeave}/>}
          {section==='journals'&&<SubmissionWorkbench catalogOnly projects={[]} draft={null} onDraftUsed={()=>{}} onRegisterLeave={registerLibraryLeave}/>}
          {section==='overview'&&<>
            <div className="portal-stats">
              <article className="portal-stat" role="button" tabIndex={0} style={{cursor:'pointer'}} aria-label="需要检查的工作区，打开工作区运行" onClick={()=>setSection('workspaces')} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSection('workspaces');}}}>
                <div><span>需要检查</span></div><strong style={overview?.failed_workspaces?{color:'var(--danger)'}:undefined}>{overviewReading||overviewFailed?'—':overview?.failed_workspaces??'—'}</strong><small>{readingDetail('工作区启动异常，点击查看')}</small>
              </article>
              {[{label:'全部账号',value:overview?.users,detail:readingDetail(`${overview?.enabled_users??'—'} 个账号已启用`)},{label:'运行中工作区',value:overview?.running_workspaces,detail:readingDetail('每位用户独立运行')},{label:'用户工作区活动任务',value:overview?.active_tasks,detail:readingDetail('关闭页面后仍继续执行')}].map(({label,value,detail})=><article className="portal-stat" key={label}><div><span>{label}</span></div><strong>{overviewReading||overviewFailed?'—':value??'—'}</strong><small>{detail}</small></article>)}
            </div>
            <div className="portal-overview-grid">
              <section className="portal-card"><div className="portal-card-heading"><div><h2>工作区状态</h2><p>公共文献共享，个人研究分别保存</p></div><button className="portal-link-button" onClick={()=>setSection('workspaces')}>查看全部<ArrowUpRight size={16}/></button></div>{users.length?<div className="portal-workspace-list">{sortedUsers.slice(0,6).map(item=><div key={item.id}><span className="portal-avatar">{item.display_name.slice(0,1)}</span><div><strong>{item.display_name}</strong><small>@{item.username}</small></div><Status state={item.workspace.state}/></div>)}</div>:<Empty text={loading?'正在读取账号…':error?'读取失败，请刷新':'还没有研究用户，可先建立账号'}/>}</section>
              <section className="portal-card"><div className="portal-card-heading"><div><h2>最近管理操作</h2><p>账号和配置修改均保留记录</p></div><button className="portal-link-button" onClick={()=>setSection('audit')}>查看全部<ArrowUpRight size={16}/></button></div><AuditList events={audit.slice(0,6)} compact/></section>
            </div>
          </>}
          {section==='users'&&<section className="portal-card">
            <div className="portal-table-toolbar"><div><h2>用户账号</h2><p>{users.length} 个账号，停用账号会立即撤销登录</p></div><label className="portal-search"><Search size={16}/><input aria-label="搜索用户" placeholder="搜索姓名或用户名" value={query} onChange={event=>setQuery(event.target.value)}/></label></div>
            <div className="portal-table-scroll"><table className="portal-table"><thead><tr><th scope="col">用户</th><th scope="col">角色</th><th scope="col">账号状态</th><th scope="col">工作区</th><th scope="col">最近登录</th><th scope="col">操作</th></tr></thead><tbody>{filtered.map(item=><tr key={item.id}><td><div className="portal-table-user"><span className="portal-avatar">{item.display_name.slice(0,1)}</span><div><strong>{item.display_name}</strong><small>@{item.username}{item.id===user.id?' · 当前账号':''}</small></div></div></td><td>{item.role==='admin'?'管理员':'研究用户'}</td><td><span className={`portal-status ${item.enabled?'ready':'stopped'}`}>{item.enabled?'已启用':'已停用'}</span></td><td><Status state={item.workspace.state}/></td><td>{day(item.last_login_at)}</td><td><div className="portal-row-actions"><button type="button" onClick={()=>openDialog({kind:'edit',user:item})} aria-label={`编辑 ${item.display_name}`}>编辑</button><button type="button" disabled={item.id===user.id} onClick={()=>openDialog({kind:'password',user:item})} aria-label={`重置 ${item.display_name} 的密码`}>重置密码</button></div></td></tr>)}</tbody></table></div>
            {!filtered.length&&<Empty text={loading?'正在读取…':error?'读取失败，请刷新':'没有匹配的账号'}/>}
          </section>}
          {section==='workspaces'&&<section className="portal-card">
            <div className="portal-card-heading"><div><h2>独立工作区</h2><p>个人任务与成果分别保存在各自的工作区；最近刷新：{lastRefresh?new Date(lastRefresh).toLocaleTimeString('zh-CN',{hour12:false}):'—'}</p></div></div>
            <div className="portal-table-scroll"><table className="portal-table"><thead><tr><th scope="col">账号</th><th scope="col">运行状态</th><th scope="col">项目</th><th scope="col">公共文献</th><th scope="col">活动任务</th><th scope="col">操作</th></tr></thead><tbody>{users.map(item=>{
              const stoppable=!item.enabled&&['ready','starting','offline','failed'].includes(item.workspace.state);
              const reason=item.enabled?'账号启用时不可停止工作区':item.workspace.state==='stopped'?'工作区已停止':item.workspace.state==='stopping'?'正在停止':item.workspace.state==='not_started'?'尚未启动':'当前状态不可停止';
              return <tr key={item.id}><td><strong>{item.display_name}</strong><small className="portal-block">@{item.username}</small></td><td><Status state={item.workspace.state}/>{item.workspace.message&&<small className="portal-block">{item.workspace.message}</small>}</td><td>{item.workspace.metrics?.projects??'—'}</td><td>{item.workspace.metrics?.documents??'—'}</td><td>{item.workspace.metrics?.active_tasks??'—'}</td><td>{stoppable?<button type="button" className="portal-secondary" disabled={busy} onClick={()=>void stopWorkspace(item)}>停止工作区</button>:<div className="portal-row-actions"><span className="portal-block">{reason}</span>{item.enabled&&<button type="button" className="portal-link-button" onClick={()=>setSection('users')}>前往账号设置</button>}</div>}</td></tr>;
            })}</tbody></table></div>
            {!users.length&&<Empty text={loading?'正在读取账号…':error?'读取失败，请刷新':'还没有研究用户，可先建立账号'}/>}
            <p className="portal-table-note">账号停用会阻止后续访问，资料继续保留；停止工作区需先停用账号</p>
          </section>}
          {section==='models'&&<ModelPanel/>}
          {section==='audit'&&<section className="portal-card">
            <div className="portal-table-toolbar"><div><h2>操作记录</h2><p>最近 100 项管理操作，可按操作者或对象筛选</p></div><label className="portal-search"><Search size={16}/><input aria-label="筛选操作者或对象" placeholder="筛选操作者或对象" value={auditQuery} onChange={event=>setAuditQuery(event.target.value)}/></label><select aria-label="按动作筛选" value={auditAction} onChange={event=>setAuditAction(event.target.value)}><option value="">全部动作</option>{auditActions.map(action=><option key={action} value={action}>{actionNames[action]||action}</option>)}</select></div>
            <AuditList events={filteredAudit}/>
            {!filteredAudit.length&&<Empty text={audit.length?'没有匹配的操作记录':'尚无管理操作记录'}/>}
          </section>}
          {section==='account'&&<section className="portal-card portal-account-card"><AccountSettings user={user} onLogout={onLogout} embedded/></section>}
        </PageHeadingProvider>
      </div>
      <footer className="portal-admin-footer"><span>问津 · 服务管理</span><span>账号权限由服务端验证</span></footer>
    </div>
    {dialog&&<div className="portal-dialog-backdrop" role="dialog" aria-modal="true" aria-label={dialog.kind==='create'?'建立用户':dialog.kind==='password'?'重置密码':'编辑用户'}>
      <section className="portal-dialog" ref={dialogRef}>
        <div className="portal-card-heading"><h2>{dialog.kind==='create'?'建立用户':dialog.kind==='password'?'重置登录密码':'编辑用户'}</h2><button type="button" className="portal-icon-button" aria-label="关闭对话框" disabled={busy} onClick={closeDialog}><X size={18}/></button></div>
        <form className="portal-form" onSubmit={event=>{event.preventDefault();void saveUser();}}>
          {dialog.kind==='create'&&<label>用户名<input autoComplete="off" required minLength={3} maxLength={48} pattern="[a-z0-9][a-z0-9_.\-]{2,47}" value={username} onChange={event=>setUsername(event.target.value)} disabled={busy}/><small>3–48 个小写字母、数字、点、下划线或连字符</small></label>}
          {dialog.kind!=='password'&&<><label>显示名称<input required maxLength={80} value={displayName} onChange={event=>setDisplayName(event.target.value)} disabled={busy}/></label><label>账号角色<select value={role} onChange={event=>setRole(event.target.value as 'user'|'admin')} disabled={busy||dialog.kind==='edit'&&dialog.user.id===user.id}><option value="user">研究用户</option><option value="admin">管理员</option></select>{dialog.kind==='edit'&&dialog.user.id===user.id&&<small>不能修改当前账号的角色</small>}</label></>}
          {dialog.kind==='edit'&&<label className="portal-checkbox"><input type="checkbox" checked={enabled} onChange={event=>setEnabled(event.target.checked)} disabled={busy||dialog.user.id===user.id}/>启用账号{dialog.user.id===user.id?<small>当前账号不能停用自己</small>:!enabled&&<small>停用后该账号的登录立即失效，资料保留</small>}</label>}
          {dialog.kind!=='edit'&&<>
            <label>临时密码<input type={showPassword?'text':'password'} autoComplete="new-password" minLength={12} maxLength={256} required value={password} onChange={event=>setPassword(event.target.value)} disabled={busy} aria-describedby="temp-password-help"/><small id="temp-password-help">至少 12 个字符，用户首次登录需修改</small></label>
            <label>确认临时密码<input type={showPassword?'text':'password'} autoComplete="new-password" minLength={12} maxLength={256} required value={confirmPassword} onChange={event=>setConfirmPassword(event.target.value)} disabled={busy}/></label>
            <div className="portal-actions">
              <button type="button" className="portal-secondary" disabled={busy} onClick={()=>{const next=randomPassword();setPassword(next);setConfirmPassword(next);setShowPassword(true);setCopied(false);}}><RefreshCw size={16}/>生成随机密码</button>
              <button type="button" className="portal-secondary" disabled={busy} aria-pressed={showPassword} onClick={()=>setShowPassword(value=>!value)}>{showPassword?<EyeOff size={18}/>:<Eye size={18}/>}{showPassword?'隐藏密码':'显示密码'}</button>
              {password&&<button type="button" className="portal-secondary" disabled={busy} onClick={()=>void copyText(password)}><Copy size={16}/>{copied?'已复制':'复制密码'}</button>}
            </div>
            <p className="portal-notice" role="note"><KeyRound size={16}/>{dialog.kind==='password'?'重置后原登录立即失效，用户下次登录需修改密码':'账号建立后，用户首次登录需修改临时密码'}</p>
          </>}
          {error&&<p role="alert" className="portal-alert">{error}</p>}
          <div className="portal-actions"><button type="button" className="portal-secondary" disabled={busy} onClick={closeDialog}>取消</button><button className="portal-primary" disabled={busy}>{busy?'正在保存…':dialog.kind==='password'?'重置密码并撤销原登录':dialog.kind==='edit'&&dialog.user.enabled&&!enabled?'停用账号并撤销登录':'保存'}</button></div>
        </form>
      </section>
    </div>}
    {issued&&<div className="portal-dialog-backdrop" role="dialog" aria-modal="true" aria-label="临时密码">
      <section className="portal-dialog">
        <div className="portal-card-heading"><h2>{issued.kind==='create'?'账号已建立':'密码已重置'}</h2><button type="button" className="portal-icon-button" aria-label="关闭对话框" onClick={()=>setIssued(null)}><X size={18}/></button></div>
        <div className="portal-form"><p>请把下面的临时密码交给 <strong>@{issued.username}</strong>，该密码只显示这一次</p><label>临时密码<input readOnly value={issued.password} aria-label="临时密码" onFocus={event=>event.currentTarget.select()}/><small>{issued.kind==='password'?'用户首次登录后需要修改密码；重置前的登录已失效':'用户首次登录后需要修改密码'}</small></label><div className="portal-actions"><button type="button" className="portal-secondary" onClick={()=>void copyText(issued.password)}><Copy size={16}/>{copied?'已复制':'复制临时密码'}</button><button type="button" className="portal-primary" autoFocus onClick={()=>setIssued(null)}>完成</button></div></div>
      </section>
    </div>}
  </main>;
}

function Status({state}:{state:string}){return <span className={`portal-status ${state}`}>{stateNames[state]||state}</span>;}
function Empty({text}:{text:string}){return <div className="portal-empty"><Users size={28}/><p>{text}</p></div>;}
function AuditList({events,compact=false}:{events:Audit[];compact?:boolean}){
  if(!events.length)return <Empty text="尚无管理操作记录"/>;
  return <div className="portal-table-scroll portal-audit-table"><table className="portal-table"><thead><tr><th scope="col">时间</th><th scope="col">操作者</th><th scope="col">动作</th>{!compact&&<><th scope="col">对象</th><th scope="col">详情</th></>}</tr></thead><tbody>{events.map(event=><tr key={event.id}><td className="portal-audit-time">{date(event.created_at)}</td><td>{event.actor||'系统初始化'}</td><td>{actionNames[event.action]||event.action}</td>{!compact&&<><td>{event.target||event.target_id?.slice(0,8)||'—'}</td><td title={auditDetail(event)}>{auditDetail(event)}</td></>}</tr>)}</tbody></table></div>;
}
const auditKeyNames:Record<string,string>={method:'方式',path:'接口',model:'模型',archived:'归档',enabled:'启用'};
function auditDetail(event:Audit){
  try{
    const value=JSON.parse(event.details||'{}') as Record<string,unknown>;
    const entries=Object.entries(value).filter(([,item])=>item!==undefined&&item!==null&&item!=='');
    if(!entries.length)return '—';
    return entries.map(([key,item])=>`${auditKeyNames[key]||key}：${typeof item==='string'?item:JSON.stringify(item)}`).join(' · ');
  }catch{return event.details||'—';}
}

type Models={base_url:string;model:string;provider_name:string;context_tokens:number|null;max_output_tokens:number|null;has_api_key:boolean;api_key_managed:boolean;database_mode:string};
function ModelPanel(){
  const [value,setValue]=useState<Models|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(true);
  const load=useCallback(async()=>{setLoading(true);setError('');try{const result=await call<Models>({path:'/api/admin/settings',silent:true});setValue(result);}catch(e){setError(e instanceof Error?e.message:'模型配置读取失败');}finally{setLoading(false);}},[]);
  useEffect(()=>{void load();},[load]);
  async function save(){
    if(!value)return;
    setBusy(true);setError('');setNotice('');
    try{
      const {base_url,model,provider_name,context_tokens,max_output_tokens}=value;
      setValue(await call<Models>({path:'/api/admin/settings',method:'PUT',body:{base_url,model,provider_name,context_tokens,max_output_tokens},silent:true}));
      setNotice('模型配置已保存，运行中的工作区会自动同步');
    }catch(e){setError(e instanceof Error?e.message:'模型配置保存失败');}
    finally{setBusy(false);}
  }
  const reading=loading&&!value;
  return <section className="portal-card portal-model-card">
    <div className="portal-card-heading"><h2>连接信息与容量限制</h2></div>
    {reading?<p role="status">正在读取配置…</p>:(!value&&error)?<div className="portal-alert" role="alert"><p>{error}</p><button type="button" className="portal-secondary" onClick={()=>void load()}>重新读取</button></div>:value&&<form className="portal-form" onSubmit={event=>{event.preventDefault();void save();}}>
      <label>服务名称<input maxLength={100} value={value.provider_name} onChange={event=>setValue({...value,provider_name:event.target.value})} disabled={busy}/></label>
      <label>模型服务地址<input type="url" placeholder="https://api.your-provider.com/v1" maxLength={500} value={value.base_url} onChange={event=>setValue({...value,base_url:event.target.value})} disabled={busy}/></label>
      <label>模型名称<input maxLength={200} value={value.model} onChange={event=>setValue({...value,model:event.target.value})} disabled={busy}/></label>
      <div className="portal-form-grid"><label>上下文 token 容量<input type="number" placeholder="如 128000" min={4096} max={2000000} value={value.context_tokens??''} onChange={event=>setValue({...value,context_tokens:event.target.value?Number(event.target.value):null})} disabled={busy}/></label><label>最大输出 token<input type="number" placeholder="如 4096" min={256} max={262144} value={value.max_output_tokens??''} onChange={event=>setValue({...value,max_output_tokens:event.target.value?Number(event.target.value):null})} disabled={busy}/></label></div>
      <div className="portal-config-facts"><div><span>模型密钥</span><strong>{value.has_api_key?'已由服务器配置':'尚未配置'}</strong></div><p>密钥由服务器的 SHIXUE_MODEL_API_KEY 管理，页面不读取或显示密钥</p><div><span>数据隔离</span><strong>公共资料库＋个人研究分区</strong></div></div>
      {error&&<p className="portal-alert" role="alert">{error}</p>}
      {notice&&<p className="portal-notice" role="status">{notice}</p>}
      <div className="portal-actions"><button className="portal-primary" disabled={busy}>{busy?'正在保存…':'保存模型配置'}</button></div>
    </form>}
  </section>;
}
