import { useEffect, useRef, useState } from 'react';
import { Folder, FileText, ChevronRight, MoreHorizontal } from 'lucide-react';
import { request } from '../api';
import { Modal } from '../Pages';
import { directoryParams, directoryScope, type DirectoryLocation, type DocumentGroups, type DirectoryResult, type DocumentEntry, type LibraryScope } from './documentTypes';

export default function DocumentBrowser({location,onLocation,onOpen,onUse,onImport,revision,processing,readOnly=false}:{
  location:DirectoryLocation;onLocation(v:DirectoryLocation):void;onOpen(id:string,asset?:string):void;
  onUse(entry:DocumentEntry):void;onImport(name:string):void;revision:number;processing:boolean;
  readOnly?:boolean;
}) {
  const [groups,setGroups]=useState<DocumentGroups|null>(null),[result,setResult]=useState<DirectoryResult|null>(null);
  const [query,setQuery]=useState(''),[kind,setKind]=useState('all'),[chosen,setChosen]=useState<string[]>([]);
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [editing,setEditing]=useState<{action:'rename'|'delete'|'move';name:string;scope?:LibraryScope;label?:string}|null>(null);
  const generation=useRef(0),menu=useRef<HTMLDetailsElement>(null);
  const group=groups?.items.find(g=>g.id===location.group),name=location.group==='uncategorized'?'未分类':group?.name||'全部文献';
  const overview=!location.group&&!query;
  async function load(offset=0,quiet=false) {
    const ticket=++generation.current;if(!quiet)setLoading(true);
    try {
      const params=directoryParams(location);params.set('kind',kind);params.set('query',query);params.set('offset',String(offset));
      const [g,items]=await Promise.all([request<DocumentGroups>('/api/knowledge/document-groups'),overview?Promise.resolve(null):request<DirectoryResult>('/api/knowledge/document-tree?'+params)]);
      if(ticket===generation.current) {
        setError('');setGroups(g);setResult(v=>items&&offset&&v?{...items,files:[...v.files,...items.files]}:items);
        if(location.group&&location.group!=='uncategorized'&&!g.items.some(c=>c.id===location.group))onLocation({group:'',path:''});
      }
    } catch(e) {if(ticket===generation.current)setError(e instanceof Error?e.message:'目录读取失败');}
    finally {if(ticket===generation.current)setLoading(false);}
  }
  useEffect(()=>{setChosen([]);setResult(null);setLoading(true);if(menu.current)menu.current.open=false;const timer=setTimeout(()=>void load(),200);return()=>{clearTimeout(timer);generation.current++;};},[location.group,location.path,query,kind,revision]);
  useEffect(()=>{if(!processing){void load(0,true);return;}const timer=setInterval(()=>void load(0,true),3000);return()=>clearInterval(timer);},[processing,location.group,location.path,query,kind]);
  function chooseGroup(id:string){setQuery('');onLocation({group:id,path:''});}
  function edit(value:NonNullable<typeof editing>){if(menu.current)menu.current.open=false;setEditing(value);}
  function move(scope:LibraryScope,label:string){edit({action:'move',name:'',scope,label});}
  async function save(){if(!editing||!groups)return;setBusy(true);setError('');try{
    if(editing.action==='move')await request('/api/knowledge/document-entries/move','POST',{scope:editing.scope,category_name:editing.name,revision:groups.revision});
    else if(group&&editing.action==='rename')await request('/api/knowledge/document-groups/'+group.id,'PATCH',{name:editing.name,version:group.version});
    else if(group)await request(`/api/knowledge/document-groups/${group.id}?version=${group.version}`,'DELETE');
    setEditing(null);setChosen([]);await load();
  }catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  return <div className="kb-file-layout">
    <aside className="kb-file-groups" aria-label="导入分类"><h2>分类</h2>
      <button className={!location.group?'is-selected':''} onClick={()=>chooseGroup('')}><Folder size={16}/>全部分类<span>{groups?.total??'…'}</span></button>
      <button className={location.group==='uncategorized'?'is-selected':''} onClick={()=>chooseGroup('uncategorized')}><Folder size={16}/>未分类<span>{groups?.uncategorized??'…'}</span></button>
      {groups?.items.map(g=><button key={g.id} className={location.group===g.id?'is-selected':''} onClick={()=>chooseGroup(g.id)}><Folder size={16}/><strong>{g.name}</strong><span>{g.files}</span></button>)}
    </aside>
    <section className="kb-file-content" aria-label="原始目录">
      <div className="kb-file-heading"><nav className="kb-file-breadcrumb" aria-label="文献路径"><button className="button-quiet" onClick={()=>chooseGroup('')}>文献库</button>
        {location.group&&<><ChevronRight size={14}/><button className="button-quiet" onClick={()=>onLocation({...location,path:''})}>{name}</button></>}
        {location.path.split('/').filter(Boolean).map((p,i,a)=><span key={i}><ChevronRight size={14}/><button className="button-quiet" onClick={()=>onLocation({...location,path:a.slice(0,i+1).join('/')})}>{p}</button></span>)}
      </nav>{location.group&&!readOnly&&<details ref={menu} className="kb-directory-menu"><summary aria-label="分类与目录管理" title="分类与目录管理"><MoreHorizontal size={19}/></summary><div className="kb-directory-menu-items">
        <button onClick={()=>{if(menu.current)menu.current.open=false;onImport(name);}}>导入到此分类</button>
        <button onClick={()=>move(directoryScope(location),location.path||name)}>修改{location.path?'目录':'整类文献'}分类</button>
        {group&&<><button onClick={()=>edit({action:'rename',name:group.name})}>重命名分类</button><button onClick={()=>edit({action:'delete',name:group.name})}>删除分类</button></>}
      </div></details>}</div>
      <div className="kb-directory-search"><input aria-label="目录文献搜索" placeholder="搜索文件名、路径或文献题名" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="目录文献类型" value={kind} onChange={e=>setKind(e.target.value)}><option value="all">全部类型</option><option value="ancient">古籍</option><option value="papers">论文</option></select></div>
      {error&&!editing&&<p role="alert" className="persistent-error">{error}<button className="button-quiet" onClick={()=>void load()}>重试</button></p>}
      {overview?groups?<div className="kb-group-cards">{[{id:'uncategorized',name:'未分类',files:groups.uncategorized},...groups.items].map(g=><button key={g.id} className="kb-group-card" onClick={()=>chooseGroup(g.id)}><Folder size={24}/><strong>{g.name}</strong><span>{g.files.toLocaleString()} 个文件</span></button>)}</div>:<p role="status">正在读取分类…</p>:<>
        {!readOnly&&!!chosen.length&&<div className="kb-file-selection"><span>已选 {chosen.length} 个文件</span><button className="button-secondary" onClick={()=>move({document_entry_ids:chosen},`${chosen.length} 个文件`)}>修改分类</button><button className="button-quiet" onClick={()=>setChosen([])}>取消选择</button></div>}
        <div className="kb-directory-list">{result?.directories.map(d=><button className="kb-directory-row" key={d.path} onClick={()=>{setQuery('');onLocation({...location,path:d.path});}}><Folder size={20}/><strong>{d.name}</strong><span>{d.files.toLocaleString()} 个文件</span><ChevronRight size={16}/></button>)}</div>
        {!!result?.files.length&&<div className="kb-file-table"><table><colgroup><col className="kb-select-column"/><col/><col className="kb-type-column"/><col className="kb-actions-column"/></colgroup><thead><tr>
          <th><input type="checkbox" hidden={readOnly} aria-label="选择当前文件" checked={result.files.every(f=>chosen.includes(f.id))} onChange={e=>setChosen(e.target.checked?result.files.slice(0,500).map(f=>f.id):[])}/></th><th>文件名称</th><th>类型</th><th>操作</th>
        </tr></thead><tbody>{result.files.map(f=><tr key={f.id}>
          <td><input type="checkbox" hidden={readOnly} aria-label={'选择文件'+f.relative_path} checked={chosen.includes(f.id)} disabled={!chosen.includes(f.id)&&chosen.length>=500} onChange={e=>setChosen(v=>e.target.checked?[...v,f.id]:v.filter(id=>id!==f.id))}/></td>
          <td><button className="kb-file-name" title={f.relative_path} onClick={()=>onOpen(f.edition_id,f.asset_id||undefined)}><FileText size={17}/><span>{f.relative_path.split('/').at(-1)}</span></button>{query&&<small className="kb-file-path">{f.group_name||'未分类'} / {f.relative_path}</small>}</td>
          <td><span className="kb-file-kind">{f.item_type==='ancient'?'古籍':['article','thesis'].includes(f.item_type)?'论文':'文献'}</span></td>
          <td><div className="kb-file-actions"><button className="button-quiet" onClick={()=>onOpen(f.edition_id,f.asset_id||undefined)}>阅读</button><button className="button-quiet" onClick={()=>onUse(f)}>检索</button>{!readOnly&&<button className="button-quiet" onClick={()=>move({document_entry_ids:[f.id]},f.relative_path)}>修改分类</button>}</div></td>
        </tr>)}</tbody></table></div>}
        {loading&&!result&&<p role="status">正在读取目录…</p>}{!loading&&!result?.files.length&&!result?.directories.length&&<p className="empty-block">{query?'未找到匹配的文件':'此目录暂无文件'}</p>}
        {result?.next_offset!==null&&result?.next_offset!==undefined&&<button className="button-secondary kb-load-more" disabled={loading} onClick={()=>void load(result.next_offset!)}>加载更多</button>}
      </>}
    </section>
    {editing&&<Modal title={editing.action==='rename'?'重命名分类':editing.action==='delete'?'删除分类':'修改文献分类'} onClose={()=>{if(!busy)setEditing(null);}}>
      {editing.action==='delete'?<p>删除“{editing.name}”分类。文献及原目录会移到“未分类”，原件与历史引用保留。</p>:<><p>{editing.action==='move'?`将“${editing.label}”移入下面的分类，保留原有目录路径。`:'填写新的分类名称。'}</p><label className="form-label">分类名称<input aria-label="编辑分类名称" list="document-group-names" maxLength={100} placeholder={editing.action==='move'?'留空为未分类':''} value={editing.name} onChange={e=>setEditing({...editing,name:e.target.value})}/></label><datalist id="document-group-names">{groups?.items.map(g=><option key={g.id} value={g.name}/>)}</datalist></>}
      {error&&<p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions"><button className="button-secondary" disabled={busy} onClick={()=>setEditing(null)}>取消</button><button className="button-primary" disabled={busy||editing.action==='rename'&&!editing.name.trim()} onClick={()=>void save()}>{editing.action==='delete'?'确认删除分类':'保存分类'}</button></div>
    </Modal>}
  </div>;
}
