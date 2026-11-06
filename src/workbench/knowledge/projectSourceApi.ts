import { request } from '../api';
import type { MaterialRole } from '../api';

/** 项目材料（版本级来源）——与后端 /api/knowledge/projects/{id}/sources 返回结构一致。 */
export type SourceRange = { asset_id: string; page_from: number; page_to: number };
export type ProjectSource = {
  id: string;
  edition_id: string;
  title: string;
  label: string;
  roles: MaterialRole[];
  mode: 'edition' | 'ranges';
  ranges: SourceRange[];
  witness_label: string;
  scope_note: string;
  version: number;
};
export type ProjectSourceSelection = {
  roles: MaterialRole[];
  mode: 'edition' | 'ranges';
  ranges?: SourceRange[];
  witness_label?: string;
  scope_note?: string;
  version?: number;
};

/**
 * 一个文献库文件（条目）加入项目时的定位信息。
 * asset + pages 用于只关联当前文件，而不是同版本的全部附件。
 */
export type ProjectFileLink = { editionId: string; assetId: string; pages: number; title: string };

/** 再次关联同一文件时合并角色，避免把已设的“底本”覆盖成其他用途。 */
export function mergeRole(roles: MaterialRole[], role: MaterialRole): MaterialRole[] {
  return Array.from(new Set([...roles, role]));
}

/** 移除底本角色；一个来源至少要保留一个用途。 */
export function withoutBase(roles: MaterialRole[]): MaterialRole[] {
  const remaining = roles.filter(item => item !== 'base');
  return remaining.length ? remaining : ['reference'];
}

/** 来源是否覆盖某个附件（整个版本来源覆盖其全部附件）。 */
export function coversAsset(source: ProjectSource, assetId: string): boolean {
  return source.mode === 'edition' || source.ranges.some(range => range.asset_id === assetId);
}

export function listProjectSources(projectId: string): Promise<ProjectSource[]> {
  return request<ProjectSource[]>(`/api/knowledge/projects/${encodeURIComponent(projectId)}/sources`);
}

export function putProjectSource(projectId: string, editionId: string, selection: ProjectSourceSelection): Promise<ProjectSource> {
  return request<ProjectSource>(`/api/knowledge/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(editionId)}`, 'PUT', selection);
}

export function patchProjectSource(projectId: string, source: ProjectSource, change: Partial<ProjectSourceSelection> & { roles: MaterialRole[] }): Promise<ProjectSource> {
  return request<ProjectSource>(`/api/knowledge/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(source.id)}`, 'PATCH', {
    roles: change.roles,
    mode: change.mode ?? source.mode,
    ranges: change.ranges ?? source.ranges,
    witness_label: change.witness_label ?? source.witness_label,
    scope_note: change.scope_note ?? source.scope_note,
    version: source.version,
  });
}

export async function removeProjectSource(projectId: string, sourceId: string): Promise<void> {
  await request(`/api/knowledge/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`, 'DELETE');
}

/**
 * 只保留一个底本：把其他来源里的 base 去掉。
 * 后端目前把底本当作 roles 标签，先由前端维护唯一性；后端实体化后可移除此补偿。
 */
export async function ensureSoleBase(projectId: string, keepSourceId: string): Promise<void> {
  const sources = await listProjectSources(projectId);
  for (const source of sources) {
    if (source.id === keepSourceId || !source.roles.includes('base')) continue;
    await patchProjectSource(projectId, source, { roles: withoutBase(source.roles) });
  }
}

/**
 * 把文献库中的一个文件加入项目：
 * - 已关联同一版本/附件时合并角色，不覆盖已有用途；
 * - 未关联时以“当前文件”为范围建立来源（不会误关联整部书的其他分册）；
 * - 设为底本时清理其他来源的底本角色。
 */
export async function linkFileToProject(projectId: string, file: ProjectFileLink, role: MaterialRole): Promise<ProjectSource> {
  const sources = await listProjectSources(projectId);
  const existing = sources.find(source => source.edition_id === file.editionId && (source.mode === 'edition' || coversAsset(source, file.assetId)));
  let saved: ProjectSource;
  if (existing) {
    saved = await patchProjectSource(projectId, existing, {
      roles: mergeRole(existing.roles, role),
      witness_label: role === 'witness' && !existing.witness_label ? file.title : existing.witness_label,
    });
  } else {
    saved = await putProjectSource(projectId, file.editionId, {
      roles: [role],
      mode: 'ranges',
      ranges: [{ asset_id: file.assetId, page_from: 1, page_to: Math.max(1, file.pages) }],
      witness_label: role === 'witness' ? file.title : '',
      scope_note: '',
    });
  }
  if (role === 'base') await ensureSoleBase(projectId, saved.id);
  return saved;
}

/** 在项目材料卡片上“设为底本”。 */
export async function setSourceAsBase(projectId: string, sourceId: string): Promise<void> {
  const sources = await listProjectSources(projectId);
  const source = sources.find(item => item.id === sourceId);
  if (!source) throw new Error('项目材料已变化，请刷新后重试');
  await patchProjectSource(projectId, source, { roles: mergeRole(source.roles, 'base') });
  await ensureSoleBase(projectId, sourceId);
}
