import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, FlaskConical, LoaderCircle, Play, Save } from 'lucide-react';
import type { Material, Project } from './api';
import { prepApi, type PrepNode, type PrepRun } from './preprocessApi';
import { debugApi, debugStatus, type DebugConfig, type DebugInput, type DebugRun, type DebugSeed, type DebugRecord } from './debugApi';
import { PunctuationComparison } from './VolumeCompare';
import { debugSample } from './debugSample';
import { debugRetryInput } from './debugRetry';
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const quantity = (n: number | null | undefined) => n == null ? '未报告' : n.toLocaleString();

export function CollationDebug({ project, materials, attemptId, onBack, onSettings }: { project: Project; materials: Material[]; attemptId?: string; onBack(): void; onSettings(): void }) {
  const key = `shixue-debug-${project.id}`;
  const [saved, setSaved] = useState<DebugConfig | null>(null), [draft, setDraft] = useState<DebugInput | null>(null);
  const [seed, setSeed] = useState<DebugSeed | null>(null), [runs, setRuns] = useState<PrepRun[]>([]), [sourceRun, setSourceRun] = useState(''), [texts, setTexts] = useState<PrepNode[]>([]), [textId, setTextId] = useState(''), [textTotal, setTextTotal] = useState(0), [textOffset, setTextOffset] = useState(0), [query, setQuery] = useState('');
  const [history, setHistory] = useState<DebugRecord[]>([]), [result, setResult] = useState<DebugRun | null>(null), [preview, setPreview] = useState<unknown>(null), [previewError, setPreviewError] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [notice, setNotice] = useState('');
  const requestDraft = useMemo(() => draft ? debugRetryInput(draft, result) : null, [draft, result]);
  const alive = useRef(true), pending = useRef({ signature: '', id: '' }), selection = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ++selection.current; }; }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([debugApi.config(project.id), prepApi.runs(project.id, ''), debugApi.history(project.id), attemptId ? debugApi.seed(project.id, attemptId) : Promise.resolve(null)]).then(([config, sources, records, original]) => {
      if (cancelled) return;
      setSaved(config); setRuns(sources.filter(r => r.status === 'accepted')); setHistory(records);
      let remembered: { input?: DebugInput; result?: string; origin?: string } = {};
      try { remembered = JSON.parse(localStorage.getItem(key) || '{}'); } catch { /* New draft. */ }
      const reuse = !!remembered.input && (!attemptId || remembered.origin === attemptId);
      if (original) {
        setSeed(original); setDraft(reuse ? remembered.input! : { config: original.config, input_text: original.input_text, title: original.title, lead: original.lead, attempt_id: attemptId, validation_feedback: original.validation_feedback });
      } else {
        setDraft(remembered.input || { config: config.config, input_text: '', title: '手动样本', lead: '' });
        if (remembered.origin) void debugApi.seed(project.id, remembered.origin).then(value => { if (!cancelled) setSeed(value); }).catch(() => { /* Source is unavailable, sample remains. */ });
      }
      if (reuse && remembered.result) void debugApi.get(project.id, remembered.result).then(value => { if (!cancelled) setResult(value); }).catch(() => { /* Removed historical draft. */ });
    }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [project.id, attemptId]);
  useEffect(() => { if (draft) { try { localStorage.setItem(key, JSON.stringify({ input: draft, result: result?.id, origin: seed?.attempt_id })); } catch { /* Keep current state. */ } } }, [draft, result?.id, seed?.attempt_id, key]);
  useEffect(() => {
    if (!sourceRun) return;
    let cancelled = false;
    void prepApi.nodes(sourceRun, query, textOffset).then(r => { if (!cancelled) { setTexts(r.items); setTextTotal(r.total); } }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [sourceRun, query, textOffset]);
  useEffect(() => {
    if (!draft?.input_text.trim() || !draft.config.system_prompt.trim()) { setPreview(null); setPreviewError(''); return; }
    let cancelled = false;
    setPreview(null);
    const timer = window.setTimeout(() => { void debugApi.preview(project.id, requestDraft!).then(p => { if (!cancelled) { setPreview(p.request); setPreviewError(''); } }).catch(e => { if (!cancelled) setPreviewError(e.message); }); }, 400);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [project.id, requestDraft]);
  useEffect(() => {
    if (!result || result.status !== 'running') return;
    let cancelled = false, timer: number | undefined;
    const poll = async () => {
      try {
        const value = await debugApi.get(project.id, result.id);
        if (cancelled) return;
        setResult(value);
        if (value.status === 'running') timer = window.setTimeout(() => void poll(), 1000);
        else { const records = await debugApi.history(project.id); if (!cancelled) setHistory(records); }
      } catch (e) { if (!cancelled) { setError((e as Error).message); timer = window.setTimeout(() => void poll(), 2000); } }
    };
    void poll(); return () => { cancelled = true; window.clearTimeout(timer); };
  }, [result?.id, result?.status, project.id]);
  const act = async (name: string, work: () => Promise<void>) => { if (busy) return; setBusy(name); setError(''); setNotice(''); try { await work(); } catch (e) { if (alive.current) setError((e as Error).message); } finally { if (alive.current) setBusy(''); } };
  const choose = async (id: string) => {
    const version = ++selection.current; setTextId(id); if (!id) return;
    await act('读取样本', async () => { const text = await prepApi.text(sourceRun, id); if (!alive.current || version !== selection.current) return; const title = texts.find(t => t.id === id)?.title || '本卷样本'; const sample = debugSample(text.formatted); setDraft(d => d ? { ...d, input_text: sample.text, title: title + ' · 卷首样本', lead: '', attempt_id: undefined, validation_feedback: undefined } : d); setNotice('载入本卷卷首 ' + Array.from(sample.text).length + ' 字；' + (sample.truncated ? '仅为样本，尚有后文；' : '') + (!sample.completeLine ? '当前行超过取样预算，末尾未到行尾，可手动粘贴完整行；' : '取样已保留完整行；') + '调试不处理整卷。'); });
  };
  const start = () => void act('请求模型', async () => {
    if (!draft) return;
    const signature = JSON.stringify(requestDraft);
    if (pending.current.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    const value = await debugApi.run(project.id, requestDraft!, pending.current.id);
    if (alive.current) { setResult(value); pending.current = { signature: '', id: '' }; setHistory(await debugApi.history(project.id)); }
  });
  if (!saved || !draft) return <div className="page-container">{error ? <p className="persistent-error">{error}</p> : <p>正在读取点校调试…</p>}<button className="button-secondary" onClick={onBack}>返回古籍点校</button></div>;
  const running = result?.status === 'running', locked = !!busy || running;
  const canSave = !!result?.passed && same(result.config, draft.config) && result.model.base_url === saved.model.base_url && result.model.model === saved.model.model;
  const model = draft.attempt_id && seed ? seed.model : saved.model;
  return <div className="page-container collation-debug">
    <button className="button-quiet back-link" onClick={onBack}><ArrowLeft size={16} />返回古籍点校</button>
    <div className="welcome-row"><div><h1><FlaskConical size={24} />点校调试</h1><p>先用一段样本文字验证提示词和请求参数，再用于批量点校。</p></div><button className="button-secondary" onClick={onSettings}>模型与设置</button></div>
    <p className="debug-model">{model.provider_name} · {model.model || '未配置模型'} · {model.base_url || '请先配置连接'}<span>项目方案版本 {saved.revision}</span></p>
    <p className="scope-note">每次测试只请求一次，不自动重试，不写入点校稿。测试会调用已配置模型并产生对应费用；有活动批次时请先暂停并等待当前请求返回。</p>
    {error && <p className="persistent-error" role="alert">{error}</p>}{notice && <p className="connection-success" role="status">{notice}</p>}
    {!!history.length && <label className="form-label">调试记录<select aria-label="调试记录" value={result?.id || ''} disabled={locked} onChange={e => { if (e.target.value) void act('读取记录', async () => { const r = await debugApi.get(project.id, e.target.value); if (alive.current) setResult(r); }); }}><option value="">选择历史记录</option>{history.map(h => <option key={h.id} value={h.id}>{new Date(h.created_at).toLocaleString('zh-CN')} · {h.title} · {debugStatus[h.status] || h.status}</option>)}</select></label>}
    {seed && <details className="surface debug-original"><summary>原批量请求：{seed.original_finish_reason || '未返回结束原因'} · 查看原输入与响应</summary><p className="inline-error">{seed.original_error}</p><h3>原请求</h3><pre>{JSON.stringify(seed.original_request, null, 2)}</pre><h3>原响应与用量</h3><pre>{JSON.stringify({ reply: seed.original_reply, usage: seed.original_usage }, null, 2)}</pre>{draft.attempt_id && <button className="button-secondary" disabled={locked} onClick={() => setDraft({ ...draft, attempt_id: undefined })}>改用当前配置的模型</button>}</details>}
    <div className="debug-input-grid">
      <section className="surface prep-section"><div className="row-between"><h2>点校提示词</h2><button className="button-quiet" disabled={locked} onClick={() => setDraft({ ...draft, config: saved.config })}>恢复项目方案</button><button className="button-secondary" disabled={locked} onClick={() => { setDraft({ ...draft, config: { ...draft.config, system_prompt: saved.recommended_prompt } }); setNotice("已载入实际断句的推荐提示词，请先调试再保存；当前项目方案尚未改动。"); }}>载入推荐提示词</button></div><label className="form-label">给模型的点校要求<textarea aria-label="点校提示词" rows={12} maxLength={12000} disabled={locked} value={draft.config.system_prompt} onChange={e => setDraft({ ...draft, config: { ...draft.config, system_prompt: e.target.value } })} /></label><details><summary>程序固定的输出约定</summary><p className="scope-note">{saved.contract}</p></details><div className="form-grid debug-parameters"><label className="form-label">思考模式<select aria-label="调试思考模式" disabled={locked} value={draft.config.thinking} onChange={e => setDraft({ ...draft, config: { ...draft.config, thinking: e.target.value as DebugInput['config']['thinking'] } })}><option value="default">沿用模型默认</option><option value="disabled">关闭思考</option><option value="enabled">开启思考</option></select></label></div><p className="scope-note">{draft.config.max_tokens == null ? '当前输出上限沿用模型服务，不发送 max_tokens。' : `当前自定义输出上限为 ${draft.config.max_tokens.toLocaleString()} token；可在高级请求参数中清空，改为沿用服务默认。`}需要自定义时展开高级请求参数。服务默认值随模型和思考模式变化，推理与正文共用输出额度。思考开关使用 thinking.type 参数，适用于支持此参数的接口；不支持的服务请保留默认。仅修改提示词不能关闭模型思考。</p><details><summary>高级请求参数</summary><label className="form-label">自定义输出 token 上限（可选）<input aria-label="调试输出上限" type="number" min={256} max={393216} placeholder="留空，沿用模型服务默认值" disabled={locked} value={draft.config.max_tokens ?? ''} onChange={e => setDraft({ ...draft, config: { ...draft.config, max_tokens: e.target.value === '' ? null : Number(e.target.value) } })} /></label><button className="button-quiet" disabled={locked || draft.config.max_tokens == null} onClick={() => setDraft({ ...draft, config: { ...draft.config, max_tokens: null } })}>沿用服务默认上限</button><label className="form-label">温度<input aria-label="调试温度" type="number" min={0} max={2} step={0.1} disabled={locked} value={draft.config.temperature} onChange={e => setDraft({ ...draft, config: { ...draft.config, temperature: Number(e.target.value) } })} /></label></details></section>
      <section className="surface prep-section"><h2>样本文字</h2><div className="form-grid"><label className="form-label">从拆分结果取样<select aria-label="调试底本" disabled={locked} value={sourceRun} onChange={e => { ++selection.current; setSourceRun(e.target.value); setTextId(''); setTexts([]); setTextOffset(0); }}><option value="">手动粘贴样本</option>{runs.map(r => <option key={r.id} value={r.id}>{materials.find(m => m.id === r.material_id)?.filename || '底本'} · {r.summary.text_count} 份</option>)}</select></label><label className="form-label">卷文本<select aria-label="调试卷文本" disabled={locked || !sourceRun} value={textId} onChange={e => void choose(e.target.value)}><option value="">选择当前页的卷</option>{texts.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label></div>{sourceRun && <div className="debug-source-search"><input aria-label="查找调试卷" value={query} disabled={locked} placeholder="按卷名查找" onChange={e => { setQuery(e.target.value); setTextOffset(0); }} /><button className="button-quiet" disabled={locked || !textOffset} onClick={() => setTextOffset(Math.max(0, textOffset - 80))}>上一页</button><span>{textTotal} 份</span><button className="button-quiet" disabled={locked || textOffset + 80 >= textTotal} onClick={() => setTextOffset(textOffset + 80)}>下一页</button></div>}<label className="form-label">{draft.title}<textarea aria-label="点校调试样本" rows={12} maxLength={8000} disabled={locked} value={draft.input_text} onChange={e => { ++selection.current; setDraft({ ...draft, input_text: e.target.value, title: '手动编辑样本', validation_feedback: undefined }); }} /></label><p className="scope-note">当前 {Array.from(draft.input_text).length.toLocaleString()} 字，最多 8,000 字。默认取卷首约 800 字，优先停在完整行；样本与正式底文、稿件分别保存。</p>{draft.lead && <details><summary>只读前文上下文</summary><pre>{draft.lead}</pre></details>}</section>
    </div>
    <p className="scope-note">片段首尾空白由程序保存并恢复；私用区字在模型请求中用不可变占位符保护，输出时恢复原字，正文内部的改字、增删和重排会高亮供人工核对。</p><details className="surface debug-request"><summary>查看本次实际请求（不含密钥）</summary>{previewError ? <p className="inline-error">{previewError}</p> : <pre>{preview ? JSON.stringify(preview, null, 2) : '输入样本后显示请求预览；预览不会调用模型。'}</pre>}</details>
    {requestDraft?.validation_feedback && <p className="prep-notice">再次运行会携带具体原字错误反馈，要求模型保留底本用字。仍只发送一次请求，不自动重复调用。</p>}
    <div className="debug-actions"><button className="button-primary" disabled={locked || !preview || !!previewError} onClick={start}>{locked ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}{running ? '模型处理中…' : '运行一次调试'}</button><button className="button-secondary" disabled={locked || !canSave} onClick={() => void act('保存方案', async () => { if (!result) return; const value = await debugApi.save(project.id, draft.config, saved.revision, result.id); if (alive.current) { setSaved(value); setNotice('已保存项目方案，后续新批次使用此提示词与参数。已有批次保留原方案。'); } })}><Save size={16} />保存并用于后续批次</button><p className="scope-note">返回格式有效且有实际处理才能应用；文字差异高亮后交人工复核；原样返回时请调整提示词并重新测试。输入修改后可再次测试，调试响应保留原请求快照。</p></div>
    {result && <section className="surface prep-section debug-result" aria-label="调试结果"><div className="row-between"><h2>{debugStatus[result.status] || result.status}</h2><span>{new Date(result.created_at).toLocaleString('zh-CN')}</span></div><div className="batch-stats"><div><span>输入 token</span><strong>{quantity(result.usage.input_tokens)}</strong></div><div><span>输出 token（含推理）</span><strong>{quantity(result.usage.output_tokens)}</strong><small>其中推理 {quantity(result.usage.reasoning_tokens)}</small></div><div><span>总 token</span><strong>{quantity(result.usage.total_tokens)}</strong></div><div><span>结束原因 / 耗时</span><strong>{result.reply.finish_reason || '等待返回'}</strong><small>{result.reply.latency_ms == null ? '等待返回' : (result.reply.latency_ms / 1000).toFixed(1) + ' 秒'}</small></div></div>{result.error && <p className="persistent-error">{result.error}</p>}{result.reply.finish_reason === 'length' && <p className="prep-notice">输出预算已用尽。这次请求不会自动重试；可关闭接口支持的思考模式、增加输出上限或缩短样本后再测试。已产生的 token 会保留。</p>}{result.validation.text != null && <><div className="prep-notice debug-quality"><strong>格式通过 · {result.validation.characters_preserved ? '原字顺序保留' : '文字差异待复核'} · 新增标点 {result.validation.quality?.inserted_punctuation ?? 0} 个</strong><p>{result.validation.quality?.notice || "请人工复核正文断句。"}</p></div><PunctuationComparison baseline={result.input_text} content={result.validation.text || ''} />{result.validation.questions?.map((q, i) => <p key={i}>{q.quote} · {q.reason} · {q.suggestion}</p>)}</>}<details><summary>模型返回正文</summary><pre>{result.reply.text || '本次响应没有正文'}</pre></details><details><summary>实际发送的请求快照</summary><pre>{JSON.stringify(result.request, null, 2)}</pre></details><details><summary>服务返回的原始响应与用量</summary>{result.reply.raw_response_truncated && <p>原始响应超过本地展示上限，仅保留前 1,000,000 字。</p>}<pre>{result.reply.raw_response || JSON.stringify(result.reply, null, 2)}</pre><pre>{JSON.stringify(result.reply.usage ?? null, null, 2)}</pre></details><p className="scope-note">响应标识 {result.reply.response_id || '未返回'} · 调试记录 {result.id}。当前结果对应上方请求快照，稿件未被修改。</p></section>}
  </div>;
}
