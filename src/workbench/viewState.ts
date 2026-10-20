import type { Material, MaterialDetail, TaskKind } from './api';

export type WorkTab = 'reader' | 'references' | 'notes';
export type ReaderZoom = number | 'fit';
export type MaterialView = { tab: WorkTab; readerPage: number; pageFrom: number; pageTo: number; sideOpen: boolean; taskKind: TaskKind; message: string; pastedSource: string; sourceMode: 'pdf' | 'paste'; targetLanguage: string; zoom: ReaderZoom; taskId?: string };
export type Session = { projectId: string; selectedByProject: Record<string, string>; views: Record<string, MaterialView>; unitByProject?: Record<string, string>; pageByProject?: Record<string, string> };
const key = 'shixue-workbench-session-v1';
export const defaultView = (pages: number): MaterialView => ({ tab: 'reader', readerPage: 1, pageFrom: 1, pageTo: Math.min(6, pages), sideOpen: true, taskKind: 'summary', message: '', pastedSource: '', sourceMode: 'pdf', targetLanguage: '中文', zoom: 'fit' });
export function readSession(): Session {
  try {
    const s = JSON.parse(localStorage.getItem(key) || '{}');
    const pages: Record<string, string> = {};
    for (const [project, value] of Object.entries(s.pageByProject || {})) { if (typeof value === 'string') pages[project] = value === 'preprocess' ? 'volume-preparation' : value; }
    return { projectId: typeof s.projectId === 'string' ? s.projectId : 'project-main', selectedByProject: s.selectedByProject && typeof s.selectedByProject === 'object' ? s.selectedByProject : {}, views: s.views && typeof s.views === 'object' ? s.views : {}, unitByProject: s.unitByProject || {}, pageByProject: pages };
  } catch { return { projectId: 'project-main', selectedByProject: {}, views: {} }; }
}
export function saveSession(session: Session) { try { localStorage.setItem(key, JSON.stringify(session)); } catch { /* Working drafts remain in memory if storage is unavailable. */ } }
export function clampPage(value: number, pages: number) { return Math.max(1, Math.min(pages || 1, Math.floor(value) || 1)); }
export function materialView(saved: MaterialView | undefined, pages: number): MaterialView {
  const v = { ...defaultView(pages), ...saved };
  return { ...v, readerPage: clampPage(v.readerPage, pages), pageFrom: clampPage(v.pageFrom, pages), pageTo: clampPage(v.pageTo, pages), zoom: v.zoom === 'fit' ? 'fit' : Math.max(.25, Math.min(3, v.zoom || 1)) };
}
export function searchMaterials(materials: Material[], query: string) {
  const term = query.trim().toLocaleLowerCase();
  return materials.filter(item => !term || `${item.title} ${item.filename}`.toLocaleLowerCase().includes(term));
}
export function sourceInfo(detail: MaterialDetail | null, view: MaterialView) {
  const pages = detail?.pages.filter(p => p.page_number >= view.pageFrom && p.page_number <= view.pageTo) || [];
  const textPages = pages.filter(p => p.text.trim());
  const missing = Array.from({ length: Math.max(0, view.pageTo - view.pageFrom + 1) }, (_, i) => view.pageFrom + i).filter(n => !textPages.some(p => p.page_number === n));
  const text = textPages.map(p => `[第 ${p.page_number} ${detail?.position_kind === "section" ? "文字段" : "页"}]\n${p.text}`).join('\n\n');
  return { missing, characters: text.length, hasText: textPages.length > 0 };
}
