import { describe, expect, it, vi } from 'vitest';
import type { Material, MaterialDetail } from './api';
import { readSession, defaultView, materialView, searchMaterials, sourceInfo } from './viewState';

const materials: Material[] = [
  { id: 'a', project_id: 'p', title: '地方社会史', filename: 'regional-HISTORY.pdf', page_count: 3, extraction_coverage: 2 / 3, created_at: '' },
  { id: 'b', project_id: 'p', title: '制度史', filename: 'institutions.pdf', page_count: 20, extraction_coverage: 1, created_at: '' },
];

describe('research context', () => {
  it('restores the former preprocessing page inside collation without losing saved work', () => {
    const legacy = { projectId: 'p', selectedByProject: { p: 'material' }, unitByProject: { p: 'draft' }, views: {}, pageByProject: { p: 'preprocess', other: 'research' } };
    vi.stubGlobal('localStorage', { getItem: () => JSON.stringify(legacy) });
    try {
      const restored = readSession();
      expect(restored.pageByProject).toEqual({ p: 'volume-preparation', other: 'research' });
      expect(restored.selectedByProject).toEqual(legacy.selectedByProject);
      expect(restored.unitByProject).toEqual(legacy.unitByProject);
    } finally { vi.unstubAllGlobals(); }
  });
  it('finds filenames without changing project material totals or selected material', () => {
    expect(searchMaterials(materials, '  history  ').map(m => m.id)).toEqual(['a']);
    expect(materials).toHaveLength(2);
    expect(materials.find(m => m.id === 'b')?.title).toBe('制度史');
  });
  it('restores a short document without carrying an invalid page range', () => {
    const saved = { ...defaultView(20), readerPage: 15, pageFrom: 2, pageTo: 20, message: '保留任务草稿' };
    const restored = materialView(saved, 3);
    expect(restored.readerPage).toBe(3);
    expect(restored.pageFrom).toBe(2);
    expect(restored.pageTo).toBe(3);
    expect(restored.message).toBe('保留任务草稿');
  });
  it('reports exactly which selected pages cannot be sent to the model', () => {
    const detail: MaterialDetail = { ...materials[0], pages: [{ page_number: 1, text: '可读原文' }, { page_number: 2, text: '  ' }, { page_number: 3, text: '另一页' }], references: [], reference_summary: { total: 0, missing: 0, in_library: 0, version_unverified: 0 } };
    const info = sourceInfo(detail, { ...defaultView(3), pageFrom: 2, pageTo: 3 });
    expect(info.missing).toEqual([2]);
    expect(info.hasText).toBe(true);
    expect(info.characters).toBe('[第 3 页]\n另一页'.length);
  });
  it('allows the UI to distinguish a scan-only range from missing material detail', () => {
    const detail: MaterialDetail = { ...materials[0], pages: [{ page_number: 1, text: '' }], references: [], reference_summary: { total: 0, missing: 0, in_library: 0, version_unverified: 0 } };
    expect(sourceInfo(detail, { ...defaultView(3), pageFrom: 1, pageTo: 1 }).hasText).toBe(false);
    expect(sourceInfo(null, defaultView(3)).characters).toBe(0);
  });
  it('counts Word text segments with their actual locator instead of PDF page markers', () => {
    const detail: MaterialDetail = { ...materials[0], position_kind: 'section', file_format: 'docx', pages: [{ page_number: 1, text: 'Word 正文' }], references: [], reference_summary: { total: 0, missing: 0, in_library: 0, version_unverified: 0 } };
    expect(sourceInfo(detail, { ...defaultView(1), pageFrom: 1, pageTo: 1 }).characters).toBe('[第 1 文字段]\nWord 正文'.length);
  });
});
