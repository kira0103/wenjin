import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, ChevronRight, Download, RefreshCw } from 'lucide-react';
import { call } from '../workbench/transport';
import type { ManagedUser } from './types';

type Granularity = 'day' | 'week' | 'month';

type SummaryRange = { from: string; to: string; granularity: string; timezone: string };
type Totals = {
  requests: number; measured_requests: number; unknown_requests: number;
  input_tokens: number; output_tokens: number; cached_tokens: number; total_tokens: number;
  estimated_cost: number; cost_unpriced_requests: number; currency: string;
  accounts: number; projects: number;
};
type UsageBucket = {
  date: string; requests: number; unknown_requests: number;
  input_tokens: number; output_tokens: number; cached_tokens: number; total_tokens: number;
  estimated_cost: number;
};
type FeatureRow = { feature: string; requests: number; total_tokens: number; estimated_cost: number };
type ModelRow = { model_id: string; model_name: string; provider_name: string; requests: number; total_tokens: number; estimated_cost: number };
type AccountProject = { project_id: string; project_name: string; requests: number; total_tokens: number; estimated_cost: number; last_at: string };
type AccountRow = { account_id: string; username: string; display_name: string; requests: number; total_tokens: number; estimated_cost: number; projects: AccountProject[] };
type ProjectRow = {
  account_id: string; username: string; display_name: string; project_id: string; project_name: string;
  requests: number; unknown_requests: number; total_tokens: number; estimated_cost: number; last_at: string;
};
type Summary = {
  range: SummaryRange;
  totals: Totals;
  buckets: UsageBucket[];
  by_feature: FeatureRow[];
  by_model: ModelRow[];
  accounts: AccountRow[];
  projects: ProjectRow[];
  sources: { workspaces_scanned: number; workspaces_with_ledger: number; generated_at: string };
};

const featureNames: Record<string, string> = {
  collation_batch: '批量点校',
  collation_debug: '点校调试',
  task: '研究工作',
  preprocess_analyze: '预处理识别',
  research_assistant: '研究助手',
  other: '其他',
};
const granularityNames: Record<Granularity, string> = { day: '按日', week: '按周', month: '按月' };
const granularities: Granularity[] = ['day', 'week', 'month'];
const maxRangeDays = 366;

function pad2(value: number) { return String(value).padStart(2, '0'); }
function localDate(value: Date) { return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`; }
function daysAgo(days: number) {
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  today.setDate(today.getDate() - days);
  return localDate(today);
}
function dateSpan(from: string, to: string) {
  const start = new Date(`${from}T00:00:00`);
  const end = new Date(`${to}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0;
  return Math.round((end.getTime() - start.getTime()) / 86400000);
}
function formatCount(value: number | null | undefined) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number).toLocaleString('zh-CN') : '—';
}
function formatMoney(value: number | null | undefined) {
  const number = Number(value);
  return Number.isFinite(number) ? `¥${Math.max(0, number).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
}
function formatCompact(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0';
  if (value >= 100000000) return `${(value / 100000000).toFixed(1)}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}万`;
  return value.toLocaleString('zh-CN');
}
function formatWhen(value: string) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
function formatBucket(date: string, granularity: string) {
  if (!date) return '—';
  if (granularity === 'day' && date.length >= 10) return `${date.slice(5, 7)}-${date.slice(8, 10)}`;
  return date.length >= 10 ? date.slice(5) : date;
}
function featureLabel(feature: string) { return featureNames[feature] || feature || '其他'; }
function errorText(error: unknown) { return error instanceof Error ? error.message : '用量数据读取失败，请重试'; }
function niceMax(value: number) {
  const safe = Math.max(1, value);
  const magnitude = Math.pow(10, Math.floor(Math.log10(safe)));
  const normalized = safe / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

export default function UsagePanel({ users }: { users: ManagedUser[] }) {
  const [from, setFrom] = useState(() => daysAgo(29));
  const [to, setTo] = useState(() => daysAgo(0));
  const [granularity, setGranularity] = useState<Granularity>('day');
  const [accountId, setAccountId] = useState('');
  const [projectId, setProjectId] = useState('');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [tableOpen, setTableOpen] = useState(false);
  const [expandedAccounts, setExpandedAccounts] = useState<string[]>([]);
  const [reload, setReload] = useState(0);
  const requestId = useRef(0);
  const hasData = useRef(false);

  const filtersProblem = useMemo(() => {
    if (!from) return '请选择开始日期';
    if (!to) return '请选择结束日期';
    if (from > to) return '开始日期不能晚于结束日期';
    if (dateSpan(from, to) + 1 > maxRangeDays) return `日期跨度不能超过 ${maxRangeDays} 天`;
    return '';
  }, [from, to]);

  const query = useMemo(() => {
    const params = new URLSearchParams({ from, to, granularity, account_id: accountId, project_id: projectId });
    return params.toString();
  }, [from, to, granularity, accountId, projectId]);

  const load = useCallback(async () => {
    if (filtersProblem) {
      setError(filtersProblem);
      setLoading(false);
      setRefreshing(false);
      return;
    }
    const ticket = ++requestId.current;
    setLoading(!hasData.current);
    setRefreshing(hasData.current);
    setError('');
    setExportError('');
    try {
      const value = await call<Summary>({ path: `/api/admin/usage/summary?${query}`, silent: true });
      if (ticket !== requestId.current) return;
      hasData.current = true;
      setSummary(value);
      // 当前项目在新的汇总结果里不存在时（例如换了账号），回到“全部项目”。
      if (projectId && !value.projects.some((row) => row.project_id === projectId)) setProjectId('');
    } catch (reason) {
      if (ticket !== requestId.current) return;
      setError(errorText(reason));
    } finally {
      if (ticket === requestId.current) { setLoading(false); setRefreshing(false); }
    }
  }, [filtersProblem, query, projectId]);

  useEffect(() => { void load(); }, [load, reload]);

  const projects = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of summary?.projects || []) {
      if (!row.project_id) continue;
      if (!seen.has(row.project_id)) seen.set(row.project_id, row.project_name || '未命名项目');
    }
    return [...seen.entries()].map(([id, label]) => ({ id, label }));
  }, [summary]);

  const featureRows = useMemo(() => (summary?.by_feature || []).filter((row) => row.requests > 0 || row.total_tokens > 0), [summary]);
  const modelRows = useMemo(() => (summary?.by_model || []).filter((row) => row.requests > 0 || row.total_tokens > 0), [summary]);
  const accounts = useMemo(() => [...(summary?.accounts || [])].sort((a, b) => b.total_tokens - a.total_tokens), [summary]);
  const projectRows = useMemo(() => [...(summary?.projects || [])].sort((a, b) => b.total_tokens - a.total_tokens), [summary]);
  const maxFeature = Math.max(1, ...featureRows.map((row) => row.total_tokens));
  const maxModel = Math.max(1, ...modelRows.map((row) => row.total_tokens));

  const chart = useMemo(() => {
    const buckets = summary?.buckets || [];
    const count = buckets.length;
    const slot = Math.max(16, Math.min(54, Math.floor(860 / Math.max(1, count))));
    const barWidth = Math.max(5, Math.min(34, Math.floor(slot * 0.58)));
    const padLeft = 66, padRight = 18, chartTop = 20, plotHeight = 210;
    const width = padLeft + padRight + Math.max(1, count) * slot;
    const baseline = chartTop + plotHeight;
    const maximum = niceMax(Math.max(1, ...buckets.map((bucket) => bucket.input_tokens + bucket.output_tokens)));
    const height = baseline + 46;
    const labelStep = Math.max(1, Math.ceil(count / 9));
    const bars = buckets.map((bucket, index) => {
      const x = padLeft + index * slot + (slot - barWidth) / 2;
      const inputHeight = (bucket.input_tokens / maximum) * plotHeight;
      const outputHeight = (bucket.output_tokens / maximum) * plotHeight;
      return { bucket, index, x, inputHeight, outputHeight, total: bucket.input_tokens + bucket.output_tokens, showLabel: index % labelStep === 0 || index === count - 1 };
    });
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({ value: maximum * ratio, y: baseline - plotHeight * ratio }));
    return { bars, ticks, slot, barWidth, padLeft, padRight, width, height, baseline, plotHeight, labelStep };
  }, [summary]);

  const hasUsage = !!summary && summary.totals.requests > 0;
  const hasCostWarning = !!summary && summary.totals.cost_unpriced_requests > 0;

  function toggleAccount(id: string) {
    setExpandedAccounts((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  async function exportCsv() {
    if (filtersProblem) { setExportError(filtersProblem); return; }
    setExporting(true);
    setExportError('');
    try {
      const response = await fetch(`/api/admin/usage/export.csv?${query}`, { credentials: 'include' });
      if (!response.ok) {
        let detail = `导出失败（${response.status}）`;
        try {
          const body = await response.json() as { detail?: unknown };
          if (typeof body.detail === 'string' && body.detail) detail = body.detail;
        } catch { /* 非 JSON 错误响应使用状态码提示。 */ }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      const match = /filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i.exec(disposition);
      let filename = `usage-${from}-${to}.csv`;
      if (match) { try { filename = decodeURIComponent(match[1]); } catch { filename = match[1]; } }
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : '导出失败，请重试');
    } finally {
      setExporting(false);
    }
  }

  function accountLastAt(row: AccountRow) {
    const values = row.projects.map((project) => project.last_at).filter(Boolean).sort();
    return values.length ? values[values.length - 1] : '';
  }

  return <section className="portal-usage" aria-busy={loading || refreshing} aria-label="用量台账">
    <div className="portal-usage-filters" role="group" aria-label="用量筛选">
      <label>账号
        <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
          <option value="">全部账号</option>
          {users.map((user) => <option key={user.id} value={user.id}>{user.display_name} · @{user.username}</option>)}
        </select>
      </label>
      <label>开始日期<input type="date" aria-label="开始日期" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} /></label>
      <label>结束日期<input type="date" aria-label="结束日期" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} /></label>
      <label>粒度
        <select value={granularity} onChange={(event) => setGranularity(event.target.value as Granularity)}>
          {granularities.map((value) => <option key={value} value={value}>{granularityNames[value]}</option>)}
        </select>
      </label>
      <label>项目
        <select value={projectId} onChange={(event) => setProjectId(event.target.value)} disabled={!projects.length}>
          <option value="">全部项目</option>
          {projects.map((project) => <option key={project.id} value={project.id}>{project.label}</option>)}
        </select>
      </label>
      <div className="portal-usage-actions">
        <button type="button" className="portal-secondary" disabled={loading || refreshing} onClick={() => setReload((value) => value + 1)}>
          <RefreshCw size={16} className={refreshing ? 'portal-usage-spin' : undefined} aria-hidden="true" />刷新
        </button>
        <button type="button" className="portal-primary" disabled={exporting || loading} onClick={() => void exportCsv()}>
          <Download size={16} aria-hidden="true" />{exporting ? '正在导出…' : '导出 CSV'}
        </button>
      </div>
    </div>

    <p className="portal-usage-hint">金额按记录时的模型单价估算（CNY，非账单）；未计量请求单独计数，不按 0 计。时区：Asia/Shanghai。</p>
    {error && <p className="portal-alert" role="alert"><span>{error}</span><button type="button" className="portal-secondary" onClick={() => setReload((value) => value + 1)}>重试</button></p>}
    {exportError && <p className="portal-alert" role="alert">{exportError}</p>}
    {refreshing && <p className="portal-usage-note" role="status">正在刷新用量数据…</p>}

    {loading && !summary && <section className="portal-card"><div className="portal-card-heading"><div><h2>用量台账</h2><p>正在读取账号与项目用量…</p></div></div><p className="portal-usage-empty" role="status">正在读取用量台账…</p></section>}

    {summary && <>
      <div className="portal-stats portal-usage-stats">
        <article className="portal-stat"><div><span>总 Token</span><BarChart3 size={18} aria-hidden="true" /></div><strong>{formatCount(summary.totals.total_tokens)}</strong><small>输入 {formatCount(summary.totals.input_tokens)} · 输出 {formatCount(summary.totals.output_tokens)}</small></article>
        <article className="portal-stat"><div><span>AI 请求</span></div><strong>{formatCount(summary.totals.requests)}</strong><small>已计量 {formatCount(summary.totals.measured_requests)} · 未计量 {formatCount(summary.totals.unknown_requests)}</small></article>
        <article className={`portal-stat${hasCostWarning ? ' is-alert' : ''}`}><div><span>估算费用</span></div><strong>{formatMoney(summary.totals.estimated_cost)}</strong><small>{summary.totals.currency || 'CNY'} 估算 · 非账单</small></article>
        <article className="portal-stat"><div><span>活跃项目</span></div><strong>{formatCount(summary.totals.projects)}</strong><small>{formatCount(summary.totals.accounts)} 个账号</small></article>
        <article className="portal-stat"><div><span>扫描工作区</span></div><strong>{formatCount(summary.sources.workspaces_scanned)}</strong><small>有台账 {formatCount(summary.sources.workspaces_with_ledger)} 个</small></article>
      </div>
      {hasCostWarning && <p className="portal-notice" role="status">含 {formatCount(summary.totals.cost_unpriced_requests)} 条未设单价请求，金额未计入</p>}

      <section className="portal-card portal-usage-chart-card">
        <div className="portal-card-heading">
          <div><h2>Token 用量趋势</h2><p>{summary.range.from} 至 {summary.range.to} · {granularityNames[(summary.range.granularity as Granularity) || 'day'] || summary.range.granularity} · 时区 {summary.range.timezone}</p></div>
          <div className="portal-usage-legend" aria-label="图例"><span><i className="portal-usage-dot input" aria-hidden="true" />输入</span><span><i className="portal-usage-dot output" aria-hidden="true" />输出</span></div>
        </div>
        {hasUsage ? <div className="portal-usage-chart">
          <svg viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label="按日期堆叠的输入与输出 token 柱状图">
            {chart.ticks.map((tick) => <g key={tick.value}>
              <line className="portal-usage-grid" x1={chart.padLeft - 8} x2={chart.width - chart.padRight} y1={tick.y} y2={tick.y} />
              <text className="portal-usage-axis-label" x={chart.padLeft - 14} y={tick.y + 4} textAnchor="end">{formatCompact(tick.value)}</text>
            </g>)}
            {chart.bars.map((bar) => <g key={bar.bucket.date}>
              <title>{`${bar.bucket.date}：输入 ${formatCount(bar.bucket.input_tokens)}，输出 ${formatCount(bar.bucket.output_tokens)}，合计 ${formatCount(bar.total)}；请求 ${formatCount(bar.bucket.requests)}，估算 ${formatMoney(bar.bucket.estimated_cost)}`}</title>
              {bar.inputHeight > 0 && <rect className="portal-usage-bar-input" x={bar.x} y={chart.baseline - bar.inputHeight} width={chart.barWidth} height={bar.inputHeight} rx={2} />}
              {bar.outputHeight > 0 && <rect className="portal-usage-bar-output" x={bar.x} y={chart.baseline - bar.inputHeight - bar.outputHeight} width={chart.barWidth} height={bar.outputHeight} rx={2} />}
              {bar.total === 0 && <rect className="portal-usage-bar-zero" x={bar.x} y={chart.baseline - 2} width={chart.barWidth} height={2} rx={1} />}
              {bar.showLabel && <text className="portal-usage-axis-label" x={bar.x + chart.barWidth / 2} y={chart.baseline + 22} textAnchor="middle">{formatBucket(bar.bucket.date, summary.range.granularity)}</text>}
            </g>)}
          </svg>
        </div> : <p className="portal-usage-empty" role="status">该时间范围还没有用量记录</p>}
        <div className="portal-usage-chart-foot">
          <button type="button" className="portal-link-button" aria-expanded={tableOpen} onClick={() => setTableOpen((value) => !value)}>{tableOpen ? '收起数据表' : '查看数据表'}</button>
          <span>{summary.buckets.length} 个统计周期 · 生成于 {formatWhen(summary.sources.generated_at)}</span>
        </div>
        {tableOpen && <div className="portal-table-scroll portal-usage-table-wrap">
          <table className="portal-table portal-usage-table">
            <caption className="sr-only">按{summary.range.granularity === 'day' ? '日' : summary.range.granularity === 'week' ? '周' : '月'}的 token 用量数据</caption>
            <thead><tr><th scope="col">日期</th><th scope="col">请求</th><th scope="col">未计量</th><th scope="col">输入 token</th><th scope="col">输出 token</th><th scope="col">缓存 token</th><th scope="col">总 token</th><th scope="col">估算费用</th></tr></thead>
            <tbody>{summary.buckets.map((bucket) => <tr key={bucket.date}><td>{bucket.date}</td><td>{formatCount(bucket.requests)}</td><td>{formatCount(bucket.unknown_requests)}</td><td>{formatCount(bucket.input_tokens)}</td><td>{formatCount(bucket.output_tokens)}</td><td>{formatCount(bucket.cached_tokens)}</td><td>{formatCount(bucket.total_tokens)}</td><td>{formatMoney(bucket.estimated_cost)}</td></tr>)}</tbody>
          </table>
        </div>}
      </section>

      <div className="portal-usage-breakdown">
        <section className="portal-card">
          <div className="portal-card-heading"><div><h2>按功能</h2><p>批量点校、点校调试、研究工作与预处理等功能的用量</p></div></div>
          {featureRows.length ? <div className="portal-usage-bars">
            {featureRows.map((row) => <div className="portal-usage-bar-row" key={row.feature} title={`${featureLabel(row.feature)}：${formatCount(row.total_tokens)} token · ${formatCount(row.requests)} 次请求 · ${formatMoney(row.estimated_cost)}`}>
              <span className="portal-usage-bar-label">{featureLabel(row.feature)}</span>
              <span className="portal-usage-bar-track" aria-hidden="true"><span className="portal-usage-bar-fill" style={{ width: `${Math.max(2, (row.total_tokens / maxFeature) * 100)}%` }} /></span>
              <span className="portal-usage-bar-value">{formatCount(row.total_tokens)}</span>
            </div>)}
          </div> : <p className="portal-usage-empty">没有可归类的功能用量</p>}
        </section>
        <section className="portal-card">
          <div className="portal-card-heading"><div><h2>按模型</h2><p>按实际调用的模型汇总 token 与费用</p></div></div>
          {modelRows.length ? <div className="portal-usage-bars">
            {modelRows.map((row) => <div className="portal-usage-bar-row" key={`${row.model_id}-${row.model_name}-${row.provider_name}`} title={`${row.provider_name ? `${row.provider_name} · ` : ''}${row.model_name || row.model_id || '未标注模型'}：${formatCount(row.total_tokens)} token · ${formatCount(row.requests)} 次请求 · ${formatMoney(row.estimated_cost)}`}>
              <span className="portal-usage-bar-label" title={row.model_name || row.model_id}>{row.model_name || row.model_id || '未标注模型'}{row.provider_name ? <small>{row.provider_name}</small> : null}</span>
              <span className="portal-usage-bar-track" aria-hidden="true"><span className="portal-usage-bar-fill" style={{ width: `${Math.max(2, (row.total_tokens / maxModel) * 100)}%` }} /></span>
              <span className="portal-usage-bar-value">{formatCount(row.total_tokens)}</span>
            </div>)}
          </div> : <p className="portal-usage-empty">没有可归类的模型用量</p>}
        </section>
      </div>

      <section className="portal-card">
        <div className="portal-card-heading"><div><h2>账号用量</h2><p>按账号汇总；展开查看该账号各项目明细</p></div></div>
        {accounts.length ? <div className="portal-table-scroll">
          <table className="portal-table portal-usage-accounts">
            <thead><tr><th scope="col"><span className="sr-only">展开</span></th><th scope="col">账号</th><th scope="col">请求</th><th scope="col">总 token</th><th scope="col">估算费用</th><th scope="col">最近活动</th></tr></thead>
            <tbody>{accounts.map((row) => {
              const open = expandedAccounts.includes(row.account_id);
              const label = row.display_name || row.username || '未知账号';
              return <Fragment key={row.account_id}>
                <tr>
                  <td><button type="button" className="portal-usage-expand" aria-expanded={open} aria-label={`${open ? '收起' : '展开'}${label}的项目明细`} onClick={() => toggleAccount(row.account_id)}><ChevronRight size={16} className={open ? 'is-open' : undefined} aria-hidden="true" /></button></td>
                  <td><strong>{label}</strong><small className="portal-block">@{row.username || '未知'}</small></td>
                  <td>{formatCount(row.requests)}</td>
                  <td>{formatCount(row.total_tokens)}</td>
                  <td>{formatMoney(row.estimated_cost)}</td>
                  <td>{formatWhen(accountLastAt(row))}</td>
                </tr>
                {open && <tr className="portal-usage-subrow"><td /><td colSpan={5}>
                  {row.projects.length ? <table className="portal-table portal-usage-projects">
                    <thead><tr><th scope="col">项目</th><th scope="col">请求</th><th scope="col">总 token</th><th scope="col">估算费用</th><th scope="col">最近活动</th></tr></thead>
                    <tbody>{row.projects.map((project) => <tr key={project.project_id || project.project_name}><td>{project.project_name || '未命名项目'}</td><td>{formatCount(project.requests)}</td><td>{formatCount(project.total_tokens)}</td><td>{formatMoney(project.estimated_cost)}</td><td>{formatWhen(project.last_at)}</td></tr>)}</tbody>
                  </table> : <p className="portal-usage-empty">该账号在此时间范围内没有项目用量</p>}
                </td></tr>}
              </Fragment>;
            })}</tbody>
          </table>
        </div> : <p className="portal-usage-empty">该时间范围还没有账号用量</p>}
      </section>

      <section className="portal-card">
        <div className="portal-card-heading"><div><h2>项目用量</h2><p>按项目平铺对比；未计量请求单独列出</p></div></div>
        {projectRows.length ? <div className="portal-table-scroll">
          <table className="portal-table portal-usage-projects-table">
            <thead><tr><th scope="col">账号</th><th scope="col">项目</th><th scope="col">请求</th><th scope="col">未计量</th><th scope="col">总 token</th><th scope="col">估算费用</th><th scope="col">最近活动</th></tr></thead>
            <tbody>{projectRows.map((row) => <tr key={`${row.account_id}-${row.project_id || row.project_name}`}>
              <td>{row.display_name || row.username || '未知账号'}<small className="portal-block">@{row.username || '未知'}</small></td>
              <td>{row.project_name || '未命名项目'}</td>
              <td>{formatCount(row.requests)}</td>
              <td>{formatCount(row.unknown_requests)}</td>
              <td>{formatCount(row.total_tokens)}</td>
              <td>{formatMoney(row.estimated_cost)}</td>
              <td>{formatWhen(row.last_at)}</td>
            </tr>)}</tbody>
          </table>
        </div> : <p className="portal-usage-empty">该时间范围还没有项目用量记录</p>}
      </section>
    </>}
  </section>;
}
