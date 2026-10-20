import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, RotateCcw, Search } from 'lucide-react';
import { request } from './api';
import './english-journals.css';

type SortKey = 'if' | 'cas' | 'jcr' | 'xr' | 'name';

export type EnglishJournal = {
  id: string
  name: string
  issn: string
  wos: string
  jif: number | null
  jcr_q: string
  jcr_cats: string
  cas_cat: string
  cas_tier: number | null
  cas_top: boolean
  cas_rank: string
  cas_small: string
  cas_review: boolean
  cas_oa: boolean
  cas_flag: string
  xr_cat: string
  xr_tier: number | null
  xr_top: boolean
  xr_small: string
  xr_flag: string
};

export type EnglishMeta = {
  label: string
  generated_at: string
  count: number
  sources: Record<string, string>
  notes: string[]
  disciplines: { name: string; count: number }[]
};

export type EnglishJournalResponse = {
  total: number
  page: number
  page_size: number
  pages: number
  items: EnglishJournal[]
  meta: EnglishMeta
};

const endpoint = '/api/submissions/english-journals';
const VERSION_NOTE = 'JCR 2025（Clarivate 2025 版，2026 年 6 月发布）· 中科院 2025 大类分区 · 新锐 2026';
const PAGE_SIZES: number[] = [20, 50, 100];
const JCR_TIERS = ['Q1', 'Q2', 'Q3', 'Q4'];
const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'if', label: '按 IF 排序' },
  { value: 'cas', label: '按中科院分区' },
  { value: 'jcr', label: '按 JCR 分区' },
  { value: 'xr', label: '按新锐分区' },
  { value: 'name', label: '按刊名排序' },
];
const JCR_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: 'Q1' },
  { value: 2, label: 'Q2 及以上' },
  { value: 3, label: 'Q3 及以上' },
  { value: 4, label: 'Q4 及以上' },
];
const TIER_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: '1 区' },
  { value: 2, label: '2 区及以上' },
  { value: 3, label: '3 区及以上' },
  { value: 4, label: '4 区及以上' },
];

const message = (error: unknown) => (error instanceof Error ? error.message : '请求英文期刊库失败');
const formatCount = (value: number) => value.toLocaleString('zh-CN');
const formatIf = (value: number | null) => (typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : '—');
const tierClass = (tier: number | null) => (tier === 1 || tier === 2 || tier === 3 || tier === 4 ? `ej-tier-${tier}` : 'ej-tier-none');
const jcrClass = (quarter: string) => (JCR_TIERS.includes(quarter) ? `ej-tier-${quarter.slice(1)}` : 'ej-tier-none');
const tierText = (tier: number | null) => (tier === 1 || tier === 2 || tier === 3 || tier === 4 ? `${tier} 区` : '未收录');
const text = (value: string | null | undefined) => (typeof value === 'string' ? value.trim() : '');
const errorHint = (detail: string) => {
  if (detail.includes('缺失')) return '英文期刊库数据文件缺失：请确认后端已放置 data/english_journals.json.gz，并重启本地服务后重试。';
  if (detail.includes('503')) return '本地服务暂时不可用（503），请稍后重试。';
  return '请确认本地服务正在运行，然后重试。';
};

export default function EnglishJournalLibrary() {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [discipline, setDiscipline] = useState('');
  const [jcr, setJcr] = useState(0);
  const [cas, setCas] = useState(0);
  const [xr, setXr] = useState(0);
  const [top, setTop] = useState(false);
  const [xrTop, setXrTop] = useState(false);
  const [sort, setSort] = useState<SortKey>('if');
  const [pageSize, setPageSize] = useState(50);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<EnglishJournalResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [expanded, setExpanded] = useState<string[]>([]);
  const sequence = useRef(0);

  // 搜索输入防抖 300ms；与已生效关键字相同时不重置计时器（首次挂载不延迟）。
  useEffect(() => {
    const next = query.trim();
    if (next === debouncedQuery) return;
    const timer = window.setTimeout(() => {
      setDebouncedQuery(next);
      setPage(1);
      setExpanded([]);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, debouncedQuery]);

  useEffect(() => {
    sequence.current += 1;
    const current = sequence.current;
    let active = true;
    const params = new URLSearchParams();
    if (debouncedQuery) params.set('q', debouncedQuery);
    if (discipline) params.set('discipline', discipline);
    if (jcr > 0) params.set('jcr', String(jcr));
    if (cas > 0) params.set('cas', String(cas));
    if (xr > 0) params.set('xr', String(xr));
    if (top) params.set('top', 'true');
    if (xrTop) params.set('xrtop', 'true');
    params.set('sort', sort);
    params.set('page', String(page));
    params.set('page_size', String(pageSize));
    setLoading(true);
    request<EnglishJournalResponse>(`${endpoint}?${params.toString()}`)
      .then(result => {
        // 旧响应（防抖/翻页/筛选竞态）一律丢弃。
        if (!active || sequence.current !== current) return;
        setError('');
        // 页码越界（多发生在缩小每页条数后）时静默修正到最后一页。
        if (result.total > 0 && result.pages > 0 && page > result.pages) {
          setPage(result.pages);
          return;
        }
        setData(result);
      })
      .catch(cause => {
        if (active && sequence.current === current) setError(message(cause));
      })
      .finally(() => {
        if (active && sequence.current === current) setLoading(false);
      });
    return () => { active = false; };
  }, [debouncedQuery, discipline, jcr, cas, xr, top, xrTop, sort, pageSize, page, reload]);

  const toggleRow = useCallback((id: string) => {
    setExpanded(previous => (previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id]));
  }, []);

  // 筛选条件或每页条数变化：回到第 1 页并收起展开行（与状态更新同批，只触发一次请求）。
  const change = (apply: () => void) => {
    apply();
    setPage(1);
    setExpanded([]);
  };

  const reset = () => {
    setQuery('');
    setDebouncedQuery('');
    setDiscipline('');
    setJcr(0);
    setCas(0);
    setXr(0);
    setTop(false);
    setXrTop(false);
    setSort('if');
    setPage(1);
    setExpanded([]);
  };

  const hasFilter = !!debouncedQuery || !!discipline || jcr > 0 || cas > 0 || xr > 0 || top || xrTop;
  const rows = data?.items ?? [];
  const shownPage = data?.page ?? page;
  const shownPages = Math.max(data?.pages ?? 0, 1);
  const disciplines = data?.meta.disciplines ?? [];

  return <section className="english-journals" aria-label="英文期刊库">
    <div className="ej-filters" role="group" aria-label="英文期刊筛选">
      <label className="ej-search"><Search size={15} aria-hidden="true" />
        <input aria-label="搜索英文期刊刊名或 ISSN" placeholder="搜索刊名或 ISSN，如 Nature、0028-0836" maxLength={200} value={query} onChange={event => setQuery(event.target.value)} />
      </label>
      <label className="ej-field">学科<select aria-label="按学科筛选" value={discipline} onChange={event => change(() => setDiscipline(event.target.value))}>
        <option value="">全部学科</option>
        {disciplines.map(entry => <option key={entry.name} value={entry.name}>{entry.name}（{formatCount(entry.count)}）</option>)}
      </select></label>
      <label className="ej-field">JCR 分区<select aria-label="按 JCR 分区筛选" value={jcr} onChange={event => change(() => setJcr(Number(event.target.value)))}>
        <option value={0}>全部</option>
        {JCR_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>
      <label className="ej-field">中科院分区<select aria-label="按中科院分区筛选" value={cas} onChange={event => change(() => setCas(Number(event.target.value)))}>
        <option value={0}>全部</option>
        {TIER_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>
      <label className="ej-field">新锐分区<select aria-label="按新锐分区筛选" value={xr} onChange={event => change(() => setXr(Number(event.target.value)))}>
        <option value={0}>全部</option>
        {TIER_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>
      <label className="ej-check"><input type="checkbox" aria-label="只看中科院 Top" checked={top} onChange={event => change(() => setTop(event.target.checked))} />只看中科院 Top</label>
      <label className="ej-check"><input type="checkbox" aria-label="只看新锐 Top" checked={xrTop} onChange={event => change(() => setXrTop(event.target.checked))} />只看新锐 Top</label>
      <label className="ej-field">排序<select aria-label="排序方式" value={sort} onChange={event => change(() => setSort(event.target.value as SortKey))}>
        {SORT_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>
      <button type="button" className="button-secondary ej-reset" onClick={reset}><RotateCcw size={14} aria-hidden="true" />重置筛选</button>
    </div>

    <p className="ej-stats" role="status" aria-live="polite">
      <span>命中 <strong>{data ? formatCount(data.total) : '—'}</strong> 种 · 第 {shownPage} / {shownPages} 页{loading && <span className="ej-loading">正在更新…</span>}</span>
      <span className="ej-version">{VERSION_NOTE}</span>
    </p>

    {error && <div role="alert" className="scope-warning ej-error">
      <strong>{error}</strong>
      <span>{errorHint(error)}</span>
      <button type="button" className="button-secondary" onClick={() => setReload(value => value + 1)}>重试</button>
    </div>}

    {data === null
      ? !error && <p className="ej-status" role="status">正在加载英文期刊库…</p>
      : <div className="ej-table-wrap" aria-busy={loading}>
        <table className="ej-table" aria-label="英文期刊列表">
          <colgroup>
            <col className="ej-col-journal" /><col className="ej-col-subject" /><col className="ej-col-jcr" />
            <col className="ej-col-cas" /><col className="ej-col-xr" /><col className="ej-col-wos" />
            <col className="ej-col-flag" /><col className="ej-col-actions" />
          </colgroup>
          <thead><tr>
            <th scope="col">期刊</th><th scope="col">学科</th><th scope="col">JCR 2025</th><th scope="col">中科院 2025</th>
            <th scope="col">新锐 2026</th><th scope="col">收录</th><th scope="col">预警</th><th scope="col">操作</th>
          </tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td className="ej-empty" colSpan={8}>{hasFilter ? '没有符合筛选条件的期刊。可放宽分区条件，或点“重置筛选”。' : '英文期刊库暂无数据。'}</td></tr>}
            {rows.map(item => <JournalRow key={item.id} item={item} expanded={expanded.includes(item.id)} onToggle={toggleRow} />)}
          </tbody>
        </table>
      </div>}

    {data && data.total > 0 && <div className="ej-pager" role="group" aria-label="英文期刊分页">
      <button type="button" className="button-secondary" disabled={loading || page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>上一页</button>
      <span>第 {shownPage} / {shownPages} 页 · 共 {formatCount(data.total)} 种</span>
      <button type="button" className="button-secondary" disabled={loading || page >= shownPages} onClick={() => setPage(value => Math.min(shownPages, value + 1))}>下一页</button>
      <label className="ej-field">每页<select aria-label="每页条数" value={pageSize} onChange={event => change(() => setPageSize(Number(event.target.value)))}>
        {PAGE_SIZES.map(size => <option key={size} value={size}>{size} 条</option>)}
      </select></label>
      {data.meta.count > 0 && <span className="ej-library-total">库内 {formatCount(data.meta.count)} 种</span>}
    </div>}

    {data && <details className="ej-sources">
      <summary>数据来源与统计口径</summary>
      <p>{data.meta.label} · 共 {formatCount(data.meta.count)} 种 · 生成于 {data.meta.generated_at}</p>
      {Object.entries(data.meta.sources).map(([key, value]) => <p key={key}>{value}</p>)}
      {data.meta.notes.map((note, index) => <p key={index}>{note}</p>)}
      <p>分区与 IF 仅作投稿参考，投稿前请核对期刊当前收录状态与要求。</p>
    </details>}
  </section>;
}

function JournalRowBase({ item, expanded, onToggle }: { item: EnglishJournal; expanded: boolean; onToggle(id: string): void }) {
  const detailId = `ej-detail-${item.id}`;
  const subject = text(item.cas_cat) || text(item.xr_cat) || '未收录';
  const casTier = item.cas_tier ?? null;
  const xrTier = item.xr_tier ?? null;
  const quarter = text(item.jcr_q);
  const hasJcrTier = JCR_TIERS.includes(quarter);
  const wos = text(item.wos);
  const unstable = /suppressed|onhold/i.test(wos);
  const warnings: { label: string; warn: boolean }[] = [];
  if (text(item.xr_flag)) warnings.push({ label: `新锐 · ${text(item.xr_flag)}`, warn: true });
  if (text(item.cas_flag).includes('预警')) warnings.push({ label: `中科院 · ${text(item.cas_flag)}`, warn: true });
  const tags = [
    wos ? `收录：${wos}` : '',
    text(item.issn) ? `ISSN：${text(item.issn)}` : '',
    text(item.cas_rank) ? `中科院排名：${text(item.cas_rank)}` : '',
    text(item.cas_flag) ? `中科院标注：${text(item.cas_flag)}` : '',
    item.cas_review ? '中科院综述期刊' : '',
    item.cas_oa ? '中科院 OA 期刊' : '',
    `IF：${formatIf(item.jif)}`,
  ].filter(Boolean);

  return <>
    <tr className={expanded ? 'ej-row is-open' : 'ej-row'} aria-selected={expanded} onClick={() => onToggle(item.id)}>
      <td className="ej-journal">
        <strong title={item.name}>{item.name}</strong>
        <small>ISSN {text(item.issn) || '未提供'}</small>
      </td>
      <td className="ej-subject">{subject}</td>
      <td className="ej-cell-jcr">
        <span className={`ej-pill ${jcrClass(quarter)}`} title={text(item.jcr_cats) || '无 JCR 学科明细'}>{hasJcrTier ? quarter : '无分区'}</span>
        <small className="ej-if">IF {formatIf(item.jif)}</small>
      </td>
      <td className="ej-cell-cas">
        <span className={`ej-pill ${tierClass(casTier)}`}>{tierText(casTier)}</span>
        {item.cas_top && <span className="ej-top" title="中科院 2025 大类 Top 期刊">Top</span>}
        {text(item.cas_rank) && <small className="ej-ranking">排名 {text(item.cas_rank)}</small>}
      </td>
      <td className="ej-cell-xr">
        <span className={`ej-pill ${tierClass(xrTier)}`}>{tierText(xrTier)}</span>
        {item.xr_top && <span className="ej-top" title="新锐 2026 Top 期刊">Top</span>}
      </td>
      <td className={unstable ? 'ej-wos is-unstable' : 'ej-wos'} title={unstable ? '该刊在 Web of Science 中已被压制或暂停收录' : undefined}>{wos || '—'}</td>
      <td className="ej-cell-flag">
        {warnings.length === 0 && <span className="ej-dash">—</span>}
        {warnings.map(entry => <span key={entry.label} className={entry.warn ? 'ej-flag is-warn' : 'ej-flag'} title={entry.label}>{entry.label}</span>)}
      </td>
      <td className="ej-actions">
        <button type="button" className="button-quiet" aria-expanded={expanded} aria-controls={detailId} onClick={event => { event.stopPropagation(); onToggle(item.id); }}>
          {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}详情
        </button>
      </td>
    </tr>
    {expanded && <tr className="ej-detail-row">
      <td colSpan={8}>
        <div className="ej-detail" id={detailId}>
          <div className="ej-detail-block">
            <h4>中科院 2025 小类</h4>
            <p>{text(item.cas_small) || '无小类明细'}</p>
          </div>
          <div className="ej-detail-block">
            <h4>新锐 2026 小类</h4>
            <p>{text(item.xr_small) || '无小类明细'}</p>
          </div>
          <div className="ej-detail-block">
            <h4>JCR 2025 学科</h4>
            <p>{text(item.jcr_cats) || '无 JCR 学科明细'}</p>
          </div>
          <div className="ej-detail-block">
            <h4>其它标注</h4>
            <ul className="ej-tags">{tags.map(tag => <li key={tag}>{tag}</li>)}</ul>
          </div>
        </div>
      </td>
    </tr>}
  </>;
}

const JournalRow = memo(JournalRowBase);
