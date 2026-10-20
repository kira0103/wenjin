import { useEffect, useRef, useState } from 'react';
import { BookOpen, Search } from 'lucide-react';
import { request, type Project } from '../api';
import { sourceRange } from './sourceOffsets';

export type CoreLocation={id:string;asset?:string;page?:number;start?:number;end?:number};
type Evidence={evidence_id:string;anchor_id:string;quote:string;locator:{title:string;edition_label:string;asset_id:string;page_index:number}};
type Corpus={id:string;name:string;state:string;source_path:string;source_revision:string;statistics:{books?:number;files?:number;bytes?:number;processed?:number;source_books?:number;source_files?:number;failed_books?:number};errors:{source_key:string;error:string}[]};
type Book={edition_id:string;title:string;category:string;source_key:string;file_count:number;authors_json:string[];date_label:string};
type Hit={passage_id:number;edition_id:string;block_id:string;title:string;asset_id:string;page_index:number;section_path:string;source_path:string;source_start:number;start_offset:number;end_offset:number;snippet:string;snippet_start:number;quote:string;category:string};
type File={source_path:string;asset_id:string;title:string;pages:number;sha256:string;metadata:Record<string,unknown>};
type Manifest={id:string;title:string;label:string;active_revision:string;files:File[];core:{corpus_name:string;source_revision:string;metadata:Record<string,unknown>}};
type Block={id:string;asset_id:string;page_index:number;body:string;quality:{source_start:number;primary_length:number}};
const categories=['儒藏','史藏','子藏','集藏','诗藏','艺藏','易藏','医藏','佛藏','道藏','四庫全書'];
const labels:Record<string,string>={ready:'导入完成',importing:'正在导入',partial:'部分文献需要核验',pending:'等待导入',paused:'导入已停止，可继续更新'};
function Highlight({text,start,end}:{text:string;start:number;end:number}){const chars=Array.from(text);return <>{chars.slice(0,start).join('')}<mark>{chars.slice(start,end).join('')}</mark>{chars.slice(end).join('')}</>;}

export default function CoreLibrary({projects,projectId,location,onEvidence,onUse,onChanged,onBack}:{projects:Project[];projectId:string;location:CoreLocation|null;onBack?():void;onEvidence(value:Evidence):void;onUse(book:{id:string;title:string;label:string;asset?:string}):void;onChanged():Promise<void>}){
  const [corpora,setCorpora]=useState<Corpus[]>([]),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const [view,setView]=useState<'books'|'search'>('books'),[cats,setCats]=useState(categories),[query,setQuery]=useState(''),[mode,setMode]=useState('literal'),[limit,setLimit]=useState(20);
  const [books,setBooks]=useState<Book[]>([]),[bookCursor,setBookCursor]=useState<string|null>(null),[bookQuery,setBookQuery]=useState('');
  const [hits,setHits]=useState<Hit[]>([]),[next,setNext]=useState<string|null>(null),[searched,setSearched]=useState(false),[scope,setScope]=useState<{id:string;title:string}|null>(null);
  const [submitted,setSubmitted]=useState<Record<string,unknown>|null>(null),[history,setHistory]=useState<(string|null)[]>([]);
  const [source,setSource]=useState(''),[reader,setReader]=useState<Manifest|null>(null),[asset,setAsset]=useState(''),[page,setPage]=useState(1),[blocks,setBlocks]=useState<Block[]>([]),[focus,setFocus]=useState<CoreLocation|null>(null);
  const [project,setProject]=useState(projectId),[wholeBook,setWholeBook]=useState(false);
  const generation=useRef(0),readerGeneration=useRef(0),pageGeneration=useRef(0);
  useEffect(()=>setProject(projectId),[projectId]);
  async function act(fn:()=>Promise<void>){setError('');setMessage('');setBusy(true);try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作未完成');}finally{setBusy(false);}}
  async function refresh(){const value=await request<{items:Corpus[]}>('/api/knowledge/core/status');setCorpora(value.items);}
  useEffect(()=>{if(location)return;void refresh().catch(e=>setError(e.message));const timer=setInterval(()=>void refresh().catch(()=>{}),4000);return()=>clearInterval(timer);},[!!location]);
  async function loadBooks(cursor:string|null=null,append=false){
    const id=++generation.current;
    if(!cats.length){setBooks([]);setBookCursor(null);return;}
    const params=new URLSearchParams({query:bookQuery,categories:cats.join(','),cursor:cursor||'',limit:'30'});
    const value=await request<{items:Book[];next_cursor:string|null}>('/api/knowledge/core/books?'+params);
    if(id!==generation.current)return;
    setBooks(v=>append?[...v,...value.items]:value.items);setBookCursor(value.next_cursor);
  }
  const categoryKey=cats.join(',');
  const imported=corpora.reduce((n,c)=>n+(c.statistics.books||c.statistics.processed||0),0);
  useEffect(()=>{if(!location)void loadBooks().catch(e=>setError(e.message));},[categoryKey,imported,!!location]);
  async function openReader(value:CoreLocation){
    const id=++readerGeneration.current;setBlocks([]);setFocus(value);
    const manifest=await request<Manifest>('/api/knowledge/core/editions/'+value.id);
    if(id!==readerGeneration.current)return;
    setReader(manifest);setAsset(value.asset||manifest.files[0]?.asset_id||'');setPage(value.page||1);setWholeBook(false);
  }
  useEffect(()=>{if(location)void act(()=>openReader(location));},[location]);
  useEffect(()=>{
    const id=++pageGeneration.current;setBlocks([]);
    if(!reader||!asset)return;
    void request<Block[]>(`/api/knowledge/core/editions/${reader.id}/read?asset_id=${asset}&page=${page}`).then(value=>{if(id===pageGeneration.current)setBlocks(value);}).catch(e=>{if(id===pageGeneration.current)setError(e.message);});
  },[reader?.id,asset,page]);
  async function find(cursor:string|null=null,prior?:Record<string,unknown>){
    const payload=prior||{query,mode,categories:cats,edition_id:scope?.id||null,limit};
    const value=await request<{items:Hit[];next_cursor:string|null}>('/api/knowledge/core/search','POST',{...payload,cursor});
    setHits(value.items);setNext(value.next_cursor);setSubmitted(payload);setSearched(true);
  }
  async function excerpt(blockId:string,start:number,end:number){
    const value=await request<{id:string;quote:string;locator:Evidence['locator']}>('/api/knowledge/anchors','POST',{block_id:blockId,start,end});
    onEvidence({evidence_id:'',anchor_id:value.id,quote:value.quote,locator:value.locator});setMessage('原文摘录已保存');
  }
  async function linkSource(){
    if(!reader||!currentFile||!project)return;
    type Link={edition_id:string;version:number;mode:string;ranges:{asset_id:string;page_from:number;page_to:number}[];roles:string[];witness_label:string;scope_note:string};
    const links=await request<Link[]>(`/api/knowledge/projects/${project}/sources`);
    if(!wholeBook&&links.some(v=>v.edition_id===reader.id&&v.mode==='edition')){setMessage('项目已包含整部书，无需重复加入此卷');return;}
    const entire=wholeBook;
    const ranges=entire?[]:[{asset_id:asset,page_from:1,page_to:currentFile.pages}];
    const previous=links.find(v=>v.edition_id===reader.id&&v.mode===(entire?'edition':'ranges')&&
      (entire||(v.ranges.length===1&&v.ranges[0].asset_id===asset&&v.ranges[0].page_from===1&&v.ranges[0].page_to===currentFile.pages)));
    await request(`/api/knowledge/projects/${project}/sources/${reader.id}`,'PUT',{
      roles:[...new Set([...(previous?.roles||[]),'reference'])],mode:entire?'edition':'ranges',ranges:entire?[]:ranges,
      version:previous?.version,witness_label:previous?.witness_label||'',scope_note:previous?.scope_note||(entire?'核心古籍整书':'核心古籍分卷：'+currentFile.title)});
    await onChanged();setMessage('已关联项目，可在研究助手中选择“当前项目”进行查证');
  }
  const currentFile=reader?.files.find(f=>f.asset_id===asset);
  return <section className="kb-core" aria-label="核心古籍库">
    {!location&&<><div className="kb-toolbar"><h2>核心古籍库</h2><button className={view==='books'?'button-primary':'button-secondary'} onClick={()=>{setView('books');setReader(null);onBack?.();}}>书目浏览</button><button className={view==='search'?'button-primary':'button-secondary'} onClick={()=>{setView('search');setReader(null);}}>原文检索</button></div>
    <p className="scope-note">核心古籍与个人资料共用文献库。按书归并分卷，保留来源录文；可将选定书或分卷加入项目，交给研究助手检索。</p>
    {error&&<p className="persistent-error" role="alert">{error}</p>}{message&&<p className="scope-note" role="status">{message}</p>}
    {corpora.map(c=><div className="kb-core-stat" key={c.id}><strong>{c.name}</strong><span>{labels[c.state]||c.state}</span><span>{(c.statistics.books??c.statistics.processed??0).toLocaleString()} 部{c.statistics.source_books&&c.state==='importing'?` / ${c.statistics.source_books.toLocaleString()}`:''}</span><span>{(c.statistics.files??c.statistics.source_files??0).toLocaleString()} 个源文件</span>{!!c.statistics.failed_books&&<span>{c.statistics.failed_books} 部待核验</span>}<details><summary>来源与导入详情</summary><p>{c.source_path}</p><p>源版本：{c.source_revision||'未记录 Git 版本'}</p><p>现代作品、源备份和仓库说明文件不进入核心古籍。</p>{c.errors.map((e,i)=><p key={i}>{e.source_key}：{e.error}</p>)}</details></div>)}
    <details className="kb-quality"><summary>导入或增量更新本地古籍库</summary><p>读取十个藏部及四庫全書下的 Markdown。按内容校验已导入文件；原文有人工修订时暂停该书更新。导入可在“处理任务”中停止后重试。</p><div className="kb-toolbar"><input aria-label="本地古籍源目录" placeholder="请输入本地古籍库目录的完整路径" value={source} onChange={e=>setSource(e.target.value)}/><button className="button-secondary" disabled={busy||corpora.some(c=>c.state==='importing')} onClick={()=>void act(async()=>{await request('/api/knowledge/core/import','POST',{source_path:source});setMessage('导入任务已创建，请在处理任务中查看进度');await refresh();})}>{corpora.length?'增量核验 / 更新':'导入核心古籍'}</button></div></details>
    </>}
    {location&&error&&<p className="persistent-error" role="alert">{error}</p>}{location&&message&&<p role="status">{message}</p>}
    {location&&!reader&&<p role="status">正在打开古籍…</p>}
    {!reader&&!location&&<><div className="kb-core-categories" role="group" aria-label="典籍分类"><strong>典籍分类</strong>{categories.map(c=><label key={c}><input type="checkbox" checked={cats.includes(c)} onChange={e=>{setCats(v=>e.target.checked?[...v,c]:v.filter(x=>x!==c));setSearched(false);setHits([]);setSubmitted(null);setHistory([]);setNext(null);}}/>{c}</label>)}<button className="button-quiet" onClick={()=>setCats(categories)}>全选</button></div>
    {view==='books'?<><form className="kb-toolbar" onSubmit={e=>{e.preventDefault();void act(()=>loadBooks());}}><input aria-label="核心古籍书目检索" placeholder="搜索书名或元数据中的作者，支持繁简字形" value={bookQuery} onChange={e=>setBookQuery(e.target.value)}/><button className="button-primary" disabled={busy||!cats.length}><Search size={16}/>搜索书目</button></form><div className="kb-list">{books.map(b=><article className="kb-card" key={b.edition_id}><div><h3>{b.title}</h3><p>{b.category} · {b.authors_json.join('、')||'源数据未注明作者'} {b.date_label} · {b.file_count} 个源文件</p><small>{b.source_key}</small></div><div className="action-row"><button className="button-secondary" disabled={busy} onClick={()=>void act(()=>openReader({id:b.edition_id}))}><BookOpen size={16}/>阅读</button><button className="button-quiet" onClick={()=>{setScope({id:b.edition_id,title:b.title});setView('search');setSearched(false);}}>检索本书</button><button className="button-quiet" onClick={()=>onUse({id:b.edition_id,title:b.title,label:'殆知阁数字录文'})}>用于问答</button></div></article>)}</div>{!books.length&&<p className="empty-block">{cats.length?'未找到书目；可调整关键词或等待导入完成。':'请至少选择一个藏部。'}</p>}{bookCursor&&<button className="button-secondary" disabled={busy} onClick={()=>void act(()=>loadBooks(bookCursor,true))}>加载更多书目</button>}</>:<>
      <form className="kb-core-search" onSubmit={e=>{e.preventDefault();setHistory([]);void act(()=>find());}}><div className="kb-toolbar"><input aria-label="古籍原文检索内容" maxLength={160} placeholder="输入原文，例如：學而時習之、孔子、𠀀" value={query} onChange={e=>setQuery(e.target.value)}/><button className="button-primary" disabled={busy||!query.trim()||!cats.length}><Search size={16}/>{busy?'检索中…':'检索原文'}</button></div><div className="kb-toolbar"><label>检索模式 <select aria-label="古籍检索模式" value={mode} onChange={e=>setMode(e.target.value)}><option value="normal">普通：繁简归一，多词同时出现</option><option value="exact">精确：繁简归一，连续字句</option><option value="literal">全文逐字：原字形、标点一致</option></select></label><label>每页 <select aria-label="古籍检索每页数量" value={limit} onChange={e=>setLimit(Number(e.target.value))}>{[10,20,50,100].map(n=><option key={n} value={n}>{n} 处</option>)}</select></label>{scope?<span>限定《{scope.title}》<button type="button" className="button-quiet" onClick={()=>{setScope(null);setSearched(false);}}>取消限定</button></span>:<span>范围：选中藏部的全部核心古籍</span>}</div></form>
      <p className="scope-note">直接查询录文，不调用语言模型。普通模式按空格分词，在同一文本段内同时出现；精确与逐字模式逐处返回，包含重叠出现位置。繁简归一仅用于检索，原文不变；异体字及标点仍须分别核验。</p>
      {searched&&<p role="status">本页 {hits.length} 处命中{next?'，后续仍有结果':'，已到结果末尾'}。点击“阅读原文”核验上下文。</p>}
      {hits.map(h=><article className="kb-card kb-result" key={`${h.passage_id}-${h.start_offset}`}><h3>{h.title} · {h.category}</h3><p>{h.section_path} · 第 {h.page_index} 文本段</p><pre><Highlight text={h.snippet} start={h.start_offset-h.snippet_start} end={h.end_offset-h.snippet_start}/></pre><small>{h.source_path} · 提取录文第 {h.source_start+h.start_offset+1} 字起</small><div className="action-row"><button className="button-secondary" disabled={busy} onClick={()=>void act(()=>openReader({id:h.edition_id,asset:h.asset_id,page:h.page_index,start:h.start_offset,end:h.end_offset}))}>阅读原文</button><button className="button-quiet" disabled={busy} onClick={()=>void act(()=>excerpt(h.block_id,h.start_offset,h.end_offset))}>摘录命中字句</button><button className="button-quiet" onClick={()=>onUse({id:h.edition_id,title:h.title,label:'殆知阁数字录文'})}>用于问答</button></div></article>)}
      {!!history.length&&submitted&&<button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>{await find(history.length>1?history[history.length-2]:null,submitted);setHistory(v=>v.slice(0,-1));})}>上一页原文命中</button>}{next&&submitted&&<button className="button-secondary" disabled={busy} onClick={()=>void act(async()=>{await find(next,submitted);setHistory(v=>[...v,next]);})}>下一页原文命中</button>}{!!history.length&&submitted&&<button className="button-quiet" disabled={busy} onClick={()=>void act(async()=>{await find(null,submitted);setHistory([]);})}>返回第一页</button>}{!searched&&<p className="empty-block">输入关键词，开始检索原文。可直接检索单字、双字词或连续句子。</p>}
    </>}</>}
    {reader&&<div className="kb-editor kb-core-reader"><div className="kb-toolbar"><button className="button-quiet" onClick={()=>{++readerGeneration.current;setReader(null);onBack?.();}}>← 返回{location?'目录':view==='books'?'书目':'检索结果'}</button><h2>{reader.title}</h2><button className="button-secondary" onClick={()=>onUse({id:reader.id,title:reader.title,label:reader.label,asset})}>检索此文件</button></div>
      <div className="kb-toolbar"><label>分卷 <select aria-label="核心古籍分卷" value={asset} onChange={e=>{setAsset(e.target.value);setPage(1);setFocus(null);}}>{reader.files.map(f=><option key={f.source_path} value={f.asset_id}>{f.title}</option>)}</select></label><label>文本段 <input aria-label="核心古籍文本段" type="number" min={1} max={currentFile?.pages||1} value={page} onChange={e=>{setPage(Math.max(1,Math.min(currentFile?.pages||1,Number(e.target.value)||1)));setFocus(null);}}/> / {currentFile?.pages||1}</label><button className="button-secondary" disabled={page<=1} onClick={()=>{setPage(v=>v-1);setFocus(null);}}>上一段</button><button className="button-secondary" disabled={page>=(currentFile?.pages||1)} onClick={()=>{setPage(v=>v+1);setFocus(null);}}>下一段</button></div>
      {!location&&<p className="scope-note">文本段由软件划分，并非原书页码。段末包含下一段的少量上下文；检索出现次数和项目固定输入均已去除这部分重复。显示提取后的录文，保留正文和注文；格式标签可在导出的原始 Markdown 中核验。</p>}
      {blocks.map(b=><article className="kb-block" key={b.id}><pre>{focus?.start!==undefined?<Highlight text={b.body} start={focus.start} end={focus.end||focus.start}/>:b.body}</pre><button className="button-secondary" disabled={busy} onClick={()=>{const range=sourceRange(b.body,window.getSelection()?.toString()||'');void act(()=>excerpt(b.id,range.start,range.end));}}>摘录选中文字 / 整段</button></article>)}
      <details><summary>来源与原件</summary><p>{currentFile?.source_path}</p><p>SHA-256：{currentFile?.sha256}</p><p>源版本：{reader.core.source_revision}</p>{typeof currentFile?.metadata.daizhige_url==='string'&&<a href={currentFile.metadata.daizhige_url} target="_blank" rel="noreferrer">打开殆知阁对应页面</a>}<pre>{JSON.stringify(currentFile?.metadata,null,2)}</pre><button className="button-secondary" onClick={()=>void act(async()=>{const result=await window.desktop.request({path:`/api/knowledge/editions/${reader.id}/files/${asset}`});if(result.status!==200||!(result.body instanceof ArrayBuffer))throw new Error('原件读取失败');await window.desktop.saveFile({filename:currentFile?.source_path.split('/').pop()||'古籍原文.md',data:result.body});})}>导出此卷原始 Markdown</button></details>
      <details className="kb-core-project"><summary>加入项目资料</summary><div className="kb-toolbar"><select aria-label="核心古籍关联项目" value={project} onChange={e=>setProject(e.target.value)}><option value="">选择项目</option>{projects.filter(p=>!p.archived).map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><label><input type="checkbox" checked={wholeBook} onChange={e=>setWholeBook(e.target.checked)}/>关联整部书（默认仅当前分卷）</label><button className="button-primary" disabled={busy||!project||!currentFile} onClick={()=>void act(linkSource)}>加入项目研究范围</button></div></details>
    </div>}
  </section>;
}
