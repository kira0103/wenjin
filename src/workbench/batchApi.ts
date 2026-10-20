import { request } from './api';
export type BatchStatus = 'queued' | 'running' | 'pausing' | 'paused' | 'interrupted' | 'stopping' | 'stopped' | 'completed' | 'completed_with_errors';
export type Batch = { id: string; project_id: string; run_id: string; title: string; status: BatchStatus; created_at: string; started_at: string; finished_at: string; error: string; model: { model: string; provider_name: string; currency?: string; price_date?: string }; total: number; ended: number; counts: { queued: number; running: number; done: number; failed: number; stopped: number }; usage: { requests: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; input_reported: number; output_reported: number; total_reported: number; unknown_requests: number; in_flight: number }; current: { id: string; title: string; chunks: number; completed_chunks: number; stage: string; waiting_seconds: number } | null; pending_review: number; needs_merge: number; approved: number; reused_chunks: number; elapsed_seconds: number; estimated_remaining_seconds: number | null; estimated_cost: number | null; cost_note: string };
export type BatchFile = { has_text_changes?: boolean; id: string; seq: number; title: string; status: 'queued' | 'running' | 'done' | 'failed' | 'stopped'; merge_status: string; unit_id: string; error: string; finished_at: string; chars: number; chunks: number; completed_chunks: number; completed_chars: number; requests: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; latency_ms: number | null; pending_questions: number };
export type BatchChoice = { id: string; title: string; kind: string; chars: number; processed: boolean; manual_modified: boolean; unit_id: string };
export type BatchEvent = { id: number; level: string; message: string; created_at: string; item_id: string; chunk_id: string; attempt_id: string };
export type BatchResult = { current_content: string; current_revision: number; id: string; title: string; status: string; merge_status: string; unit_id: string; baseline: string; original: string; content: string; questions: { quote: string; reason: string; suggestion: string; location: string }[] };
export type BatchAttempt = { reviewed_from_history?: boolean; request?: unknown; id: string; chunk_id: string; status: string; started_at: string; finished_at: string; error: string; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; latency_ms: number; response_id: string; finish_reason: string; usage: unknown };
export type Baseline = { title: string; baseline: string; original: string; source: { run_id?: string; source_start?: number; source_end?: number } };
export type DiffSpan = { text: string; kind: 'unchanged' | 'punctuation' | 'changed' | 'added' | 'removed'; peer: string };
export type TextComparison = { source_spans: DiffSpan[]; result_spans: DiffSpan[]; changed_groups: number; inserted_punctuation: number };
export const batchApi = {
  deletePreview: (id: string) => request<import('./DeleteDialogs').DeletePreview>(`/api/collation/batches/${id}/delete-preview`),
  delete: (id: string, preview_token: string) => request(`/api/collation/batches/${id}`, 'DELETE', { confirmation: id, preview_token }),
  compare: (baseline: string, content: string) => request<TextComparison>('/api/collation/compare', 'POST', { baseline, content }),
  baseline: (id: string) => request<Baseline>(`/api/units/${id}/baseline`),
  sourceIssues: (id: string, offset = 0) => request<{ total: number; items: { start: number; original: string; replacement: string; reason: string; context: string }[] }>(`/api/units/${id}/source-issues?offset=${offset}`),
  scope: (project: string, run: string) => request<BatchChoice[]>(`/api/projects/${project}/collation/scope?run_id=${run}`),
  create: (project: string, run_id: string, text_ids: string[], request_id: string) => request<Batch>(`/api/projects/${project}/collation/batches`, 'POST', { run_id, text_ids, request_id }),
  list: (project: string) => request<Batch[]>(`/api/projects/${project}/collation/batches`),
  get: (id: string) => request<Batch>(`/api/collation/batches/${id}`),
  files: (id: string, offset = 0, status = '') => request<{ total: number; items: BatchFile[] }>(`/api/collation/batches/${id}/items?offset=${offset}&status=${status}`),
  events: (id: string) => request<BatchEvent[]>(`/api/collation/batches/${id}/events`),
  action: (id: string, action: 'pause' | 'resume' | 'stop' | 'retry') => request<Batch>(`/api/collation/batches/${id}/action`, 'POST', { action }),
  result: (id: string) => request<BatchResult>(`/api/collation/items/${id}/result`),
  attempts: (id: string, offset = 0) => request<{ total: number; items: BatchAttempt[] }>(`/api/collation/items/${id}/attempts?offset=${offset}`),
  export: (id: string) => request<ArrayBuffer>(`/api/collation/batches/${id}/export`),
  exportLog: (id: string) => request<{ filename: string; content: string }>(`/api/collation/batches/${id}/export?format=json`),
};
export const batchNames: Record<BatchStatus, string> = { queued: '排队中', running: '运行中', pausing: '正在暂停', paused: '已暂停', interrupted: '已中断，可继续', stopping: '正在停止', stopped: '已停止', completed: '机器处理结束，待复核', completed_with_errors: '机器处理结束，有失败文件' };
export const fileNames: Record<BatchFile['status'], string> = { queued: '待执行', running: '执行中', done: '机器完成', failed: '失败', stopped: '未执行 · 已停止' };
