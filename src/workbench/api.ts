import { call } from './transport';

type ProjectType = 'general' | 'collation' | 'literature' | 'translation';
export type Project = { id: string; name: string; description: string; project_type: ProjectType; rules: string; archived?: number; material_count?: number };
export type MaterialRole = 'base' | 'witness' | 'citation' | 'reference' | 'auxiliary';
export type Material = { id: string; project_id: string; project_ids?: string[]; entry_id?: string; file_format?: string; position_kind?: 'page' | 'section'; roles?: MaterialRole[]; witness_label?: string; scope_note?: string; item_type?: string; version_label?: string; filename: string; title: string; page_count: number; extraction_coverage: number; reference_count?: number; created_at: string };
export type RefItem = { id: string; ordinal: number; raw_text: string; title: string; authors: string; year: string; page_number: number; match_id?: string | null; match_status: string; matched_title?: string; matched_authors?: string; matched_year?: string; matched_reference?: string };
export type MaterialDetail = Material & { kind?: string; edition_id?: string; source_revision?: string; source_revision_changed?: boolean|null; source_positions?: {asset_id?: string; page_index?: number; number: number; missing?: boolean}[]; pages: { page_number: number; text: string }[]; references: RefItem[]; reference_summary: { total: number; in_library: number; missing: number; version_unverified: number } };
export type TaskKind = 'translate' | 'summary' | 'punctuate' | 'proofread';
export type SourceContext = { unit_selection?: { start: number; end: number; source_text: string }; selection?: string; material_id?: string | null; material_title?: string; position_kind?: 'page' | 'section'; unit_id?: string; unit_title?: string; input_revision?: number; page_from?: number | null; page_to?: number | null; missing_pages?: number[]; manual_source?: boolean; truncated?: boolean; model?: string; provider_name?: string };
export type TaskArtifact = { source_excerpt?: string; source_excerpt_truncated?: boolean; id: string; title: string; content: string; source_context?: SourceContext };
export type WorkTask = { id: string; project_id: string; material_id: string | null; material_title?: string; task_type: TaskKind; title: string; status: string; error?: string; instructions?: string; page_from?: number; page_to?: number; manual_source?: boolean; target_language?: string; artifact?: TaskArtifact | null; created_at: string; updated_at?: string };
type ArtifactBody = { content: string; source_context?: SourceContext; source_excerpt?: string; source_excerpt_truncated?: boolean; research_sources?:{anchor_id:string;quote:string;locator:{title:string;edition_label:string;page_index:number}}[] };
export type WorkArtifact = { id: string; project_id: string; task_id: string; material_id: string | null; material_title?: string; title: string; artifact_type: string; body: string; created_at: string };
export type ModelSettings = { api_key_managed?: boolean; service_mode?: string; context_tokens?: number | null; max_output_tokens?: number | null; price_input_per_million?: number | null; price_output_per_million?: number | null; price_cached_per_million?: number | null; currency?: string; price_date?: string; base_url: string; model: string; provider_name: string; configured?: boolean; has_api_key?: boolean; data_dir?: string };
export type UnitNote = { id: string; kind: 'variant' | 'citation' | 'question' | 'ai'; location: string; base_reading: string; witness_id: string; witness_reading: string; decision: string; evidence: string; status: 'pending' | 'accepted' | 'rejected' };
export type ResearchUnit = { baseline_text?: string | null; preprocess?: { run_id?: string; node_id?: string; source_start?: number; source_end?: number; recipe_name?: string; source_hash?: string; output_hash?: string }; id: string; project_id: string; kind: 'collation' | 'translation' | 'reading'; title: string; volume: string; category: string; material_id: string; page_from: number; page_to: number; location_label: string; status: 'working' | 'review' | 'approved'; revision: number; notes: UnitNote[]; content?: string; original_text?: string; created_at: string; updated_at: string; versions?: { revision: number; status: string; created_at: string }[]; tasks?: string[] };
export type Bootstrap = { catalog_error?: string; collation_activity?: { id: string; status: string; total: number; ended: number } | null; prepared_material_count?: number; projects: Project[]; materials: Material[]; global_materials: Material[]; units: ResearchUnit[]; tasks: WorkTask[]; artifacts: WorkArtifact[]; settings: ModelSettings };

export async function request<T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'GET', body?: any, contentType?: string): Promise<T> {
  return call<T>({ path, method, body, contentType });
}

export const api = {
  bootstrap: (projectId: string) => request<Bootstrap>(`/api/bootstrap?project_id=${encodeURIComponent(projectId)}`),
  material: (id: string) => request<MaterialDetail>(`/api/materials/${encodeURIComponent(id)}`),
  importDocument: (data: ArrayBuffer | Blob, filename: string, projectId?: string, role = 'reference', entryId?: string) => request<Material & { duplicate: boolean }>(`/api/materials/import?filename=${encodeURIComponent(filename)}&role=${encodeURIComponent(role)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''}${entryId ? `&entry_id=${encodeURIComponent(entryId)}` : ''}`, 'POST', data, 'application/octet-stream'),
  createProject: (config: Partial<Project> & { name: string }) => request<Project>('/api/projects', 'POST', config),
  configureProject: (project: Project) => request<Project>(`/api/projects/${project.id}`, 'PUT', project),
  projectDeletePreview: (id: string) => request<import('./DeleteDialogs').DeletePreview>(`/api/projects/${id}/delete-preview`),
  deleteProject: (id: string, confirmation: string, preview_token: string) => request<{ next_project_id: string; cleanup_errors: string[] }>(`/api/projects/${id}`, 'DELETE', { confirmation, preview_token }),
  archiveProject: (id: string) => request(`/api/projects/${id}/archive`, 'POST', {}),
  restoreProject: (id: string) => request(`/api/projects/${id}/restore`, 'POST', {}),
  createUnit: (projectId: string, config: object) => request<ResearchUnit>(`/api/projects/${projectId}/units`, 'POST', config),
  unit: (id: string) => request<ResearchUnit>(`/api/units/${id}`),
  saveUnit: (unit: ResearchUnit, status = unit.status, confirm = false) => request<ResearchUnit>(`/api/units/${unit.id}`, 'PUT', { expected_revision: unit.revision, content: unit.content || '', notes: unit.notes, status, confirm_review: confirm }),
  exportUnit: (unit: ResearchUnit) => request<{filename:string;content:string}>(`/api/units/${unit.id}/export`, 'POST', { expected_revision:unit.revision,content:unit.content||'',notes:unit.notes,status:unit.status }),
  unitVersion: (id: string, revision: number) => request<ResearchUnit>(`/api/units/${id}/versions/${revision}`),
  addToLibrary: (referenceId: string) => request<any>(`/api/references/${referenceId}/add-to-library`, 'POST', {}),
  confirmReferenceVersion: (referenceId: string) => request(`/api/references/${referenceId}/confirm-version`, 'POST', {}),
  createTask: (payload: object) => request<WorkTask>('/api/tasks', 'POST', payload),
  task: (id: string) => request<WorkTask>(`/api/tasks/${id}`),
  resumeTask: (id: string) => request(`/api/tasks/${id}/resume`, 'POST', {}),
  saveSettings: (settings: ModelSettings & { api_key?: string }) => request<ModelSettings>('/api/settings', 'PUT', settings),
  testConnection: () => request<{ ok: boolean; model: string }>('/api/settings/test', 'POST', {}),
  artifacts: (projectId: string) => request<WorkArtifact[]>(`/api/artifacts?project_id=${encodeURIComponent(projectId)}`),
  exportArtifact: (id: string) => request<{filename:string;content:string}>(`/api/artifacts/${id}/export`),
};

export const taskNames: Record<string, string> = {
  research: '文献研究',
  translate: '文本翻译', summary: '论文总结', punctuate: '古籍标点建议', proofread: 'OCR 文本疑字建议',
};

export const tasksAvailable: TaskKind[] = ['translate', 'summary', 'punctuate', 'proofread'];
export const taskDescriptions: Record<TaskKind, string> = {
  translate: '保留术语与段落，译文需对照原文复核', summary: '生成阅读卡片，并标明所处理的页码范围',
  punctuate: '仅建议断句与标点，保留底本原字', proofread: '根据文字上下文提出疑字候选，需人工对照影印页',
};

export function artifactBody(body: string): ArtifactBody {
  try { const value = JSON.parse(body); return { ...value, content: typeof value.content === 'string' ? value.content : body }; }
  catch { return { content: body || '' }; }
}
