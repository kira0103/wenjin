import { request, type ModelSettings } from './api';
type DebugProfile = { system_prompt: string; max_tokens: number | null; temperature: number; thinking: 'default' | 'disabled' | 'enabled' };
export type DebugConfig = { recommended_prompt: string; revision: number; config: DebugProfile; model: ModelSettings; contract: string; updated_at: string };
export type DebugInput = { validation_feedback?: string; config: DebugProfile; input_text: string; title: string; lead: string; attempt_id?: string };
export type DebugRun = { retry_feedback?: string; id: string; title: string; status: string; config: DebugProfile; model: ModelSettings; request: unknown; input_text: string; error: string; passed: boolean; created_at: string; finished_at: string; reply: { text?: string; raw_response?: string; raw_response_truncated?: boolean; response_id?: string; finish_reason?: string; latency_ms?: number; usage?: unknown }; validation: { characters_preserved?: boolean; quality?: { text_changes?: number; inserted_punctuation: number; unchanged: boolean; unpunctuated_body_lines: number; requires_review: boolean; notice: string }; text?: string; questions?: { quote: string; reason: string; suggestion: string }[] }; usage: { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; reasoning_tokens: number | null } };
export type DebugSeed = DebugInput & { model: ModelSettings; original_request: unknown; original_reply: unknown; original_error: string; original_finish_reason: string; original_usage: unknown };
export type DebugRecord = { id: string; title: string; status: string; created_at: string };
export const debugApi = {
  config: (project: string) => request<DebugConfig>(`/api/projects/${project}/collation/debug/config`),
  seed: (project: string, attempt: string) => request<DebugSeed>(`/api/projects/${project}/collation/debug/seed/${attempt}`),
  preview: (project: string, input: DebugInput) => request<{ request: unknown; model: ModelSettings; sample_chars: number }>(`/api/projects/${project}/collation/debug/preview`, 'POST', input),
  run: (project: string, input: DebugInput, request_id: string) => request<DebugRun>(`/api/projects/${project}/collation/debug/runs`, 'POST', { ...input, request_id }),
  get: (project: string, id: string) => request<DebugRun>(`/api/projects/${project}/collation/debug/runs/${id}`),
  history: (project: string) => request<DebugRecord[]>(`/api/projects/${project}/collation/debug/runs`),
  save: (project: string, config: DebugProfile, expected_revision: number, debug_run_id: string) => request<DebugConfig>(`/api/projects/${project}/collation/debug/config`, 'PUT', { config, expected_revision, debug_run_id }),
};
export const debugStatus: Record<string, string> = { review: '已返回，含文字差异待复核', running: '等待模型返回', passed: '格式与字序通过，待人工复核', no_change: '未新增标点，需人工核对', failed: '调试失败', interrupted: '调试中断' };
