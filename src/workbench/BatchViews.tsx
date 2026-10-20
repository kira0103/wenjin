import { DeleteBatchButton } from './DeleteDialogs';
import { useEffect, useRef, useState } from 'react';
import { Download, LoaderCircle, Pause, Play, RotateCcw, Square } from 'lucide-react';
import type { Material, Project } from './api';
import { prepApi, type PrepRun } from './preprocessApi';
import { batchApi, batchNames, fileNames, type Batch, type BatchChoice, type BatchFile, type BatchEvent, type BatchResult, type BatchAttempt } from './batchApi';
import { Modal } from './Pages';
import { PunctuationComparison } from './VolumeCompare';
import { nextBatchIds } from './batchSelection';
const active = (b: Batch) => ['queued', 'running', 'pausing', 'stopping'].includes(b.status);
const quantity = (value: number | null) => value === null ? '未报告' : value.toLocaleString();
const duration = (seconds: number | null) => seconds === null ? '计算中' : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;

export function BatchPage({ project, materials, initialRun, onOpen, onPrepare, onSettings, onDebug }: { onDebug(attempt?: string): void; project: Project; materials: Material[]; initialRun?: string; onOpen(id: string): void; onPrepare(): void; onSettings(): void }) {
  const [runs, setRuns] = useState<PrepRun[]>([]), [runId, setRunId] = useState(initialRun || '');
  const [choices, setChoices] = useState<BatchChoice[]>([]), [selected, setSelected] = useState<string[]>([]), [scopeLoading, setScopeLoading] = useState(true);
  const [history, setHistory] = useState<Batch[]>([]), [batch, setBatch] = useState<Batch | null>(null);
  const [files, setFiles] = useState<BatchFile[]>([]), [fileTotal, setFileTotal] = useState(0), [offset, setOffset] = useState(0), [filter, setFilter] = useState('');
  const [events, setEvents] = useState<BatchEvent[]>([]), [busy, setBusy] = useState(''), [error, setError] = useState('');
  const [result, setResult] = useState<BatchResult | null>(null), [attemptFile, setAttemptFile] = useState<BatchFile | null>(null), [attempts, setAttempts] = useState<BatchAttempt[]>([]), [attemptOffset, setAttemptOffset] = useState(0), [attemptTotal, setAttemptTotal] = useState(0);
  const [setup, setSetup] = useState(false), [batchSize, setBatchSize] = useState('20');
  const alive = useRef(true), request = useRef({ signature: '', id: '' });
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([prepApi.runs(project.id, ''), batchApi.list(project.id)]).then(([r, h]) => {
      if (cancelled) return;
      const ready = r.filter(v => v.status === 'accepted'); setRuns(ready); setHistory(h);
      setRunId(id => ready.some(v => v.id === id) ? id : ready[0]?.id || '');
      let saved = ''; try { saved = localStorage.getItem(`shixue-batch-${project.id}`) || ''; } catch { /* Use latest. */ }
      const current = h.find(v => v.id === saved) || h[0];
      setBatch(current || null); setSetup(!current || !!initialRun && current.run_id !== initialRun);
      if (!ready.length) setScopeLoading(false);
    }).catch(e => { if (!cancelled) { setError(e.message); setScopeLoading(false); } });
    return () => { cancelled = true; };
  }, [project.id]);
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setScopeLoading(true);
    void batchApi.scope(project.id, runId).then(values => { if (!cancelled) { setChoices(values); setSelected(values.filter(v => !v.processed && !v.manual_modified).slice(0, /^[1-9]\d*$/.test(batchSize) ? Math.min(5000, Number(batchSize)) : 20).map(v => v.id)); } }).catch(e => { if (!cancelled) setError(e.message); }).finally(() => { if (!cancelled) setScopeLoading(false); });
    return () => { cancelled = true; };
  }, [project.id, runId, setup]);
  useEffect(() => {
    if (!batch) return;
    try { localStorage.setItem(`shixue-batch-${project.id}`, batch.id); } catch { /* Preserve in memory. */ }
  }, [batch?.id, project.id]);
  useEffect(() => {
    if (!batch) return;
    let cancelled = false, timer: number | undefined;
    const poll = async () => {
      try {
        const [value, rows, logs] = await Promise.all([batchApi.get(batch.id), batchApi.files(batch.id, offset, filter), batchApi.events(batch.id)]);
        if (cancelled) return;
        setBatch(value); setFiles(rows.items); setFileTotal(rows.total); setEvents(logs);
        setHistory(h => [value, ...h.filter(v => v.id !== value.id)]);
        timer = window.setTimeout(() => void poll(), active(value) ? 1200 : 4200);
      } catch (e) { if (!cancelled) { setError((e as Error).message); timer = window.setTimeout(() => void poll(), 3000); } }
    };
    void poll(); return () => { cancelled = true; window.clearTimeout(timer); };
  }, [batch?.id, offset, filter]);
  useEffect(() => {
    if (!attemptFile) return;
    let cancelled = false;
    void batchApi.attempts(attemptFile.id, attemptOffset).then(r => { if (!cancelled) { setAttempts(r.items); setAttemptTotal(r.total); } }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [attemptFile?.id, attemptOffset]);
  const act = async (label: string, work: () => Promise<void>) => {
    if (busy) return;
    setBusy(label); setError('');
    try { await work(); } catch (e) { if (alive.current) setError((e as Error).message); } finally { if (alive.current) setBusy(''); }
  };
  const control = (action: 'pause' | 'resume' | 'stop' | 'retry') => void act('更新执行状态', async () => { if (!batch) return; const value = await batchApi.action(batch.id, action); if (alive.current) setBatch(value); });
  const start = () => void act('创建批次', async () => {
    const signature = JSON.stringify([runId, [...selected].sort()]);
    if (request.current.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    const value = await batchApi.create(project.id, runId, selected, request.current.id);
    if (alive.current) { setBatch(value); setHistory(h => [value, ...h.filter(v => v.id !== value.id)]); setOffset(0); setFilter(''); setSetup(false); request.current = { signature: '', id: '' }; }
  });
  return <div className="batch-page">
    {error && <p className="persistent-error" role="alert">{error}<button className="button-quiet" onClick={onSettings}>模型与设置</button><button className="button-quiet" onClick={() => setError('')}>关闭提示</button></p>}
    <div className="welcome-row"><div><h2>批量点校</h2><p>逐文件执行，长卷自动分段并合并为完整卷稿。机器完成后统一人工复核。</p></div><button className="button-secondary" disabled={!!busy} onClick={() => setSetup(v => !v)}>{setup ? '收起文件选择' : '选择文件并开始新批次'}</button></div>
    {setup && <section className="surface prep-section batch-setup"><div className="row-between"><h2>选择拆分文件</h2><button className="button-primary" disabled={!!busy || scopeLoading || !selected.length || nextBatchIds(choices, batchSize) === null || !!batch && active(batch) && batch.run_id === runId} onClick={start}>{busy === '创建批次' && <LoaderCircle size={15} className="spin" />}开始批量点校（{selected.length} 份）</button></div>
      {runs.length ? <><label className="form-label">底本拆分结果<select aria-label="批量底本" disabled={!!busy} value={runId} onChange={e => setRunId(e.target.value)}>{runs.map(r => <option key={r.id} value={r.id}>{materials.find(m => m.id === r.material_id)?.filename || '历史底本'} · {r.summary.text_count} 份文本</option>)}</select></label><p className="scope-note">默认选择未点校文件，卷首材料也可处理。已有人工作或已有结果的文件可选中重跑，结果另存候选。模型沿用“模型与设置”；每次只执行一个文件。</p>
      <div className="batch-size-row"><label className="form-label">本次选取多少份<input aria-label="本次处理数量" type="number" disabled={!!busy || scopeLoading} min={1} max={5000} value={batchSize} onChange={e => { const value = e.target.value; setBatchSize(value); const ids = nextBatchIds(choices, value); if (ids && !scopeLoading) setSelected(ids); }} /></label><button className="button-secondary" disabled={!!busy || scopeLoading || !/^[1-9]\d*$/.test(batchSize) || Number(batchSize) > 5000} onClick={() => setSelected(choices.filter(v => !v.processed && !v.manual_modified).slice(0, Number(batchSize)).map(v => v.id))}>按数量选取下一批</button></div><p className="scope-note">按原文顺序选取未点校文件，跳过已有结果与已有修改稿；可再手动调整。继续原批次请使用下方“继续”，它会复用已保存文件和片段。</p>
      <label className="prep-check"><input type="checkbox" aria-label="全选拆分文件" disabled={!!busy || scopeLoading} checked={!!choices.length && selected.length === choices.length} onChange={e => setSelected(e.target.checked ? choices.map(v => v.id) : [])} />全选 {selected.length} / {choices.length} 份文件</label>
      <div className="batch-choice-list">{scopeLoading ? <p>正在读取文件范围…</p> : choices.map(choice => <label className="batch-choice" key={choice.id}><input type="checkbox" aria-label={`批量选择 ${choice.title}`} disabled={!!busy} checked={selected.includes(choice.id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, choice.id] : ids.filter(id => id !== choice.id))} /><strong>{choice.title}</strong><span>{choice.chars.toLocaleString()} 字</span><small>{choice.processed ? '已有机器结果' : choice.manual_modified ? '已有稿件，保留原稿' : '未点校'}</small></label>)}</div></> : <div className="empty-block"><strong>先准备卷文本</strong><span>完成固定拆分清洗后即可批量点校。</span><button className="button-primary" onClick={onPrepare}>准备卷文本</button></div>}</section>}
    {!!history.length && <label className="form-label batch-history">执行批次<select aria-label="执行批次" value={batch?.id || ''} disabled={!!busy} onChange={e => { setBatch(history.find(b => b.id === e.target.value) || null); setOffset(0); setFilter(''); }}>
      {history.map(b => <option key={b.id} value={b.id}>{new Date(b.created_at).toLocaleString('zh-CN')} · {b.total} 份 · {batchNames[b.status]}</option>)}</select></label>}
    {batch && <><section className="surface prep-section batch-monitor" aria-label="批次监控"><div className="row-between"><div><h2>{batchNames[batch.status]}</h2><p className="scope-note">{batch.model.provider_name} · {batch.model.model} · {new Date(batch.created_at).toLocaleString('zh-CN')}</p></div><div className="action-row"><DeleteBatchButton batch={batch} disabled={!!busy} onDeleted={id => { const next = history.find(b => b.id !== id) || null; setHistory(h => h.filter(b => b.id !== id)); setBatch(next); setFiles([]); setEvents([]); setResult(null); setAttemptFile(null); setError(''); setSetup(!next); localStorage.removeItem(`shixue-batch-${project.id}`); }} />
      <button className="button-secondary" disabled={!!busy || !['queued', 'running'].includes(batch.status)} onClick={() => control('pause')}><Pause size={14} />暂停</button><button className="button-secondary" disabled={!!busy || !['paused', 'interrupted'].includes(batch.status)} onClick={() => control('resume')}><Play size={14} />继续</button><button className="button-secondary" disabled={!!busy || !['queued', 'running', 'pausing', 'paused', 'interrupted'].includes(batch.status)} onClick={() => control('stop')}><Square size={14} />停止</button><button className="button-secondary" disabled={!!busy || !batch.counts.failed || active(batch)} onClick={() => control('retry')}><RotateCcw size={14} />重试失败文件</button>
    </div></div>{batch.error && <p className="persistent-error">{batch.error}</p>}{batch.status === 'stopping' && <p className="prep-notice">正在取消在途请求；已完成片段保留，取消后未返回的服务端用量记为未知。</p>}
    <div className="batch-stats"><div><span>本批处理进度</span><strong>{batch.ended} / {batch.total}</strong><small>成功 {batch.counts.done} · 失败 {batch.counts.failed} · 待执行 {batch.counts.queued} · 已停止 {batch.counts.stopped}</small></div><div><span>当前文件</span><strong>{batch.current?.title || '—'}</strong><small>{batch.current ? `已验证 ${batch.current.completed_chunks || 0} / ${batch.current.chunks} 片段 · ${batch.current.stage} · ${batch.current.waiting_seconds} 秒` : '暂无执行文件'} · 在途请求 {batch.usage.in_flight}</small></div><div><span>输入 / 输出 token（已报告）</span><strong>{quantity(batch.usage.input_tokens)} / {quantity(batch.usage.output_tokens)}</strong><small>已报告总计 {quantity(batch.usage.total_tokens)} · 未完整计量 {batch.usage.unknown_requests} 次 · 请求 {batch.usage.requests} 次</small></div><div><span>人工复核</span><strong>{batch.pending_review} 份待复核</strong><small>需合并 {batch.needs_merge} · 已审定 {batch.approved} · 复用片段 {batch.reused_chunks}</small></div></div>
    <progress className="batch-progress" value={batch.ended} max={batch.total || 1} /><p className="scope-note">执行耗时 {duration(batch.elapsed_seconds)} · 预计剩余 {duration(batch.estimated_remaining_seconds)} · {batch.estimated_cost === null ? batch.cost_note : `参考费用 ${batch.estimated_cost} ${batch.model.currency}（单价日期 ${batch.model.price_date}）；${batch.cost_note}`}。已结束文件包含失败，机器成功仍需人工审定。用量包含重试和验证失败；未报告项不视为零。</p>
    </section><section className="surface prep-section"><div className="row-between"><h2>文件执行列表</h2><select aria-label="文件状态筛选" value={filter} onChange={e => { setFilter(e.target.value); setOffset(0); }}><option value="">全部文件</option>{Object.entries(fileNames).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div>
      <div className="batch-table-scroll"><table className="batch-table"><thead><tr><th>顺序 / 文件</th><th>状态</th><th>片段进度</th><th>输入 / 输出 token</th><th>耗时</th><th>结果与明细</th></tr></thead><tbody>{files.map(file => <tr key={file.id}><td>{file.seq} · {file.title}<small>{file.chars.toLocaleString()} 字底文</small></td><td>{fileNames[file.status]}{file.has_text_changes && <small className="diff-warning-badge">文字差异待复核</small>}{file.merge_status === 'candidate' && <small>候选待合并</small>}{file.status === 'done' && file.merge_status === 'applied' && <small>已保存待复核稿</small>}{file.error && <small className="inline-error">{file.error}</small>}</td><td>{file.completed_chunks || 0} / {file.chunks}<small>{(file.completed_chars || 0).toLocaleString()} 字已验证</small></td><td>{quantity(file.input_tokens)} / {quantity(file.output_tokens)}<small>{file.requests} 次调用</small></td><td>{file.latency_ms === null ? '—' : `${(file.latency_ms / 1000).toFixed(1)} 秒`}</td><td><div className="batch-row-actions">{file.status === 'done' && <button className="button-quiet" onClick={() => file.merge_status === 'candidate' ? void act('读取候选稿', async () => { const value = await batchApi.result(file.id); if (alive.current) setResult(value); }) : onOpen(file.unit_id)}>{file.merge_status === 'candidate' ? '查看候选' : '打开本卷复核'}</button>}<button className="button-quiet" onClick={() => { setAttempts([]); setAttemptOffset(0); setAttemptFile(file); }}>调用明细</button></div></td></tr>)}</tbody></table></div>
      <Pager offset={offset} total={fileTotal} onChange={setOffset} />
      <div className="action-row"><button className="button-secondary" disabled={!!busy} onClick={() => void act('导出批次结果', async () => { const bytes = await batchApi.export(batch.id); if (alive.current) await window.desktop.saveFile({ filename: '批量点校-' + batch.id + '.zip', data: bytes }); })}><Download size={14} />导出结果 ZIP</button><button className="button-secondary" disabled={!!busy} onClick={() => void act('导出执行记录', async () => { const file = await batchApi.exportLog(batch.id); if (alive.current) await window.desktop.saveFile({ filename: file.filename, data: new TextEncoder().encode(file.content).buffer }); })}>导出日志与用量</button></div><p className="scope-note">成功稿、候选待合并稿和不完整片段分目录导出，失败与未执行文件在清单中明确列出。</p>
    </section><section className="surface prep-section"><h2>最近执行事件</h2><div className="batch-events">{events.map(event => <div key={event.id} className={event.level === 'error' ? 'inline-error' : ''}><time>{new Date(event.created_at).toLocaleTimeString('zh-CN')}</time><span>{event.message}</span></div>)}</div></section></>}
    {result && <Modal title={result.title + ' · 候选待合并'} onClose={() => setResult(null)}><p className="scope-note">当前稿件已保留。请对照候选内容手工合并，不会自动覆盖已有人工修改。下方当前稿件为打开时的快照，合并前可打开最新稿件。</p><PunctuationComparison baseline={result.baseline} content={result.content} /><details><summary>当前人工稿 · 版本 {result.current_revision}</summary><pre>{result.current_content}</pre></details>{result.questions.map((q,i) => <p key={i}>{q.location} · {q.quote} · {q.reason} · {q.suggestion}</p>)}<div className="modal-actions"><button className="button-secondary" onClick={() => void act('导出候选稿', async () => { await window.desktop.saveFile({ filename: result.title + '-候选.txt', data: new TextEncoder().encode(result.content).buffer }); })}>导出候选稿</button><button className="button-primary" onClick={() => { const id = result.unit_id; setResult(null); onOpen(id); }}>打开当前稿件合并</button></div></Modal>}
    {attemptFile && <Modal title={attemptFile.title + ' · 请求明细'} onClose={() => setAttemptFile(null)}><p className="scope-note">每次尝试单独记录，包括失败与重试；缓存复用没有新增请求。</p>{attempts.map(a => <article className="batch-attempt" key={a.id}><strong>{a.reviewed_from_history ? '已复用，文字差异待复核' : ({ error: '请求失败', requesting: '等待模型返回', review: '含文字差异，待复核', valid: '校验通过', invalid: '校验失败', received: '响应已收到', cancelled: '本地已取消，用量未报告', unknown: '响应未知', not_sent: '未发送' } as Record<string, string>)[a.status] || a.status} · {new Date(a.started_at).toLocaleString('zh-CN')}</strong><p>输入 {quantity(a.input_tokens)} · 输出 {quantity(a.output_tokens)} · 总计 {quantity(a.total_tokens)} · {(a.latency_ms / 1000).toFixed(1)} 秒</p><small>请求 {a.id} · 响应 {a.response_id || '未返回标识'} · 结束原因 {a.finish_reason || '未返回'}</small>{a.error && <p className={a.reviewed_from_history ? 'scope-note' : 'inline-error'}>{a.reviewed_from_history ? '历史校验记录：' : ''}{a.error}</p>}<button className="button-secondary" onClick={() => { setAttemptFile(null); onDebug(a.id); }}>调试此请求</button><details><summary>服务返回的原始用量</summary><pre>{JSON.stringify(a.usage, null, 2)}</pre></details>{a.request != null && <details><summary>实际请求与校验反馈</summary><pre>{JSON.stringify(a.request, null, 2)}</pre></details>}</article>)}<Pager offset={attemptOffset} total={attemptTotal} onChange={setAttemptOffset} /></Modal>}
  </div>;
}
function Pager({ offset, total, onChange }: { offset: number; total: number; onChange(n: number): void }) {
  return <div className="prep-pagination batch-pager"><button className="button-quiet" disabled={!offset} onClick={() => onChange(Math.max(0, offset - 80))}>上一页</button><span>{total ? `${offset + 1}–${Math.min(total, offset + 80)}` : '0'} / {total}</span><button className="button-quiet" disabled={offset + 80 >= total} onClick={() => onChange(offset + 80)}>下一页</button></div>;
}
export function BatchHistory({ project, onOpen }: { project: Project; onOpen(id: string): void }) {
  const [rows, setRows] = useState<Batch[]>([]), [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false, timer: number | undefined;
    const load = async () => { try { const list = await batchApi.list(project.id); if (!cancelled) { setRows(list); setError(''); } } catch (e) { if (!cancelled) setError((e as Error).message); } finally { if (!cancelled) timer = window.setTimeout(() => void load(), 4200); } };
    void load(); return () => { cancelled = true; window.clearTimeout(timer); };
  }, [project.id]);
  if (!rows.length && !error) return null;
  return <section className="page-container batch-history-summary"><h2>批量点校记录</h2>{error && <p className="persistent-error">{error}</p>}{rows.map(batch => <div className="unit-row batch-history-row" key={batch.id}><button className="button-quiet batch-history-open" onClick={() => onOpen(batch.id)}><div><strong>{new Date(batch.created_at).toLocaleString('zh-CN')} · {batch.total} 份文件</strong><small>{batch.model.model} · {batch.ended} 份已结束 · 已报告 {quantity(batch.usage.total_tokens)} token · 未计量 {batch.usage.unknown_requests} 次</small></div><span>{batchNames[batch.status]}</span></button><DeleteBatchButton batch={batch} onDeleted={id => setRows(values => values.filter(b => b.id !== id))} /></div>)}</section>;
}
