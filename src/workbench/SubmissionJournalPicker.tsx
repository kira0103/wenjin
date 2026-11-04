import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDown, Search, X } from 'lucide-react';
import { call } from './transport';

export type SubmissionJournal={public_notes?:string;personal_legacy?:boolean;catalog_kind?:'english';catalog_id?:string;issn?:string;id:string;name:string;discipline:string;level:string;website:string;submission_url:string;notes:string;favorite:boolean|number;revision:number};
type EnglishItem={id:string;name:string;issn:string;cas_cat:string;jcr_q:string};
type EnglishPage={items:EnglishItem[];total:number;pages:number};
const MAX_CANDIDATES=50;
const fromEnglish=(j:EnglishItem):SubmissionJournal=>({id:`english:${j.id}`,name:j.name,issn:j.issn,catalog_kind:'english',discipline:j.cas_cat,level:j.jcr_q,website:'',submission_url:'',notes:'',favorite:false,revision:1});
const message=(error:unknown)=>error instanceof Error?error.message:'期刊读取失败';

/** 目标期刊选择：搜索式 combobox，输入过滤 ≤50 条候选，避免全量原生 option。 */
export default function SubmissionJournalPicker({journals,value,onChange}:{journals:SubmissionJournal[];value:string|null;onChange(id:string|null):void}) {
  const [catalog,setCatalog]=useState(value?.startsWith('english:')?'english':'chinese');
  const [query,setQuery]=useState(''),[open,setOpen]=useState(false),[active,setActive]=useState(0);
  const [page,setPage]=useState(1),[reload,setReload]=useState(0);
  const [remote,setRemote]=useState<EnglishPage|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [chosen,setChosen]=useState<SubmissionJournal|null>(null);
  const listId=useId();
  const box=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!value?.startsWith('english:')||journals.some(j=>j.id===value)||chosen?.id===value)return;
    let live=true;
    void call<EnglishItem>({path:`/api/submissions/english-journals/${encodeURIComponent(value.slice(8))}`,silent:true}).then(j=>{if(live)setChosen(fromEnglish(j));}).catch(e=>{if(live)setError(message(e));});
    return ()=>{live=false;};
  },[value,journals,chosen?.id]);
  useEffect(()=>{
    if(catalog!=='english')return;
    let live=true;setLoading(true);setError('');
    const timer=setTimeout(()=>{
      const params=new URLSearchParams({q:query.trim(),page:String(page),page_size:'30',sort:'name'});
      void call<EnglishPage>({path:`/api/submissions/english-journals?${params}`,silent:true}).then(result=>{if(live)setRemote(result);}).catch(e=>{if(live){setRemote(null);setError(message(e));}}).finally(()=>{if(live)setLoading(false);});
    },250);
    return ()=>{live=false;clearTimeout(timer);};
  },[catalog,query,page,reload]);
  useEffect(()=>{const dismiss=(event:PointerEvent)=>{if(box.current&&!box.current.contains(event.target as Node))setOpen(false);};document.addEventListener('pointerdown',dismiss);return()=>document.removeEventListener('pointerdown',dismiss);},[]);
  const english:SubmissionJournal[]=(remote?.items||[]).map(fromEnglish);
  const selected=journals.find(j=>j.id===value)||(chosen?.id===value?chosen:null)||english.find(j=>j.id===value)||null;
  const candidates=useMemo(()=>{
    const keyword=query.trim().toLocaleLowerCase();
    const pool=catalog==='english'?english:journals.filter(j=>j.catalog_kind!=='english');
    const matched=keyword?pool.filter(j=>`${j.name} ${j.discipline} ${j.level} ${j.issn||''}`.toLocaleLowerCase().includes(keyword)):pool;
    return [...matched].sort((a,b)=>Number(!!b.favorite)-Number(!!a.favorite)||a.name.localeCompare(b.name,'zh-CN')).slice(0,MAX_CANDIDATES);
  },[catalog,english,journals,query]);
  function switchCatalog(next:string){if(catalog===next)return;setCatalog(next);setQuery('');setPage(1);setRemote(null);setError('');setOpen(false);setActive(0);}
  function choose(id:string){const target=candidates.find(journal=>journal.id===id)||null;setChosen(target);onChange(id);setOpen(false);setQuery('');setActive(0);}
  function clear(){onChange(null);setChosen(null);setQuery('');setOpen(false);}
  function onKeyDown(event:React.KeyboardEvent<HTMLInputElement>){
    if(event.key==='ArrowDown'){event.preventDefault();setOpen(true);setActive(index=>Math.min(index+1,Math.max(0,candidates.length-1)));}
    else if(event.key==='ArrowUp'){event.preventDefault();setOpen(true);setActive(index=>Math.max(index-1,0));}
    else if(event.key==='Home'&&open){event.preventDefault();setActive(0);}
    else if(event.key==='End'&&open){event.preventDefault();setActive(Math.max(0,candidates.length-1));}
    else if(event.key==='Enter'){if(open&&candidates[active]){event.preventDefault();choose(candidates[active].id);}}
    else if(event.key==='Escape'){setOpen(false);}
  }
  const inputId=`${listId}-input`,statusId=`${listId}-status`;
  return <section className="submission-journal-picker" aria-label="选择目标期刊">
    <div className="submission-view-toggle" role="group" aria-label="目标期刊目录"><button type="button" aria-pressed={catalog==='chinese'} onClick={()=>switchCatalog('chinese')}>中文期刊</button><button type="button" aria-pressed={catalog==='english'} onClick={()=>switchCatalog('english')}>英文期刊</button></div>
    <div className="journal-combobox" ref={box}>
      <label className="form-label" htmlFor={inputId}>目标期刊</label>
      <div className="journal-combobox-field">
        <Search size={16} aria-hidden="true"/>
        <input id={inputId} role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list" aria-describedby={statusId} aria-activedescendant={open&&candidates[active]?`${listId}-option-${active}`:undefined} autoComplete="off" maxLength={200} value={query} placeholder={catalog==='english'?'搜索英文刊名或 ISSN':'搜索刊名、学科或等级'} onChange={event=>{setQuery(event.target.value);setOpen(true);setActive(0);setPage(1);setRemote(null);}} onFocus={()=>setOpen(true)} onKeyDown={onKeyDown}/>
        {selected?<button type="button" className="journal-combobox-clear" aria-label={`清除已选期刊 ${selected.name}`} onClick={clear}><X size={16}/></button>:<ChevronDown size={16} aria-hidden="true"/>}
      </div>
      <p className="journal-combobox-status" id={statusId} role="status">{catalog==='english'?(loading?'正在查找英文期刊…':remote?`匹配 ${remote.total.toLocaleString()} 种 · 第 ${page} / ${Math.max(1,remote.pages)} 页`:''):`匹配 ${candidates.length} 种${candidates.length>=MAX_CANDIDATES?'，仅显示前 50 条，可继续输入缩小范围':''}`}</p>
      {open&&<ul className="journal-combobox-list" id={listId} role="listbox" aria-label="目标期刊候选">
        {candidates.map((journal,index)=><li key={journal.id} id={`${listId}-option-${index}`} role="option" aria-selected={journal.id===value} className={index===active?'is-active':''} onMouseDown={event=>event.preventDefault()} onMouseEnter={()=>setActive(index)} onClick={()=>choose(journal.id)}>
          <div><strong>{journal.name}</strong><small>{[journal.level,journal.discipline,journal.issn?`ISSN ${journal.issn}`:''].filter(Boolean).join(' · ')||'等级与学科未填写'}</small></div>
          {!!journal.favorite&&<span className="journal-combobox-star">已收藏</span>}
        </li>)}
        {!candidates.length&&<li className="journal-combobox-empty" aria-disabled="true">{loading?'正在查找…':'没有匹配的期刊，可缩短关键词或切换目录'}</li>}
      </ul>}
    </div>
    {selected&&<p className="scope-note">已选：{selected.name}{selected.level?` · ${selected.level}`:''}{selected.discipline?` · ${selected.discipline}`:''} · {selected.catalog_kind==='english'?'英文期刊':'中文期刊'}</p>}
    {catalog==='english'&&<div className="submission-picker-status" role="group" aria-label="英文期刊翻页"><button type="button" className="button-quiet" disabled={loading||page<=1} onClick={()=>setPage(value=>value-1)}>上一页</button><button type="button" className="button-quiet" disabled={loading||!remote||page>=remote.pages} onClick={()=>setPage(value=>value+1)}>下一页</button></div>}
    {error&&<p role="alert" className="scope-warning">{error}<button type="button" className="button-quiet" onClick={()=>setReload(value=>value+1)}>重新加载英文期刊</button></p>}
  </section>;
}
