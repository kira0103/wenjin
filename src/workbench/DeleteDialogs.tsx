import { useState } from 'react';
import { batchApi, type Batch } from './batchApi';
import { Modal } from './Pages';

export type DeletePreview = { id: string; name: string; created_at?: string; counts: Record<string, number>; can_delete: boolean; preview_token: string };

export function ProjectDeleteConfirmation({ preview, onCancel, onDelete }: { preview: DeletePreview; onCancel(): void; onDelete(preview: DeletePreview): Promise<void> }) {
  const [name, setName] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <Modal title={`确认删除研究项目：${preview.name}`} onClose={() => { if (!busy) onCancel(); }}>
    <p>将删除该项目的 {preview.counts.units} 份工作稿、{preview.counts.artifacts} 份成果、{preview.counts.batches} 个执行批次，以及项目内的拆分、调试和执行记录。</p>
    <p>文献库中的原文件和其他项目保留。删除前会备份数据库；项目工作数据删除后不能直接继续原任务。</p>
    {!preview.can_delete && <p className="persistent-error">项目仍有执行中的任务，请先停止批次或等待请求结束。</p>}
    <label className="form-label">输入项目名称“{preview.name}”确认删除<input aria-label="删除项目确认名称" disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
    {error && <p className="persistent-error">{error}</p>}
    <div className="modal-actions"><button className="button-secondary" disabled={busy} onClick={onCancel}>取消</button><button className="button-danger" disabled={busy || !preview.can_delete || name.trim() !== preview.name} onClick={() => { setBusy(true); void onDelete(preview).catch(e => setError(e.message)).finally(() => setBusy(false)); }}>{busy ? '删除中…' : '确认删除项目'}</button></div>
  </Modal>;
}

export function DeleteBatchButton({ batch, onDeleted, disabled = false }: { batch: Batch; onDeleted(id: string): void; disabled?: boolean }) {
  const [preview, setPreview] = useState<DeletePreview | null>(null), [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const active = ['queued', 'running', 'pausing', 'stopping'].includes(batch.status);
  return <><button className="button-quiet delete-record" disabled={disabled || busy || active} title={active ? '先停止批次并等待在途请求结束后删除' : '删除批次记录，已保存稿件保留'} onClick={() => { setBusy(true); setError(''); setConfirmed(false); void batchApi.deletePreview(batch.id).then(setPreview).catch(e => setError(e.message)).finally(() => setBusy(false)); }}>删除批次</button>
    {!preview && error && <span className="inline-error">{error}</span>}
    {preview && <Modal title="确认删除执行批次" onClose={() => { if (!busy) setPreview(null); }}>
      <p>{preview.name} · {preview.created_at && new Date(preview.created_at).toLocaleString('zh-CN')} · {preview.counts.files} 份文件</p>
      <p>删除该批次的 {preview.counts.attempts} 次调用记录、用量、日志与片段缓存。已保存的卷稿、校记和文献库原文件保留。</p>
      {!!preview.counts.candidates && <p className="prep-notice">还有 {preview.counts.candidates} 份未合并候选稿保存在此批次中，也会被删除，请先导出需要保留的候选稿。</p>}
      <p>删除后不能通过此批次断点继续。删除前会备份数据库。</p>
      {!preview.can_delete && <p className="persistent-error">此批次仍在执行，请先停止后删除。</p>}
      <label className="prep-check"><input aria-label="确认删除批次记录" type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />我已了解删除范围，确认删除该批次</label>
      {error && <p className="persistent-error">{error}</p>}
      <div className="modal-actions"><button className="button-secondary" disabled={busy} onClick={() => setPreview(null)}>取消</button><button className="button-danger" disabled={busy || !confirmed || !preview.can_delete} onClick={() => { setBusy(true); void batchApi.delete(batch.id, preview.preview_token).then(() => { setPreview(null); onDeleted(batch.id); }).catch(e => setError(e.message)).finally(() => setBusy(false)); }}>{busy ? '删除中…' : '确认删除批次'}</button></div>
    </Modal>}
  </>;
}
