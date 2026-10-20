import { request, type ResearchUnit } from './api';

export type PrepNode = { id: string; title: string; label: string; start: number; end: number; path: string };
export type PrepText = { original: string; prepared: string; formatted: string; start: number; end: number; truncated: boolean };
export type PrepRun = { id: string; project_id: string; material_id: string; recipe: { name: string }; status: 'review' | 'accepted'; revision: number; summary: { workflow?: string; source_chars: number; text_count?: number; restored_repeat_count?: number; warnings: string[]; warning_count: number; coverage_ok: boolean }; pending_count: number; applied_count: number; source_hash: string; output_hash: string; created_at: string };
export const prepApi = {
  split: (project: string, material_id: string) => request<PrepRun>(`/api/projects/${project}/preprocess/split`, 'POST', { material_id }),
  runs: (project: string, material: string) => request<PrepRun[]>(`/api/projects/${project}/preprocess/runs?material_id=${material}`),
  nodes: (id: string, query = '', offset = 0) => request<{ total: number; items: PrepNode[] }>(`/api/preprocess/runs/${id}/texts?query=${encodeURIComponent(query)}&offset=${offset}`),
  text: (id: string, node = '') => request<PrepText>(`/api/preprocess/runs/${id}/text?text_id=${encodeURIComponent(node)}`),
  units: (id: string, text_ids: string[]) => request<ResearchUnit[]>(`/api/preprocess/runs/${id}/units`, 'POST', { text_ids }),
  exportText: (id: string, text: string) => request<{ filename: string; content: string }>(`/api/preprocess/runs/${id}/export?text_id=${encodeURIComponent(text)}`),
  exportZip: (id: string) => request<ArrayBuffer>(`/api/preprocess/runs/${id}/export?format=zip`),
};
