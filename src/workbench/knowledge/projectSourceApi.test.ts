import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request } from '../api';
import type { ProjectSource } from './projectSourceApi';
import {
  coversAsset,
  ensureSoleBase,
  linkFileToProject,
  mergeRole,
  patchProjectSource,
  setSourceAsBase,
  withoutBase,
} from './projectSourceApi';

vi.mock('../api', () => ({ request: vi.fn() }));

const mocked = vi.mocked(request);

function source(overrides: Partial<ProjectSource> = {}): ProjectSource {
  return {
    id: 'source-1',
    edition_id: 'edition-1',
    title: '文献甲',
    label: '版本一',
    roles: ['reference'],
    mode: 'ranges',
    ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }],
    witness_label: '',
    scope_note: '',
    version: 2,
    ...overrides,
  };
}

const file = { editionId: 'edition-1', assetId: 'asset-1', pages: 3, title: '文件甲' };

beforeEach(() => {
  mocked.mockReset();
});

describe('project source roles', () => {
  it('mergeRole 去重合并且保留已有用途顺序', () => {
    expect(mergeRole(['witness', 'base'], 'reference')).toEqual(['witness', 'base', 'reference']);
    expect(mergeRole(['witness'], 'witness')).toEqual(['witness']);
    expect(mergeRole([], 'base')).toEqual(['base']);
  });

  it('withoutBase 移除底本并保证至少保留一个用途', () => {
    expect(withoutBase(['base'])).toEqual(['reference']);
    expect(withoutBase(['base', 'witness'])).toEqual(['witness']);
    expect(withoutBase(['witness', 'base', 'reference'])).toEqual(['witness', 'reference']);
    expect(withoutBase(['reference'])).toEqual(['reference']);
  });

  it('coversAsset 对整版本与指定页段分别判定', () => {
    expect(coversAsset(source({ mode: 'edition', ranges: [] }), 'any-asset')).toBe(true);
    expect(coversAsset(source(), 'asset-1')).toBe(true);
    expect(coversAsset(source(), 'asset-2')).toBe(false);
    expect(coversAsset(source({ ranges: [] }), 'asset-1')).toBe(false);
  });
});

describe('linkFileToProject', () => {
  it('已关联同一版本时 PATCH 合并角色，不覆盖已有底本/参校本', async () => {
    const existing = source({ roles: ['witness', 'base'], witness_label: '参甲' });
    const merged = source({ roles: ['witness', 'base', 'reference'], witness_label: '参甲' });
    mocked.mockResolvedValueOnce([existing]);
    mocked.mockResolvedValueOnce(merged);

    const saved = await linkFileToProject('project-1', file, 'reference');

    expect(mocked).toHaveBeenNthCalledWith(1, '/api/knowledge/projects/project-1/sources');
    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/source-1', 'PATCH', {
      roles: ['witness', 'base', 'reference'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }],
      witness_label: '参甲',
      scope_note: '',
      version: 2,
    });
    expect(saved.roles).toEqual(['witness', 'base', 'reference']);
    expect(saved.witness_label).toBe('参甲');
  });

  it('整个版本来源覆盖当前附件，PATCH 时保留 edition 模式', async () => {
    const existing = source({ mode: 'edition', ranges: [], roles: ['witness'] });
    const merged = source({ mode: 'edition', roles: ['witness', 'base'] });
    mocked.mockResolvedValueOnce([existing]);
    mocked.mockResolvedValueOnce(merged);
    mocked.mockResolvedValueOnce([merged]);

    await linkFileToProject('project-1', { ...file, assetId: 'other-asset', pages: 9 }, 'base');

    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/source-1', 'PATCH', {
      roles: ['witness', 'base'],
      mode: 'edition',
      ranges: [],
      witness_label: '',
      scope_note: '',
      version: 2,
    });
  });

  it('未关联时 PUT 只关联当前附件页段，不扩成整部书', async () => {
    mocked.mockResolvedValueOnce([]);
    mocked.mockResolvedValueOnce(source({ id: 'new-source', roles: ['witness'], witness_label: '某书' }));

    await linkFileToProject('project-1', { editionId: 'edition-9', assetId: 'asset-9', pages: 8, title: '某书' }, 'witness');

    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/edition-9', 'PUT', {
      roles: ['witness'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-9', page_from: 1, page_to: 8 }],
      witness_label: '某书',
      scope_note: '',
    });
  });

  it('同版本但当前附件不在已有范围内时，按新页段建立来源', async () => {
    mocked.mockResolvedValueOnce([source({ ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }] })]);
    mocked.mockResolvedValueOnce(source({ id: 'new-source', mode: 'ranges', roles: ['reference'] }));

    await linkFileToProject('project-1', { editionId: 'edition-1', assetId: 'asset-2', pages: 5, title: '分册二' }, 'reference');

    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/edition-1', 'PUT', {
      roles: ['reference'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-2', page_from: 1, page_to: 5 }],
      witness_label: '',
      scope_note: '',
    });
  });

  it('权限角色 base 时清理其他来源的底本，且不会 PATCH 自己', async () => {
    const other = source({ id: 'old-base', edition_id: 'edition-2', roles: ['base', 'witness'], ranges: [] });
    const saved = source({ id: 'new-source', edition_id: 'edition-3', roles: ['base'], ranges: [{ asset_id: 'asset-3', page_from: 1, page_to: 4 }] });
    mocked.mockResolvedValueOnce([]);
    mocked.mockResolvedValueOnce(saved);
    mocked.mockResolvedValueOnce([saved, other]);
    mocked.mockResolvedValueOnce(other);

    await linkFileToProject('project-1', { editionId: 'edition-3', assetId: 'asset-3', pages: 4, title: '新底本' }, 'base');

    const calls = mocked.mock.calls;
    expect(calls.map(call => call[0])).toEqual([
      '/api/knowledge/projects/project-1/sources',
      '/api/knowledge/projects/project-1/sources/edition-3',
      '/api/knowledge/projects/project-1/sources',
      '/api/knowledge/projects/project-1/sources/old-base',
    ]);
    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/edition-3', 'PUT', {
      roles: ['base'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-3', page_from: 1, page_to: 4 }],
      witness_label: '',
      scope_note: '',
    });
    expect(mocked).toHaveBeenNthCalledWith(4, '/api/knowledge/projects/project-1/sources/old-base', 'PATCH', {
      roles: ['witness'],
      mode: 'ranges',
      ranges: [],
      witness_label: '',
      scope_note: '',
      version: 2,
    });
  });

  it('已是底本的来源再加 base 不重复清理', async () => {
    const existing = source({ id: 'existing-base', roles: ['base'] });
    mocked.mockResolvedValueOnce([existing]);
    mocked.mockResolvedValueOnce(existing);
    mocked.mockResolvedValueOnce([existing]);

    await linkFileToProject('project-1', file, 'base');

    const paths = mocked.mock.calls.map(call => call[0]);
    expect(paths).toEqual([
      '/api/knowledge/projects/project-1/sources',
      '/api/knowledge/projects/project-1/sources/existing-base',
      '/api/knowledge/projects/project-1/sources',
    ]);
  });
});

describe('setSourceAsBase / ensureSoleBase', () => {
  it('设为底本会合并角色并清理其他来源的底本', async () => {
    const target = source({ id: 'target', roles: ['reference'] });
    const other = source({ id: 'other-base', edition_id: 'edition-2', roles: ['base', 'reference'], ranges: [] });
    mocked.mockResolvedValueOnce([target, other]);
    mocked.mockResolvedValueOnce(source({ id: 'target', roles: ['reference', 'base'] }));
    mocked.mockResolvedValueOnce([source({ id: 'target', roles: ['reference', 'base'] }), other]);
    mocked.mockResolvedValueOnce(other);

    await setSourceAsBase('project-1', 'target');

    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/target', 'PATCH', {
      roles: ['reference', 'base'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }],
      witness_label: '',
      scope_note: '',
      version: 2,
    });
    expect(mocked).toHaveBeenNthCalledWith(4, '/api/knowledge/projects/project-1/sources/other-base', 'PATCH', {
      roles: ['reference'],
      mode: 'ranges',
      ranges: [],
      witness_label: '',
      scope_note: '',
      version: 2,
    });
  });

  it('来源已变化时提示刷新而不是静默成功', async () => {
    mocked.mockResolvedValueOnce([source({ id: 'other' })]);
    await expect(setSourceAsBase('project-1', 'missing')).rejects.toThrow('项目材料已变化，请刷新后重试');
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  it('ensureSoleBase 只清理带 base 的其他来源', async () => {
    const keep = source({ id: 'keep', roles: ['base'] });
    const plain = source({ id: 'plain', roles: ['reference'] });
    const stale = source({ id: 'stale', roles: ['base', 'witness'] });
    mocked.mockResolvedValueOnce([keep, plain, stale]);
    mocked.mockResolvedValueOnce(stale);

    await ensureSoleBase('project-1', 'keep');

    expect(mocked).toHaveBeenCalledTimes(2);
    expect(mocked).toHaveBeenNthCalledWith(2, '/api/knowledge/projects/project-1/sources/stale', 'PATCH', {
      roles: ['witness'],
      mode: 'ranges',
      ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }],
      witness_label: '',
      scope_note: '',
      version: 2,
    });
  });
});

describe('请求编码与 API 契约', () => {
  it('项目 ID 会编码，PATCH 始终携带乐观锁 version', async () => {
    mocked.mockResolvedValueOnce(source());

    await patchProjectSource('项目 1', source(), { roles: ['base'] });

    expect(mocked).toHaveBeenCalledWith(
      '/api/knowledge/projects/%E9%A1%B9%E7%9B%AE%201/sources/source-1',
      'PATCH',
      {
        roles: ['base'],
        mode: 'ranges',
        ranges: [{ asset_id: 'asset-1', page_from: 1, page_to: 3 }],
        witness_label: '',
        scope_note: '',
        version: 2,
      },
    );
  });
});
