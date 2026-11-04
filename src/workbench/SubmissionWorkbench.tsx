import {confirmDialog,confirmDiscard} from './dialogQueue';
import JournalPicker, { type SubmissionJournal } from './SubmissionJournalPicker';
import JournalSourceDetails from './JournalSourceDetails';
import PageHeading, { PageHeadingActions } from './PageHeading';
import { platform } from './platform';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, Globe2, Search, RefreshCw, ArrowUpRight, ExternalLink, Pencil, Plus, Star, Upload } from 'lucide-react';
import { type Project } from './api';
import { call } from './transport';
import { Modal } from './Pages';
import { Drawer, Wizard } from './Overlays';
import EnglishJournalLibrary from './EnglishJournalLibrary';
import './submissions.css';

type ApiMethod='GET'|'POST'|'PUT'|'PATCH'|'DELETE';
/** 页内已展示错误：请求标记 silent，不再派发全局错误浮层（P0-4）。 */
function request<T>(path:string,method:ApiMethod='GET',body?:unknown){return call<T>({path,method,body,silent:true});}

const paperStates = { writing: '撰写中', ready: '待投稿', review: '审稿中', revision: '返修中', accepted: '已录用', published: '已发表', rejected: '已拒稿' };
type State = keyof typeof paperStates;
type Journal = SubmissionJournal;
type Paper = { id: string; title: string; authors: string; discipline: string; priority: string; progress: number; status: State; target_journal_id: string | null; target_journal_name?:string|null; project_id: string | null; source_kind: 'artifact' | 'unit' | null; source_id: string | null; source_title: string; notes: string; archived: number | boolean; revision: number; updated_at: string; current_journal_name?: string; submitted_date?: string; last_date?: string; round?: number; journal_ids?: string[] };
type Attempt = { id: string; journal_id: string; journal_name: string; submitted_date: string; status: State | 'withdrawn'; round: number; last_date: string };
type PaperDetail = Paper & { attempts: Attempt[]; events: { id: string; attempt_id: string | null; kind: string; event_date: string; note: string; detail: { source_title?: string } }[] };
type Dataset = { id: string; label: string; row_count: number; created_at: string };
type Overview = { journals: Journal[]; papers: Paper[]; datasets: Dataset[]; public_catalog?:boolean };
type Preview = { fingerprint: string; label: string; rows: number; new: number; existing: number; already_imported: boolean; errors: string[]; error_count: number; policy: string; sample: Journal[] };
export type SubmissionDraft = { title: string; project_id: string; source_kind: 'artifact' | 'unit'; source_id: string };
const endpoint = '/api/submissions';
/** 审稿超过该天数记为超期。 */
const OVERDUE_DAYS = 120;
const priorityNames: Record<string, string> = { high: '高优先', medium: '普通', low: '低优先' };
const actionNames: Record<string, string> = { created: '创建论文', edited: '编辑论文', submit: '正式投稿', request_revision: '收到返修', revise: '提交修订稿', accept: '收到录用', reject: '收到拒稿', publish: '确认发表', withdraw: '撤回投稿' };
const allowed: Record<State, string[]> = { writing: [], ready: ['submit'], review: ['request_revision', 'accept', 'reject', 'withdraw'], revision: ['revise', 'accept', 'reject', 'withdraw'], accepted: ['publish'], published: [], rejected: ['submit'] };
const emptyJournal = (): Journal => ({ id: '', name: '', discipline: '', level: '', website: '', submission_url: '', notes: '', favorite: false, revision: 1 });
const emptyPaper = (): Paper => ({ id: '', title: '', authors: '', discipline: '', priority: 'medium', progress: 0, status: 'writing', target_journal_id: null, project_id: null, source_kind: null, source_id: null, source_title: '', notes: '', archived: false, revision: 1, updated_at: '' });
const message = (e: unknown) => e instanceof Error ? e.message : '操作失败';
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const updatedLabel = (value: string) => { if (!value) return '—'; const time = new Date(value); return Number.isNaN(time.getTime()) ? value : time.toLocaleDateString('zh-CN'); };
const ordered = (items: string[]) => [...new Set(items.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));

/** 后台期刊目录 CSV 与后端导入使用同一套表头；后台代理不开放批量导入接口，这里逐条写入。 */
const CSV_HEADERS = ['期刊名称', '学科门类', '期刊等级', '官网', '投稿地址'] as const;
type CatalogRow = { name: string; discipline: string; level: string; website: string; submission_url: string };
type CatalogImport = { label: string; errors: string[]; failures: string[]; fresh: CatalogRow[]; updates: { journal: Journal; row: CatalogRow }[]; unchanged: number; progress: number; total: number };
const catalogNameKey = (name: string) => name.trim().toLocaleLowerCase();
function parseCatalogCsv(content: string): { rows: CatalogRow[]; errors: string[] } {
  const records: string[][] = [];
  let record: string[] = [], field = '', quoted = false;
  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    if (quoted) { if (char === '"') { if (content[index + 1] === '"') { field += '"'; index += 1; } else quoted = false; } else field += char; }
    else if (char === '"') quoted = true;
    else if (char === ',') { record.push(field); field = ''; }
    else if (char === '\n') { record.push(field); field = ''; records.push(record); record = []; }
    else if (char !== '\r') field += char;
  }
  if (field.length || record.length) { record.push(field); records.push(record); }
  const header = records.shift();
  if (!header || header.length !== 5 || CSV_HEADERS.some(name => !header.includes(name))) return { rows: [], errors: [`表头须为：${CSV_HEADERS.join('、')}`] };
  const columns: Record<string, number> = {};
  CSV_HEADERS.forEach(name => { columns[name] = header.indexOf(name); });
  const rows: CatalogRow[] = [], errors: string[] = [], seen = new Map<string, CatalogRow>();
  records.forEach((cells, index) => {
    if (cells.every(cell => !cell.trim())) return;
    if (cells.length !== 5) { errors.push(`第 ${index + 2} 行：列数与表头不一致`); return; }
    const row: CatalogRow = { name: cells[columns['期刊名称']].trim(), discipline: cells[columns['学科门类']].trim(), level: cells[columns['期刊等级']].trim(), website: cells[columns['官网']].trim(), submission_url: cells[columns['投稿地址']].trim() };
    if (!row.name) { errors.push(`第 ${index + 2} 行：期刊名称不能为空`); return; }
    if (row.name.length > 300 || row.discipline.length > 120 || row.level.length > 80 || row.website.length > 2000 || row.submission_url.length > 2000) { errors.push(`第 ${index + 2} 行：字段超长`); return; }
    const key = catalogNameKey(row.name), previous = seen.get(key);
    if (previous) { if (JSON.stringify(previous) !== JSON.stringify(row)) errors.push(`第 ${index + 2} 行：同名期刊信息冲突，请合并后再导入`); return; }
    seen.set(key, row); rows.push(row);
  });
  if (!rows.length && !errors.length) errors.push('目录中没有期刊');
  return { rows, errors };
}
async function readCsvFile(picked: File): Promise<string> {
  const bytes = await picked.arrayBuffer();
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { return new TextDecoder('gb18030', { fatal: true }).decode(bytes); }
}

export default function SubmissionWorkbench({ projects, draft, onDraftUsed, onRegisterLeave, catalogOnly=false, onCreateFromArtifact }: { catalogOnly?:boolean; onCreateFromArtifact?:()=>void; projects: Project[]; draft: SubmissionDraft | null; onDraftUsed(): void; onRegisterLeave(callback: (() => Promise<boolean>) | null): void }) {
  const endpoint=catalogOnly?'/api/admin/journals':'/api/submissions';
  const [englishCount,setEnglishCount]=useState<number|null>(null);
  const [data, setData] = useState<Overview | null>(null), [tab, setTab] = useState<'papers' | 'journals' | 'english'>(catalogOnly?'journals':'papers');
  const [query, setQuery] = useState(''), [discipline, setDiscipline] = useState(''), [level, setLevel] = useState('');
  const [favorites, setFavorites] = useState(false), [links, setLinks] = useState(false), [group, setGroup] = useState('discipline'), [limit, setLimit] = useState(80);
  const [project, setProject] = useState(''), [status, setStatus] = useState(''), [archived, setArchived] = useState(false), [view, setView] = useState<'board' | 'list'>('board');
  const [journal, setJournal] = useState<Journal | null>(null), [paper, setPaper] = useState<Paper | null>(null), [detail, setDetail] = useState<PaperDetail | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [detailNotice, setDetailNotice] = useState(''), [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false);
  const [importing, setImporting] = useState<{ content: string; label: string; report: Preview } | null>(null);
  const [catalogImport, setCatalogImport] = useState<CatalogImport | null>(null);
  const file = useRef<HTMLInputElement>(null), dirtyRef = useRef(false), mounted = useRef(true);
  dirtyRef.current = dirty;
  useEffect(() => { mounted.current = true; onRegisterLeave(async () => !dirtyRef.current || await confirmDiscard()); const unload = (e: BeforeUnloadEvent) => { if (dirtyRef.current) { e.preventDefault(); e.returnValue = ''; } }; window.addEventListener('beforeunload', unload); return () => { mounted.current = false; onRegisterLeave(null); window.removeEventListener('beforeunload', unload); }; }, [onRegisterLeave]);
  // 成功提示 4 秒后自动收起。
  useEffect(()=>{if(!notice)return;const timer=window.setTimeout(()=>setNotice(''),4000);return()=>window.clearTimeout(timer);},[notice]);
  const refresh = useCallback(async () => { const result = await request<Overview>(`${endpoint}/overview`); if (mounted.current) setData(result); }, [endpoint]);
  useEffect(() => { void refresh().catch(e => setError(message(e))); }, [refresh]);
  useEffect(()=>{if(!catalogOnly)return;let live=true;void request<{total:number}>(`${endpoint}/english-journals?page_size=1`).then(value=>{if(live)setEnglishCount(value.total);}).catch(()=>{});return()=>{live=false;};},[catalogOnly,endpoint]);
  useEffect(() => { if (draft) { setTab('papers'); setPaper({ ...emptyPaper(), ...draft }); setDirty(true); onDraftUsed(); } }, [draft, onDraftUsed]);
  useEffect(() => { setLimit(80); }, [query, discipline, level, favorites, links, group]);
  const closeForm = async () => { if (busy || dirty && !await confirmDiscard()) return; setPaper(null); setJournal(null); setImporting(null); setCatalogImport(null); setDirty(false); };
  const run = async (fn: () => Promise<void>) => { setBusy(true); setError(''); try { await fn(); } catch (e) { setError(message(e)); } finally { if (mounted.current) setBusy(false); } };
  const openPaper = (id: string) => { setDetailNotice(''); void run(async () => setDetail(await request<PaperDetail>(`${endpoint}/papers/${id}`))); };
  const journalName = (id: string | null) => data?.journals.find(j => j.id === id)?.name || '';
  const linked = (id: string) => data?.papers.filter(p => p.target_journal_id === id || p.journal_ids?.includes(id)) || [];
  const saveJournal = () => journal && void run(async () => { if(data?.public_catalog)await request(`${endpoint}/journals/${journal.id}/preferences`,'PATCH',{favorite:!!journal.favorite,notes:journal.notes,revision:journal.revision});else await request(`${endpoint}/journals${journal.id ? `/${journal.id}` : ''}`, journal.id ? 'PUT' : 'POST', { ...journal, favorite: !!journal.favorite }); setJournal(null); setDirty(false); await refresh(); setNotice('期刊信息已保存'); });
  const savePaper = () => paper && void run(async () => { const saved = await request<PaperDetail>(`${endpoint}/papers${paper.id ? `/${paper.id}` : ''}`, paper.id ? 'PUT' : 'POST', { ...paper, status: paper.status === 'ready' ? 'ready' : 'writing', archived: !!paper.archived }); setPaper(null); setDetail(saved); setDirty(false); await refresh(); setNotice('论文已保存'); });
  const pickCsv = async (picked: File) => { await run(async () => {
    if (picked.size > 5_000_000) throw Error('CSV 文件不能超过 5 MB');
    const label = picked.name.replace(/\.csv$/i, '').slice(0, 120) || '导入期刊目录';
    const content = await readCsvFile(picked);
    if (catalogOnly) {
      const parsed = parseCatalogCsv(content);
      const existingByName = new Map((data?.journals || []).map(journal => [catalogNameKey(journal.name), journal]));
      const fresh: CatalogRow[] = [], updates: { journal: Journal; row: CatalogRow }[] = [];
      let unchanged = 0;
      for (const row of parsed.rows) {
        const existing = existingByName.get(catalogNameKey(row.name));
        if (!existing) { fresh.push(row); continue; }
        if (existing.revision === 1 && (!existing.discipline || !existing.level || !existing.website || !existing.submission_url)) updates.push({ journal: existing, row });
        else unchanged += 1;
      }
      setCatalogImport({ label, errors: parsed.errors, failures: [], fresh, updates, unchanged, progress: 0, total: fresh.length + updates.length });
      return;
    }
    const report = await request<Preview>(`${endpoint}/import/preview`, 'POST', { content, label });
    setImporting({ content, label, report });
  }); };
  const confirmCatalogImport = async () => { if (!catalogImport) return; await run(async () => {
    const { fresh, updates, label } = catalogImport;
    const failures: string[] = [];
    let created = 0, updated = 0, progress = 0;
    const step = (name: string, cause: unknown) => failures.push(`${name}：${message(cause)}`);
    for (const row of fresh) {
      try { await request(`${endpoint}/journals`, 'POST', { name: row.name, discipline: row.discipline, level: row.level, website: row.website, submission_url: row.submission_url, notes: '', favorite: false }); created += 1; }
      catch (e) { step(row.name, e); }
      progress += 1;
      if (mounted.current) setCatalogImport(previous => previous ? { ...previous, progress, failures: failures.slice() } : previous);
    }
    for (const { journal, row } of updates) {
      try { await request(`${endpoint}/journals/${journal.id}`, 'PUT', { ...journal, name: row.name, discipline: journal.discipline || row.discipline, level: journal.level || row.level, website: journal.website || row.website, submission_url: journal.submission_url || row.submission_url, notes: journal.notes || '', favorite: !!journal.favorite }); updated += 1; }
      catch (e) { step(row.name, e); }
      progress += 1;
      if (mounted.current) setCatalogImport(previous => previous ? { ...previous, progress, failures: failures.slice() } : previous);
    }
    await refresh();
    setCatalogImport(null);
    setNotice(`${label}导入完成：新增 ${created} 种，补全 ${updated} 种${failures.length ? `，${failures.length} 种失败` : ''}`);
  }); };
  const journals = (data?.journals || []).filter(j => j.catalog_kind!=='english' && (!query || `${j.name} ${j.notes}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && (!discipline || j.discipline === discipline) && (!level || j.level === level) && (!favorites || j.favorite) && (!links || j.submission_url));
  const papers = (data?.papers || []).filter(p => !!p.archived === archived && (!query || `${p.title} ${p.authors} ${p.notes}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && (!project || p.project_id === project) && (!status || p.status === status)).sort((a, b) => ['high', 'medium', 'low'].indexOf(a.priority) - ['high', 'medium', 'low'].indexOf(b.priority) || b.updated_at.localeCompare(a.updated_at));
  const chineseJournals=(data?.journals||[]).filter(j=>j.catalog_kind!=='english');
  const stats = (data?.papers || []).filter(p => !p.archived);
  const loadingSubject = tab === 'papers' ? '投稿数据' : tab === 'journals' ? '期刊目录' : '英文期刊';
  const toolbar=data&&tab!=='english'?<div className="submission-toolbar"><label className="submission-search"><Search size={18}/><input aria-label={tab === 'papers' ? '搜索论文' : '搜索期刊'} placeholder={tab === 'papers' ? '搜索题目、作者、备注' : '搜索期刊名称、备注'} value={query} onChange={e => setQuery(e.target.value)} /></label>
        {tab === 'journals' ? <><select aria-label="期刊学科" value={discipline} onChange={e => setDiscipline(e.target.value)}><option value="">全部学科</option>{ordered(chineseJournals.map(j => j.discipline)).map(d => <option key={d}>{d}</option>)}</select><select aria-label="期刊等级" value={level} onChange={e => setLevel(e.target.value)}><option value="">全部等级</option>{ordered(chineseJournals.map(j => j.level)).map(d => <option key={d}>{d}</option>)}</select>{!data.public_catalog&&<button className="button-secondary" onClick={() => { setJournal(emptyJournal()); setDirty(false); }}><Plus size={16}/>新增期刊</button>}{!data.public_catalog&&<button className="button-secondary" disabled={busy} onClick={() => file.current?.click()}><Upload size={16} />导入目录 CSV</button>}<input ref={file} hidden type="file" accept=".csv,text/csv" aria-label="期刊 CSV 文件" onChange={e => { const picked = e.target.files?.[0]; e.target.value = ''; if (picked) void pickCsv(picked); }} /></> : <><select aria-label="论文所属项目" value={project} onChange={e => setProject(e.target.value)}><option value="">全部项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}{p.archived ? '（已归档）' : ''}</option>)}</select><select aria-label="投稿阶段筛选" value={status} onChange={e => setStatus(e.target.value)}><option value="">全部阶段</option>{Object.entries(paperStates).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select><label className="submission-check"><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} />已归档</label><div className="submission-view-toggle" role="group" aria-label="论文视图"><button type="button" aria-pressed={view === 'board'} onClick={() => setView('board')}>看板</button><button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>列表</button></div><button className="button-primary" onClick={() => { setPaper(emptyPaper()); setDirty(false); }}><Plus size={16} />新建论文</button></>}
      </div>:null;
  return <div className={`page-container submission-workbench ${catalogOnly?'catalog-management':''}`}>
    {!catalogOnly&&<PageHeading title="论文投稿管理" description="查找期刊，记录论文从投稿到发表的进展" actions={<button className="button-secondary" disabled={busy} onClick={()=>void run(refresh)}><RefreshCw size={16}/>刷新</button>}/>}
    {catalogOnly&&<PageHeadingActions><div className="catalog-summary"><article><div><span>中文 C 刊</span><strong>{data?chineseJournals.length.toLocaleString():'—'}<small>种期刊</small></strong></div></article><article><div><span>英文期刊</span><strong>{englishCount===null?'—':englishCount.toLocaleString()}<small>种期刊</small></strong></div></article></div></PageHeadingActions>}
    <div className={catalogOnly?'catalog-panel':undefined}>
    {error && !paper && !journal && !detail && !importing && !catalogImport && <div role="alert" className="scope-warning">{error}<button className="button-quiet" onClick={() => setError('')}>关闭提示</button></div>}{notice && <p role="status" className="submission-notice">{notice}</p>}
    {!catalogOnly&&tab==='papers'&&<div className="submission-stats">{[['全部论文', stats.length], ['审稿与返修', stats.filter(p => ['review', 'revision'].includes(p.status)).length], ['录用与发表', stats.filter(p => ['accepted', 'published'].includes(p.status)).length], ['中文 C 刊', chineseJournals.length], ['收藏期刊', chineseJournals.filter(j=>j.favorite).length]].map(([name, count]) => <div key={name}><strong>{count}</strong><span>{name}</span></div>)}</div>}
    <div className="catalog-panel-heading"><div className="submission-tabs" role="tablist" aria-label="投稿管理视图">{([['papers','论文管理'],['journals','中文 C 刊库'],['english','英文期刊库']] as const).filter(([id])=>!catalogOnly||id!=='papers').map(([id,text])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>{setTab(id);setQuery('');setNotice('');}}>{catalogOnly&&(id==='journals'?<BookOpen size={16}/>:<Globe2 size={16}/>)}{text}</button>)}</div>{catalogOnly&&<button className="button-secondary catalog-refresh" aria-label="刷新期刊目录" disabled={busy} onClick={()=>void run(refresh)}><RefreshCw size={16}/>刷新目录</button>}</div>
    {!data?<div className="catalog-loading" role="status">{error?<><BookOpen size={24}/><strong>{loadingSubject}暂未加载</strong><button className="button-secondary" onClick={()=>void run(refresh)}>重新加载</button></>:<><span className="catalog-loading-line"/><span className="catalog-loading-line"/><span className="catalog-loading-line"/><p>正在读取{loadingSubject}…</p></>}</div>:<>
      {tab==='papers'&&toolbar}
      {tab === 'journals' ? <>
        <div className="journal-controls">{toolbar}<div className="submission-toolbar secondary">{!catalogOnly&&<label className="submission-check"><input type="checkbox" checked={favorites} onChange={e => setFavorites(e.target.checked)} />仅看收藏</label>}<label className="submission-check"><input type="checkbox" checked={links} onChange={e => setLinks(e.target.checked)} />有投稿地址</label>{!catalogOnly&&<select aria-label="期刊分组" value={group} onChange={e => setGroup(e.target.value)}><option value="discipline">按学科分组</option><option value="level">按等级分组</option></select>}<span className="scope-note">筛选后 {journals.length} 种</span></div>
        <JournalSourceDetails><strong>导入记录（{data.datasets.length}）</strong>{data.datasets.map(d=><p key={d.id}>{d.label} · {d.row_count} 种 · {new Date(d.created_at).toLocaleDateString('zh-CN')}</p>)}<p>等级与学科按提供的目录记录；投稿前请核对期刊当前要求</p></JournalSourceDetails></div>
        {catalogOnly?<ChineseJournalTable journals={journals.slice(0,limit)} onEdit={value=>{setJournal(value);setDirty(false);}}/>:ordered(journals.slice(0, limit).map(j => group === 'level' ? j.level || '未分类' : j.discipline || '未分类')).map(section => <section key={section} className="journal-section"><h2>{section}</h2><div className="journal-wall">{journals.slice(0, limit).filter(j => (group === 'level' ? j.level || '未分类' : j.discipline || '未分类') === section).map(j => <article className="journal-note" key={j.id} data-journal-id={j.id}>
          <div className="journal-note-top"><span>{j.level || '等级未填写'}</span>{!catalogOnly&&<button className="icon-btn" aria-label={`${j.favorite ? '取消收藏' : '收藏'}${j.name}`} aria-pressed={!!j.favorite} disabled={busy} onClick={() => void run(async () => { await request(`${endpoint}/journals/${j.id}/preferences`, 'PATCH', { favorite: !j.favorite, revision:j.revision }); await refresh(); })}><Star size={18} fill={j.favorite ? 'currentColor' : 'none'} /></button>}</div>
          <h3>{j.name}</h3><p>{j.discipline || '学科未填写'}</p>{j.personal_legacy&&<small>历史个人期刊</small>}{j.public_notes&&<p className="journal-excerpt">{j.public_notes}</p>}{j.notes && <p className="journal-excerpt">{data.public_catalog?'我的备注：':''}{j.notes}</p>}<div className="journal-links"><Link url={j.website}>官网</Link><Link url={j.submission_url}>投稿地址</Link></div><div className="journal-note-bottom"><span>{linked(j.id).length} 篇关联论文</span><button className="button-quiet" aria-label={`${data.public_catalog?'我的期刊备注':'编辑期刊'}：${j.name}`} onClick={() => { setJournal(j); setDirty(false); }}><Pencil size={16} />{data.public_catalog?'我的备注':'编辑期刊'}</button></div>
        </article>)}</div></section>)}{!journals.length && <div className="empty-block submission-empty"><BookOpen size={26} className="submission-empty-icon"/><strong>{chineseJournals.length ? '没有符合条件的期刊' : '还没有可维护的期刊目录'}</strong><span>{chineseJournals.length ? '可调整学科、等级或关键词筛选' : '可新增单本期刊，或导入目录 CSV 批量建立'}</span><div className="empty-actions">{(query || discipline || level || favorites || links) && <button type="button" className="button-secondary" onClick={() => { setQuery(''); setDiscipline(''); setLevel(''); setFavorites(false); setLinks(false); }}>重置筛选</button>}{!data.public_catalog && data.datasets.length === 0 && <button type="button" className="button-secondary" disabled={busy} onClick={() => file.current?.click()}><Upload size={16}/>导入目录 CSV</button>}</div></div>}{journals.length > limit && <button className="button-secondary load-more" onClick={() => setLimit(n => n + 80)}>加载更多期刊</button>}
      </> : tab === 'english' ? <EnglishJournalLibrary endpoint={catalogOnly?'/api/admin/journals/english-journals':undefined}/> : <>{papers.length === 0 ? (data.papers.length === 0
        ? <div className="empty-block submission-empty"><BookOpen size={26} className="submission-empty-icon"/><strong>还没有投稿论文</strong><span>新建论文后，可用看板跟踪从撰写到发表的进展</span><div className="empty-actions"><button type="button" className="button-primary" onClick={() => { setPaper(emptyPaper()); setDirty(false); }}><Plus size={16}/>新建论文</button>{onCreateFromArtifact && <button type="button" className="button-secondary" onClick={onCreateFromArtifact}>从研究成果创建</button>}</div></div>
        : <div className="empty-block"><strong>没有符合条件的论文</strong><span>当前筛选条件下没有论文，可调整项目、阶段或归档条件</span><div className="empty-actions"><button type="button" className="button-secondary" onClick={() => { setProject(''); setStatus(''); setArchived(false); setQuery(''); }}>重置筛选</button></div></div>
      ) : view === 'board' ? <div className="paper-board">{Object.entries(paperStates).filter(([s]) => !status || s === status).map(([s, label]) => <section className="paper-lane" key={s}><h2>{label}<span>{papers.filter(p => p.status === s).length}</span></h2>{papers.filter(p => p.status === s).map(p => { const age = p.last_date ? Math.max(0, Math.floor((Date.now() - new Date(p.last_date).getTime()) / 86400000)) : null; return <button key={p.id} className="paper-card" onClick={() => openPaper(p.id)}><span className={`paper-priority priority-${p.priority}`}>{priorityNames[p.priority]}</span><h3>{p.title}</h3><p>{p.authors || '作者未填写'}</p><p>{p.target_journal_id ? `目标：${journalName(p.target_journal_id)}` : p.current_journal_name || '尚未选择目标期刊'}</p>{p.status === 'writing' && <><progress max="100" value={p.progress} aria-label={`写作进度 ${p.progress}%`} /><small>写作进度 {p.progress}%</small></>}{['review', 'revision'].includes(p.status) && <small className={age !== null && age > OVERDUE_DAYS ? 'submission-overdue' : ''}>第 {p.round || 1} 轮 · {age === null ? '尚无进展记录' : `最近进展距今 ${age} 天`}</small>}{p.project_id && <small>{projects.find(x => x.id === p.project_id)?.name || '原关联项目已移除'}</small>}</button>; })}{!papers.some(p => p.status === s) && <p className="paper-lane-empty">暂无论文</p>}</section>)}</div> : <PaperList papers={papers} journals={data.journals} onOpen={openPaper} />}</>}
    </>}
    </div>
    {journal && <Modal title={data?.public_catalog?'我的期刊备注':journal.id ? '编辑期刊' : '新增期刊'} onClose={closeForm}><form onSubmit={e => { e.preventDefault(); saveJournal(); }}><p className="scope-note">{data?.public_catalog?'期刊资料由管理员维护；你的备注和收藏仅自己可见':'修改会保存到公共期刊卡片；原始目录信息和历史投稿记录会保留'}</p><div className="submission-form-grid">{([['name', '期刊名称', 300], ['discipline', '学科门类', 120], ['level', '期刊等级', 80], ['website', '官网', 2000], ['submission_url', '投稿地址', 2000]] as const).map(([key, label, max]) => <label className="form-label" key={key}>{label}<input readOnly={!!data?.public_catalog} aria-label={label} required={key === 'name'} maxLength={max} type={key === 'website' || key === 'submission_url' ? 'url' : 'text'} value={journal[key]} onChange={e => { setJournal({ ...journal, [key]: e.target.value }); setDirty(true); }} /></label>)}</div><label className="form-label">备注<textarea aria-label="期刊备注" rows={4} maxLength={12000} placeholder="选题偏好、审稿周期、投稿要求等" value={journal.notes} onChange={e => { setJournal({ ...journal, notes: e.target.value }); setDirty(true); }} /></label>{!catalogOnly&&<label className="submission-check"><input type="checkbox" checked={!!journal.favorite} onChange={e => { setJournal({ ...journal, favorite: e.target.checked }); setDirty(true); }} />收藏此期刊</label>}{journal.id && !catalogOnly && !data?.public_catalog && <JournalSources id={journal.id} />}{journal.id && linked(journal.id).length > 0 && <p className="scope-note">关联论文：{linked(journal.id).map(p => p.title).join('、')}</p>}<div className="modal-actions"><button type="button" className="button-secondary" disabled={busy} onClick={closeForm}>取消</button><button className="button-primary" disabled={busy || !journal.name.trim()}>{busy ? '保存中…' : '保存期刊'}</button></div>{error && <p role="alert" className="scope-warning">{error}</p>}</form></Modal>}
    {paper && paper.id && <Modal title="编辑论文" onClose={closeForm}><form onSubmit={e => { e.preventDefault(); savePaper(); }}><label className="form-label">论文题目<input aria-label="论文题目" required maxLength={500} value={paper.title} onChange={e => { setPaper({ ...paper, title: e.target.value }); setDirty(true); }} /></label><div className="submission-form-grid"><label className="form-label">作者<input aria-label="论文作者" maxLength={1000} value={paper.authors} onChange={e => { setPaper({ ...paper, authors: e.target.value }); setDirty(true); }} /></label><label className="form-label">学科<input aria-label="论文学科" maxLength={120} value={paper.discipline} onChange={e => { setPaper({ ...paper, discipline: e.target.value }); setDirty(true); }} /></label><label className="form-label">优先级<select aria-label="论文优先级" value={paper.priority} onChange={e => { setPaper({ ...paper, priority: e.target.value }); setDirty(true); }}>{Object.entries(priorityNames).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label className="form-label">关联项目<select aria-label="论文关联项目" value={paper.project_id || ''} onChange={e => { setPaper({ ...paper, project_id: e.target.value || null, source_kind: null, source_id: null, source_title: '' }); setDirty(true); }}><option value="">不关联项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>
      <JournalPicker journals={data?.journals || []} value={paper.target_journal_id} onChange={id => { setPaper({ ...paper, target_journal_id: id }); setDirty(true); }} />
      {['writing', 'ready'].includes(paper.status) && <label className="form-label">投稿阶段<select aria-label="投稿阶段" value={paper.status} onChange={e => { setPaper({ ...paper, status: e.target.value as State }); setDirty(true); }}><option value="writing">撰写中</option><option value="ready">待投稿</option></select></label>}{paper.status === 'writing' && <label className="form-label">写作进度（%）<input aria-label="写作进度" type="number" min="0" max="100" value={paper.progress} onChange={e => { setPaper({ ...paper, progress: Number(e.target.value) }); setDirty(true); }} /></label>}{paper.source_id && <p className="scope-note">已关联研究成果：{paper.source_title || '保存时记录来源题名'}</p>}<label className="form-label">论文备注<textarea aria-label="论文备注" rows={4} maxLength={12000} value={paper.notes} onChange={e => { setPaper({ ...paper, notes: e.target.value }); setDirty(true); }} /></label>{paper.id && <label className="submission-check"><input type="checkbox" checked={!!paper.archived} onChange={e => { setPaper({ ...paper, archived: e.target.checked }); setDirty(true); }} />归档论文（保留全部记录）</label>}<div className="modal-actions"><button type="button" className="button-secondary" disabled={busy} onClick={closeForm}>取消</button><button className="button-primary" disabled={busy || !paper.title.trim()}>{busy ? '保存中…' : '保存论文'}</button></div>{error && <p role="alert" className="scope-warning">{error}</p>}</form></Modal>}
    {paper && !paper.id && <Wizard title="新建论文" finishLabel="创建论文" busy={busy} error={error} onClose={closeForm} onFinish={savePaper} steps={[
      { label: '题目与作者', nextDisabled: !paper.title.trim(), content: <><label className="form-label">论文题目<input aria-label="论文题目" maxLength={500} value={paper.title} onChange={e => { setPaper({ ...paper, title: e.target.value }); setDirty(true); }} /></label><label className="form-label">作者<input aria-label="论文作者" maxLength={1000} value={paper.authors} onChange={e => { setPaper({ ...paper, authors: e.target.value }); setDirty(true); }} /></label></> },
      { label: '期刊与优先级', content: <><JournalPicker journals={data?.journals || []} value={paper.target_journal_id} onChange={id => { setPaper({ ...paper, target_journal_id: id }); setDirty(true); }} /><p className="scope-note">目标期刊可稍后在论文详情里修改</p><div className="submission-form-grid"><label className="form-label">学科<input aria-label="论文学科" maxLength={120} value={paper.discipline} onChange={e => { setPaper({ ...paper, discipline: e.target.value }); setDirty(true); }} /></label><label className="form-label">优先级<select aria-label="论文优先级" value={paper.priority} onChange={e => { setPaper({ ...paper, priority: e.target.value }); setDirty(true); }}>{Object.entries(priorityNames).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label></div></> },
      { label: '写作与归档', content: <>{['writing', 'ready'].includes(paper.status) && <label className="form-label">投稿阶段<select aria-label="投稿阶段" value={paper.status} onChange={e => { setPaper({ ...paper, status: e.target.value as State }); setDirty(true); }}><option value="writing">撰写中</option><option value="ready">待投稿</option></select></label>}{paper.status === 'writing' && <label className="form-label">写作进度（%）<input aria-label="写作进度" type="number" min="0" max="100" value={paper.progress} onChange={e => { setPaper({ ...paper, progress: Number(e.target.value) }); setDirty(true); }} /></label>}<label className="form-label">关联项目<select aria-label="论文关联项目" value={paper.project_id || ''} onChange={e => { setPaper({ ...paper, project_id: e.target.value || null, source_kind: null, source_id: null, source_title: '' }); setDirty(true); }}><option value="">不关联项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{paper.source_id && <p className="scope-note">已关联研究成果：{paper.source_title || '保存时记录来源题名'}</p>}<label className="form-label">论文备注<textarea aria-label="论文备注" rows={4} maxLength={12000} value={paper.notes} onChange={e => { setPaper({ ...paper, notes: e.target.value }); setDirty(true); }} /></label><label className="submission-check"><input type="checkbox" checked={!!paper.archived} onChange={e => { setPaper({ ...paper, archived: e.target.checked }); setDirty(true); }} />归档论文（保留全部记录）</label></> },
    ]} />}
    {detail && !paper && <PaperDialog paper={detail} journals={data?.journals || []} busy={busy} error={error} notice={detailNotice} onDirty={setDirty} onClose={async () => { if (!busy && (!dirty || await confirmDiscard('离开后本次填写不会保存，已记录的信息不受影响'))) { setDetail(null); setDirty(false); setDetailNotice(''); } }} onEdit={() => { setPaper(detail); setDetail(null); setDirty(false); setDetailNotice(''); }} onAction={body => void run(async () => { const saved = await request<PaperDetail>(`${endpoint}/papers/${detail.id}/actions`, 'POST', { ...body, revision: detail.revision }); setDetail(saved); setDirty(false); await refresh(); setDetailNotice('投稿进展已记录'); })} />}
    {importing && <Modal title="预览期刊目录" onClose={closeForm}><p>{importing.label}</p><p>共 {importing.report.rows} 种期刊 · 新增 {importing.report.new} 种 · 已有 {importing.report.existing} 种</p><p className="scope-note">{importing.report.policy}</p>{importing.report.already_imported && <p>该文件已经导入，重复确认不会新增记录</p>}{importing.report.error_count > 0 ? <div role="alert" className="scope-warning">共 {importing.report.error_count} 个错误，未导入任何行{importing.report.errors.map((e, i) => <p key={i}>{e}</p>)}</div> : <ul>{importing.report.sample.map(j => <li key={j.name}>{j.name} · {j.discipline} · {j.level}</li>)}</ul>}<div className="modal-actions"><button type="button" className="button-secondary" disabled={busy} onClick={closeForm}>取消</button>{importing.report.already_imported ? <button type="button" className="button-primary" onClick={() => setImporting(null)}>完成</button> : <button className="button-primary" disabled={busy || importing.report.error_count > 0} onClick={() => void run(async () => { const result = await request<{ imported: number }>(`${endpoint}/import`, 'POST', { content: importing.content, label: importing.label, fingerprint: importing.report.fingerprint }); setImporting(null); await refresh(); setNotice(result.imported ? `目录导入完成，共 ${result.imported} 种期刊` : '该目录已导入，无需重复添加'); })}>{busy ? '导入中…' : '确认导入'}</button>}</div>{error && <p role="alert" className="scope-warning">{error}</p>}</Modal>}
    {catalogImport && <Modal title="预览期刊目录" onClose={() => { if (!busy) setCatalogImport(null); }}><p>{catalogImport.label}</p><p>共 {catalogImport.fresh.length + catalogImport.updates.length + catalogImport.unchanged} 种 · 新增 {catalogImport.fresh.length} 种 · 补全 {catalogImport.updates.length} 种 · 保留 {catalogImport.unchanged} 种</p><p className="scope-note">已有期刊卡片与人工填写的信息会保留，只补充空白字段；导入会逐条写入公共目录，完成后请核对</p>{busy && <p role="status">正在写入 {catalogImport.progress} / {catalogImport.total}…</p>}{catalogImport.errors.length > 0 && <div role="alert" className="scope-warning">共 {catalogImport.errors.length} 条问题，相关行不会导入{catalogImport.errors.slice(0, 20).map((item, index) => <p key={index}>{item}</p>)}</div>}{catalogImport.failures.length > 0 && <div role="alert" className="scope-warning">写入失败 {catalogImport.failures.length} 条{catalogImport.failures.slice(0, 20).map((item, index) => <p key={index}>{item}</p>)}</div>}<div className="modal-actions"><button type="button" className="button-secondary" disabled={busy} onClick={() => setCatalogImport(null)}>取消</button><button type="button" className="button-primary" disabled={busy || catalogImport.total === 0} onClick={() => void confirmCatalogImport()}>{busy ? '正在导入…' : `确认导入 ${catalogImport.total} 种`}</button></div>{error && <p role="alert" className="scope-warning">{error}</p>}</Modal>}
  </div>;
}

function Link({ url, children }: { url: string; children: React.ReactNode }) {
  if (url.startsWith('mailto:')) return <span className="submission-email" title="投稿邮箱">邮箱：{url.slice(7)}</span>;
  return url ? <button className="button-quiet" title={url} onClick={() => { void platform.openExternal(url); }}><ExternalLink size={16} />{children}</button> : null;
}
function JournalSources({ id }: { id: string }) {
  const [sources, setSources] = useState<{ label: string; original: Journal }[]>([]), [error, setError] = useState('');
  useEffect(() => { let active = true; void request<{ sources: typeof sources }>(`${endpoint}/journals/${id}`).then(r => { if (active) setSources(r.sources); }).catch(e => { if (active) setError(message(e)); }); return () => { active = false; }; }, [id]);
  return <details className="submission-sources"><summary>原始目录信息（{sources.length}）</summary>{sources.map((s, i) => <p key={i}>{s.label}<br />{s.original.name} · {s.original.discipline} · {s.original.level}<br />官网：{s.original.website || '未提供'}<br />投稿地址：{s.original.submission_url || '未提供'}</p>)}{!sources.length && <p>手动新增的期刊，尚无目录来源</p>}{error && <p role="alert">{error}</p>}</details>;
}
function PaperDialog({ paper, journals, busy, error, notice, onDirty, onClose, onEdit, onAction }: { paper: PaperDetail; journals: Journal[]; busy: boolean; error: string; notice: string; onDirty(dirty: boolean): void; onClose(): void; onEdit(): void; onAction(body: { action: string; date: string; note: string; journal_id: string | null }): void }) {
  const [action, setAction] = useState(''), [day, setDay] = useState(today), [note, setNote] = useState(''), [journal, setJournal] = useState<string | null>(paper.target_journal_id);
  useEffect(() => { setAction(''); setNote(''); setDay(today()); setJournal(paper.target_journal_id); }, [paper.revision, paper.id, paper.target_journal_id]);
  const primaryAction = paper.status === 'review' ? 'request_revision' : paper.status === 'revision' ? 'revise' : allowed[paper.status][0];
  const startAction = (next: string) => {
    if (next === 'withdraw') {
      void confirmDialog({ title: '撤回这篇投稿？', description: '撤回后论文会回到可重新投稿的状态，已记录的进展保留', confirmLabel: '撤回投稿', cancelLabel: '取消', tone: 'danger' }).then(ok => { if (ok) { setAction(next); onDirty(true); } });
      return;
    }
    setAction(next); onDirty(true);
  };
  return <Drawer title="论文详情" width={560} onClose={onClose} footer={<button type="button" className="button-secondary" disabled={busy} onClick={onClose}>关闭</button>}><h3 className="submission-paper-title">{paper.title}</h3>
    {notice && <p role="status" className="submission-notice">{notice}</p>}
    <div className="submission-paper-heading"><span>{paperStates[paper.status]} · {paper.authors || '作者未填写'}</span><button type="button" disabled={busy || !!action} className="button-secondary" onClick={onEdit}><Pencil size={16} />编辑论文</button></div>{paper.source_id && <p className="scope-note">来源成果：{paper.source_title || paper.source_id}</p>}{paper.notes && <p className="submission-preserve">{paper.notes}</p>}<p className="scope-note">目标期刊：{journals.find(j => j.id === paper.target_journal_id)?.name || paper.target_journal_name || '未设置'}</p>
    {!paper.archived && <div className="submission-action-row">{allowed[paper.status].map(a => <button key={a} type="button" className={a === primaryAction ? 'button-primary' : a === 'withdraw' ? 'button-quiet submission-withdraw' : 'button-secondary'} disabled={busy || !!action} onClick={() => startAction(a)}>{a === 'submit' && paper.status === 'rejected' ? '改投新期刊' : actionNames[a]}</button>)}</div>}
    {action && <form className="submission-action-form" onSubmit={e => { e.preventDefault(); onAction({ action, date: day, note, journal_id: journal }); }}><h3>{actionNames[action]}</h3>{action === 'submit' && <JournalPicker journals={journals} value={journal} onChange={setJournal} />}<label className="form-label">实际日期<input aria-label="投稿进展日期" required type="date" max={today()} value={day} onChange={e => setDay(e.target.value)} /></label><label className="form-label">记录说明<textarea aria-label="投稿进展说明" rows={3} maxLength={12000} placeholder="审稿意见、拒稿原因、稿件编号或返修要求等" value={note} onChange={e => setNote(e.target.value)} /></label><p className="scope-note">这里只记录进展；正式投稿请打开期刊投稿地址完成</p><div className="modal-actions"><button type="button" className="button-secondary" disabled={busy} onClick={() => { setAction(''); onDirty(false); }}>取消操作</button><button className="button-primary" disabled={busy || action === 'submit' && !journal}>{busy ? '保存中…' : '确认记录'}</button></div></form>}
    {error && <p role="alert" className="scope-warning">{error}</p>}<h3>投稿记录</h3>{!paper.attempts.length && <p className="scope-note">尚未正式投稿</p>}{paper.attempts.map((attempt, i) => <section className="submission-attempt" key={attempt.id}><strong>第 {i + 1} 次投稿 · {attempt.journal_name}</strong><p>{attempt.submitted_date} · {attempt.status === 'withdrawn' ? '已撤稿' : paperStates[attempt.status]} · 第 {attempt.round} 轮</p><div className="journal-links"><Link url={journals.find(j => j.id === attempt.journal_id)?.website || ''}>期刊官网</Link><Link url={journals.find(j => j.id === attempt.journal_id)?.submission_url || ''}>投稿地址</Link></div>{paper.events.filter(e => e.attempt_id === attempt.id).map(e => <div className="submission-timeline" key={e.id}><strong>{e.event_date} · {actionNames[e.kind]}</strong>{e.note && <p className="submission-preserve">{e.note}</p>}</div>)}</section>)}<details className="submission-sources"><summary>完整操作记录</summary>{paper.events.map(e => <p key={e.id}>{e.event_date} · {actionNames[e.kind] || e.kind}{e.detail.source_title ? ` · ${e.detail.source_title}` : ''}</p>)}</details>
  </Drawer>;
}

function PaperList({ papers, journals, onOpen }: { papers: Paper[]; journals: Journal[]; onOpen(id: string): void }) {
  if (!papers.length) return <p className="empty-block">没有符合条件的论文</p>;
  return <div className="paper-list"><table className="paper-table"><caption className="sr-only">论文列表</caption><thead><tr><th scope="col">论文题目</th><th scope="col">状态</th><th scope="col">目标期刊</th><th scope="col">更新时间</th><th scope="col">优先级</th></tr></thead><tbody>{papers.map(p => <tr key={p.id} className="paper-row" tabIndex={0} onClick={() => onOpen(p.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(p.id); } }}>
    <td><span className="paper-row-title">{p.title}</span>{p.authors && <small>{p.authors}</small>}</td>
    <td><span className={`paper-state state-${p.status}`}>{paperStates[p.status]}</span>{!!p.archived && <small>已归档</small>}</td>
    <td>{p.target_journal_id ? journals.find(j => j.id === p.target_journal_id)?.name || '未设置' : p.current_journal_name || '未选择'}</td>
    <td>{updatedLabel(p.updated_at)}</td>
    <td><span className={`paper-priority priority-${p.priority}`}>{priorityNames[p.priority]}</span></td>
  </tr>)}</tbody></table></div>;
}

function ChineseJournalTable({journals,onEdit}:{journals:Journal[];onEdit(journal:Journal):void}) {
  return <div className="catalog-table-scroll"><table className="catalog-table"><thead><tr><th scope="col">期刊名称</th><th scope="col">学科门类</th><th scope="col">收录等级</th><th scope="col">相关链接</th><th scope="col"><span className="sr-only">操作</span></th></tr></thead><tbody>{journals.map(journal=><tr key={journal.id}><td><div className="catalog-journal-name"><span className="catalog-journal-icon"><BookOpen size={18}/></span><strong>{journal.name}</strong></div></td><td>{journal.discipline||'—'}</td><td><span className="catalog-level">{journal.level||'未标注'}</span></td><td><div className="journal-links"><Link url={journal.website}>官网<ArrowUpRight size={16}/></Link><Link url={journal.submission_url}>投稿<ArrowUpRight size={16}/></Link>{!journal.website&&!journal.submission_url&&<span className="catalog-no-link">暂无链接</span>}</div></td><td><button className="catalog-edit" aria-label={`编辑期刊：${journal.name}`} onClick={()=>onEdit(journal)}><Pencil size={16}/>编辑</button></td></tr>)}</tbody></table></div>;
}
