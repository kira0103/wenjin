import { useEffect, useRef, useState } from 'react';
import { Download, FileText, LoaderCircle } from 'lucide-react';
import { type Material, type Project, type ResearchUnit } from './api';
import { prepApi, type PrepNode, type PrepRun, type PrepText } from './preprocessApi';

export function VolumePreparation({ project, materials, onCreated, onMaterials, onBatch }: { onBatch(run: string): void; project: Project; materials: Material[]; onCreated(unit: ResearchUnit): Promise<void>; onMaterials(): void }) {
  const [materialId, setMaterialId] = useState(() => (materials.find(m => m.roles?.includes('base')) || materials[0])?.id || '');
  const [processing, setProcessing] = useState(false);
  const material = materials.find(m => m.id === materialId) || materials.find(m => m.roles?.includes('base')) || materials[0];
  return <div className="preprocess-page volume-preparation">
    <p className="scope-note">点校前先准备底本的卷文本：按卷拆分、还原重文号、去掉【】并将小标题换行。完成后可导出，或选择一卷开始点校。</p>
    {!material ? <div className="empty-block"><strong>先添加底本</strong><span>支持 Word、TXT 和有文字层的 PDF。</span><button className="button-primary" onClick={onMaterials}>添加项目材料</button></div> : <>
      <label className="form-label">底本 / 原文附件<select aria-label="预处理底本" disabled={processing} value={material.id} onChange={e => setMaterialId(e.target.value)}>{materials.map(m => <option key={m.id} value={m.id}>{m.filename}{m.roles?.includes('base') ? ' · 底本' : ''}</option>)}</select></label>
      <Preparation key={`${project.id}-${material.id}`} project={project} material={material} onCreated={onCreated} onBatch={onBatch} onBusy={setProcessing} />
    </>}
  </div>;
}

function Preparation({ project, material, onCreated, onBusy, onBatch }: { onBatch(run: string): void; project: Project; material: Material; onCreated(unit: ResearchUnit): Promise<void>; onBusy(value: boolean): void }) {
  const [run, setRun] = useState<PrepRun | null>(null), [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [nodes, setNodes] = useState<PrepNode[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0), [query, setQuery] = useState('');
  const [activeId, setActiveId] = useState(''), [text, setText] = useState<PrepText | null>(null), [textTitle, setTextTitle] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; onBusy(false); }; }, [onBusy]);
  useEffect(() => { onBusy(!!busy); }, [busy, onBusy]);
  useEffect(() => {
    let cancelled = false;
    void prepApi.runs(project.id, material.id).then(list => { if (!cancelled) setRun(list[0] || null); }).catch(e => { if (!cancelled) setError(e.message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [project.id, material.id]);
  useEffect(() => {
    if (!run) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void prepApi.nodes(run.id, query, offset).then(result => {
        if (cancelled) return;
        setNodes(result.items); setTotal(result.total);
        setActiveId(current => result.items.some(n => n.id === current) ? current : (result.items.find(n => n.label === '卷') || result.items[0])?.id || '');
      }).catch(e => { if (!cancelled) setError(e.message); });
    }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [run?.id, query, offset]);
  useEffect(() => {
    setText(null);
    if (!run || !activeId) return;
    let cancelled = false;
    void prepApi.text(run.id, activeId).then(value => { if (!cancelled) setText(value); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [run?.id, activeId]);
  useEffect(() => { setTextTitle(nodes.find(n => n.id === activeId)?.title || ''); }, [nodes, activeId]);
  const act = async (label: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(label); setError(''); setNotice('');
    try { await action(); } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : '处理失败，请重试'); }
    finally { if (alive.current) setBusy(''); }
  };
  const ready = run?.status === 'accepted';
  return <>
    {error && <p className="persistent-error" role="alert">{error}<button className="button-quiet" onClick={() => setError('')}>关闭提示</button></p>}
    {notice && <p className="prep-notice" role="status">{notice}</p>}
    <section className="surface prep-section prep-basic" aria-busy={!!busy}>
      <h2>拆分并清洗</h2>
      <p>固定使用《古今合璧事类备要》的处理规则，处理所选底本的全部文字。</p>
      <ul className="prep-basic-rules"><li>按实际卷题拆分，一卷一份文本，卷首材料另存。</li><li>按词头顺序还原“丨”重文号，去掉【】，保留引文前后的空格。</li><li>事理、事类、诗集、格物总论等小标题独占一行，支持简繁体。</li></ul>
      <div className="action-row"><button className="button-primary" disabled={!!busy || loading} onClick={() => void act('拆分并清洗中', async () => {
        const value = await prepApi.split(project.id, material.id);
        if (!alive.current) return;
        setRun(value); setNodes([]); setTotal(0); setQuery(''); setOffset(0); setActiveId(''); setText(null);
        setNotice('拆分完成，可以直接查看和导出。');
      })}>{busy === '拆分并清洗中' ? <LoaderCircle size={16} className="spin" /> : <FileText size={16} />}{loading ? '正在读取结果…' : busy === '拆分并清洗中' ? '正在拆分并清洗…' : run ? '重新拆分并清洗' : '开始拆分并清洗'}</button></div>
      <p className="scope-note">可重复执行，只保留最新拆分结果；原始文件与已有点校稿保留。无法确定的重文号或缺字留到点校时核对。</p>
    </section>
    {run && <section className="surface prep-section prep-output">
      <div className="prep-output-header"><div><h2>拆分结果</h2><p className="scope-note">{run.summary.source_chars.toLocaleString()} 字原文 · {run.summary.text_count?.toLocaleString()} 份文本 · {new Date(run.created_at).toLocaleString('zh-CN')}</p></div>
        <div className="action-row prep-result-actions"><button className="button-primary" disabled={!!busy || !ready} onClick={() => void act('导出全部卷文本', async () => { const bytes = await prepApi.exportZip(run.id); if (alive.current) await window.desktop.saveFile({ filename: '拆分卷文本.zip', data: bytes }); })}><Download size={16} />导出全部卷 ZIP</button><button className="button-secondary" disabled={!!busy || !ready || !text} onClick={() => void act('导出当前卷', async () => { const file = await prepApi.exportText(run.id, activeId); if (alive.current) await window.desktop.saveFile({ filename: file.filename, data: new TextEncoder().encode(file.content).buffer }); })}>导出当前卷 TXT</button></div>
      </div>
      {!ready && <p className="prep-notice">这是旧流程保存的待复核结果。点击上方“重新拆分并清洗”，即可生成可直接导出的文本。</p>}
      {!!run.summary.warnings.length && <details className="prep-warnings"><summary>卷题识别说明（{run.summary.warning_count || run.summary.warnings.length} 条）</summary>{run.summary.warnings.slice(0, 10).map((message, i) => <p key={i}>{message}</p>)}{run.summary.warnings.length > 10 && <p>请结合原文检查其余卷题。</p>}</details>}
      <div className="prep-range-header"><label className="form-label">查找卷文本<input aria-label="查找拆分文本" disabled={!!busy} value={query} onChange={e => { setQuery(e.target.value); setOffset(0); }} placeholder="例如：别集第五卷" /></label><Pagination offset={offset} total={total} busy={!!busy} onChange={setOffset} /></div>
      <div className="prep-results"><div className="prep-nodes" role="group" aria-label="拆分卷文本">{nodes.map(node => <div className={`prep-node ${activeId === node.id ? 'prep-node-active' : ''}`} key={node.id}><button disabled={!!busy} aria-pressed={activeId === node.id} onClick={() => setActiveId(node.id)} title={node.title}><strong>{node.title}</strong><small>{(node.end-node.start).toLocaleString()} 字原文</small></button></div>)}{!nodes.length && <p className="scope-note">{query ? '没有找到匹配的卷文本。' : '正在读取卷文本…'}</p>}</div>
        <div className="prep-source-preview">{text ? <><h3>{textTitle}</h3><h4>处理后文本</h4><pre className="prep-cleaned-text">{text.formatted}</pre>{text.truncated && <p className="scope-note">页面预览前 8,000 字，导出使用完整卷文本。</p>}<details className="prep-original"><summary>查看原始文本</summary><pre>{text.original}</pre></details></> : <p className="scope-note">{activeId ? '正在读取文本…' : '点击左侧卷名查看处理后文本。'}</p>}</div>
      </div>
      <div className="prep-next"><span>文本准备好后，可按文件顺序自动点校，也可打开当前卷手工整理。</span><button className="button-primary" disabled={!!busy || !ready} onClick={() => onBatch(run.id)}>批量点校</button><button className="button-secondary" disabled={!!busy || !ready || !text} onClick={() => void act('建立本卷点校工作', async () => { const units = await prepApi.units(run.id, [activeId]); if (alive.current && units[0]) await onCreated(units[0]); })}>开始本卷点校</button></div>
    </section>}
    {busy && <div className="prep-busy" role="status"><LoaderCircle size={18} className="spin" />{busy}…</div>}
  </>;
}

function Pagination({ offset, total, busy, onChange }: { offset: number; total: number; busy: boolean; onChange(n: number): void }) {
  return <div className="prep-pagination"><button className="button-quiet" disabled={!offset || busy} onClick={() => onChange(Math.max(0, offset-80))}>上一页</button><span>{total ? `${offset+1}–${Math.min(total, offset+80)}` : '0'} / {total}</span><button className="button-quiet" disabled={offset+80 >= total || busy} onClick={() => onChange(offset+80)}>下一页</button></div>;
}
