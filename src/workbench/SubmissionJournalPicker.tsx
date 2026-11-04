import { useEffect, useState } from 'react';
import { request } from './api';

export type SubmissionJournal={public_notes?:string;personal_legacy?:boolean;catalog_kind?:'english';catalog_id?:string;issn?:string;id:string;name:string;discipline:string;level:string;website:string;submission_url:string;notes:string;favorite:boolean|number;revision:number};
type EnglishItem={id:string;name:string;issn:string;cas_cat:string;jcr_q:string};
type EnglishPage={items:EnglishItem[];total:number;pages:number};
const fromEnglish=(j:EnglishItem):SubmissionJournal=>({id:`english:${j.id}`,name:j.name,issn:j.issn,catalog_kind:'english',discipline:j.cas_cat,level:j.jcr_q,website:'',submission_url:'',notes:'',favorite:false,revision:1});

export default function SubmissionJournalPicker({journals,value,onChange}:{journals:SubmissionJournal[];value:string|null;onChange(id:string|null):void}) {
  const [catalog,setCatalog]=useState(value?.startsWith('english:')?'english':'chinese');
  const [query,setQuery]=useState(''),[page,setPage]=useState(1),[reload,setReload]=useState(0);
  const [remote,setRemote]=useState<EnglishPage|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [chosen,setChosen]=useState<SubmissionJournal|null>(null);
  useEffect(()=>{
    if(!value?.startsWith('english:')||journals.some(j=>j.id===value)||chosen?.id===value)return;
    let live=true;
    void request<EnglishItem>(`/api/submissions/english-journals/${encodeURIComponent(value.slice(8))}`).then(j=>{if(live)setChosen(fromEnglish(j));}).catch(e=>{if(live)setError(e instanceof Error?e.message:'已选英文期刊读取失败');});
    return ()=>{live=false;};
  },[value,journals,chosen?.id]);
  useEffect(()=>{
    if(catalog!=='english')return;
    let live=true;setLoading(true);setError('');
    const timer=setTimeout(()=>{
      const params=new URLSearchParams({q:query.trim(),page:String(page),page_size:'30',sort:'name'});
      void request<EnglishPage>(`/api/submissions/english-journals?${params}`).then(result=>{if(live)setRemote(result);}).catch(e=>{if(live){setRemote(null);setError(e instanceof Error?e.message:'英文期刊读取失败');}}).finally(()=>{if(live)setLoading(false);});
    },250);
    return ()=>{live=false;clearTimeout(timer);};
  },[catalog,query,page,reload]);
  const english:SubmissionJournal[]=(remote?.items||[]).map(fromEnglish);
  const selected=journals.find(j=>j.id===value)||(chosen?.id===value?chosen:null)||english.find(j=>j.id===value);
  const candidates=catalog==='english'?english:journals.filter(j=>j.catalog_kind!=='english'&&(!query||`${j.name} ${j.discipline}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const options=selected?[selected,...candidates.filter(j=>j.id!==selected.id)]:candidates;
  function switchCatalog(next:string){if(catalog===next)return;setCatalog(next);setQuery('');setPage(1);setRemote(null);setError('');}
  return <section className="submission-journal-picker" aria-label="选择目标期刊">
    <div className="submission-view-toggle" role="group" aria-label="目标期刊目录"><button type="button" aria-pressed={catalog==='chinese'} onClick={()=>switchCatalog('chinese')}>中文期刊</button><button type="button" aria-pressed={catalog==='english'} onClick={()=>switchCatalog('english')}>英文期刊</button></div>
    <label className="form-label">查找期刊<input aria-label="筛选目标期刊" placeholder={catalog==='english'?'输入英文刊名或 ISSN':'输入中文刊名或学科'} value={query} maxLength={200} onChange={e=>{setQuery(e.target.value);setPage(1);setRemote(null);}}/></label>
    <label className="form-label">目标期刊<select aria-label="目标期刊" value={value||''} onChange={e=>{const id=e.target.value||null;setChosen(options.find(j=>j.id===id)||null);onChange(id);}}><option value="">尚未选择</option>{value&&!selected&&<option value={value}>当前已选期刊</option>}{options.map(j=><option key={j.id} value={j.id}>{j.id===value?'当前已选 · ':''}{j.name}{j.issn?` · ISSN ${j.issn}`:j.level?` · ${j.level}`:''}</option>)}</select></label>
    {selected&&<p className="scope-note">已选：{selected.name} · {selected.catalog_kind==='english'?'英文期刊':'中文期刊'}</p>}
    {catalog==='english'&&<div className="submission-picker-status" role="status">{loading?'正在查找英文期刊…':remote?`找到 ${remote.total.toLocaleString()} 种 · 第 ${page} / ${Math.max(1,remote.pages)} 页`:''}<button type="button" className="button-quiet" disabled={loading||page<=1} onClick={()=>setPage(p=>p-1)}>上一页</button><button type="button" className="button-quiet" disabled={loading||!remote||page>=remote.pages} onClick={()=>setPage(p=>p+1)}>下一页</button></div>}
    {error&&<p role="alert" className="scope-warning">{error}<button type="button" className="button-quiet" onClick={()=>setReload(n=>n+1)}>重新加载英文期刊</button></p>}
  </section>;
}
