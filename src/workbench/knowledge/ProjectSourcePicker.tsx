import { useEffect, useRef, useState } from 'react';
import { FileText, LoaderCircle, Search, Upload } from 'lucide-react';
import { request } from '../api';
import type { MaterialRole } from '../api';
import { Drawer } from '../Overlays';
import type { DirectoryResult, DocumentEntry, DocumentGroups } from './documentTypes';
import {
  linkFileToProject,
  type ProjectSource,
} from './projectSourceApi';
import './projectSourcePicker.css';

type Scope = 'personal' | 'system';
type EntryDetail = { asset_id: string | null; pages: number; active_revision: string | null };
type Tone = 'success' | 'error' | 'neutral';

const ROLE_OPTIONS: { role: MaterialRole; label: string; hint: string }[] = [
  { role: 'base', label: '设为底本', hint: '点校的原文依据，一个项目只能有一个' },
  { role: 'witness', label: '设为参校本', hint: '用于异文核对，可添加多个' },
  { role: 'reference', label: '仅研究参考', hint: '检索与研究使用，不参与点校' },
];

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '操作失败，请重试';
}

export default function ProjectSourcePicker({
  projectId,
  sources,
  initialRole,
  allowSystemLibrary,
  onClose,
  onChanged,
  onNotify,
  onImport,
}: {
  projectId: string;
  sources: ProjectSource[];
  initialRole?: MaterialRole;
  allowSystemLibrary: boolean;
  onClose(): void;
  onChanged(): Promise<void>;
  onNotify(message: string, tone?: Tone): void;
  onImport(role: MaterialRole): Promise<boolean | null>;
}) {
  const [scope, setScope] = useState<Scope>('personal');
  const [groups, setGroups] = useState<DocumentGroups | null>(null);
  const [group, setGroup] = useState('');
  const [kind, setKind] = useState('all');
  const [query, setQuery] = useState('');
  const [files, setFiles] = useState<DocumentEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [role, setRole] = useState<MaterialRole>(initialRole || 'reference');
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [actionError, setActionError] = useState('');
  const generation = useRef(0);
  const entryById = useRef(new Map<string, DocumentEntry>());
  const apiBase = scope === 'system' ? '/api/system-library/knowledge' : '/api/knowledge';

  const baseSource = sources.find(source => source.roles.includes('base'));
  const linkedEditions = new Set(sources.map(source => source.edition_id));
  const selectedEntries = selected.map(id => entryById.current.get(id)).filter((entry): entry is DocumentEntry => Boolean(entry));

  useEffect(() => {
    if (!allowSystemLibrary) setScope('personal');
  }, [allowSystemLibrary]);

  useEffect(() => {
    let cancelled = false;
    setGroups(null);
    setGroup('');
    setQuery('');
    setKind('all');
    setFiles([]);
    setTotal(0);
    setNextOffset(null);
    setSelected([]);
    setLoadError('');
    void request<DocumentGroups>(`${apiBase}/document-groups`)
      .then(value => { if (!cancelled) setGroups(value); })
      .catch(error => { if (!cancelled) setLoadError(errorText(error)); });
    return () => { cancelled = true; };
  }, [apiBase, reload]);

  useEffect(() => {
    let cancelled = false;
    const ticket = ++generation.current;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams();
      if (group === 'uncategorized') params.set('uncategorized', 'true');
      else if (group) params.set('group_id', group);
      params.set('kind', kind);
      params.set('query', query);
      params.set('offset', '0');
      setLoading(true);
      setLoadError('');
      void request<DirectoryResult>(`${apiBase}/document-tree?${params.toString()}`)
        .then(value => {
          if (cancelled || ticket !== generation.current) return;
          for (const file of value.files) entryById.current.set(file.id, file);
          setFiles(value.files);
          setTotal(value.total);
          setNextOffset(value.next_offset);
        })
        .catch(error => { if (!cancelled && ticket === generation.current) setLoadError(errorText(error)); })
        .finally(() => { if (!cancelled && ticket === generation.current) setLoading(false); });
    }, query ? 220 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiBase, group, kind, query, reload]);

  async function loadMore() {
    if (nextOffset === null || loading) return;
    const ticket = ++generation.current;
    const params = new URLSearchParams();
    if (group === 'uncategorized') params.set('uncategorized', 'true');
    else if (group) params.set('group_id', group);
    params.set('kind', kind);
    params.set('query', query);
    params.set('offset', String(nextOffset));
    setLoading(true);
    setLoadError('');
    try {
      const value = await request<DirectoryResult>(`${apiBase}/document-tree?${params.toString()}`);
      if (ticket !== generation.current) return;
      for (const file of value.files) entryById.current.set(file.id, file);
      setFiles(current => [...current, ...value.files]);
      setNextOffset(value.next_offset);
    } catch (error) {
      if (ticket === generation.current) setLoadError(errorText(error));
    } finally {
      if (ticket === generation.current) setLoading(false);
    }
  }

  function changeRole(next: MaterialRole) {
    setRole(next);
    setActionError('');
    if (next === 'base') setSelected(current => current.slice(-1));
  }

  function toggle(entry: DocumentEntry) {
    if (!entry.active_revision || busy) return;
    setActionError('');
    setSelected(current => {
      if (current.includes(entry.id)) return current.filter(id => id !== entry.id);
      if (role === 'base') return [entry.id];
      return [...current, entry.id];
    });
  }

  async function linkSelected() {
    if (!selectedEntries.length || busy) return;
    if (role === 'base' && selectedEntries.length > 1) {
      setActionError('底本只能选择一个文件，请先取消多余勾选。');
      return;
    }
    setBusy(true);
    setActionError('');
    let linked = 0;
    const failures: string[] = [];
    for (const entry of selectedEntries) {
      try {
        const detail = await request<EntryDetail>(`${apiBase}/document-entries/${encodeURIComponent(entry.id)}`);
        const pages = Math.max(0, Number(detail.pages) || 0);
        if (!detail.asset_id || !pages) throw new Error('该文件还没有可用正文，请等“处理任务”完成后再加入');
        await linkFileToProject(projectId, {
          editionId: entry.edition_id,
          assetId: detail.asset_id,
          pages,
          title: entry.title || entry.relative_path,
        }, role);
        linked += 1;
      } catch (error) {
        failures.push(`${entry.title || entry.relative_path}：${errorText(error)}`);
      }
    }
    if (linked) {
      try { await onChanged(); } catch { /* 列表刷新失败不影响已经写入的关联。 */ }
    }
    setBusy(false);
    if (failures.length) {
      setActionError(failures.join('；'));
      if (linked) onNotify(`已加入 ${linked} 份，${failures.length} 份失败`, 'error');
      else onNotify('没有文件加入项目，请检查提示后重试', 'error');
      return;
    }
    const name = selectedEntries.length === 1 ? (selectedEntries[0].title || selectedEntries[0].relative_path) : `${selectedEntries.length} 份文献`;
    onNotify(role === 'base' ? `已将《${name}》设为底本` : role === 'witness' ? `已将《${name}》设为参校本` : `已将《${name}》加入研究参考`, 'success');
    onClose();
  }

  async function importFiles() {
    if (importing || busy) return;
    setImporting(true);
    setActionError('');
    try {
      const result = await onImport(role);
      if (result) onClose();
      else if (result === false) setActionError('导入未完成，请按页面提示检查后重试');
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setImporting(false);
    }
  }

  const groupOptions = groups ? [
    { id: '', name: '全部分类', files: groups.total },
    ...(groups.uncategorized ? [{ id: 'uncategorized', name: '未分类', files: groups.uncategorized }] : []),
    ...groups.items.map(item => ({ id: item.id, name: item.name, files: item.files })),
  ] : [];

  return <Drawer
    title="选择项目文献"
    width={880}
    onClose={() => { if (!busy && !importing) onClose(); }}
    footer={<div className="psp-footer">
      <div className="psp-roles" role="group" aria-label="加入项目后的用途">
        {ROLE_OPTIONS.map(option => <button key={option.role} type="button" className={`psp-role ${role === option.role ? 'is-active' : ''}`} aria-pressed={role === option.role} disabled={busy || importing} onClick={() => changeRole(option.role)}>
          <strong>{option.label}</strong>
          <small>{option.hint}</small>
        </button>)}
      </div>
      {role === 'base' && baseSource && <p className="psp-hint">当前底本是《{baseSource.title}》，加入后将替换为新选择的文件。</p>}
      {actionError && <p className="inline-error" role="alert">{actionError}</p>}
      <div className="psp-actions">
        <span className="psp-count">已选 {selectedEntries.length} 个文件{selectedEntries.length === 1 ? ` · ${selectedEntries[0].title || selectedEntries[0].relative_path}` : ''}</span>
        <div className="action-row">
          <button type="button" className="button-secondary" disabled={busy || importing} onClick={() => void importFiles()}>{importing ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}{importing ? '导入中…' : '导入文件并加入'}</button>
          <button type="button" className="button-primary" disabled={!selectedEntries.length || busy || importing} onClick={() => void linkSelected()}>{busy ? <LoaderCircle size={16} className="spin" /> : null}{busy ? '正在加入…' : `加入项目（${selectedEntries.length}）`}</button>
        </div>
      </div>
    </div>}
  >
    <div className="psp-toolbar">
      {allowSystemLibrary ? <div className="psp-scope" role="tablist" aria-label="文献来源">
        <button role="tab" aria-selected={scope === 'personal'} className={scope === 'personal' ? 'is-active' : ''} onClick={() => setScope('personal')}>我的文献库</button>
        <button role="tab" aria-selected={scope === 'system'} className={scope === 'system' ? 'is-active' : ''} onClick={() => setScope('system')}>系统文献库</button>
      </div> : <span className="psp-scope-label">我的文献库</span>}
      <select aria-label="选择文献分类" value={group} onChange={event => { setGroup(event.target.value); setSelected([]); }}>
        {groupOptions.map(option => <option key={option.id || 'all'} value={option.id}>{option.name}（{option.files}）</option>)}
      </select>
      <select aria-label="文献类型" value={kind} onChange={event => { setKind(event.target.value); setSelected([]); }}>
        <option value="all">全部类型</option>
        <option value="ancient">古籍</option>
        <option value="papers">论文</option>
      </select>
    </div>
    <div className="psp-search">
      <Search size={16} />
      <input aria-label="搜索文献文件" placeholder="按题名或文件名查找" value={query} onChange={event => { setQuery(event.target.value); setSelected([]); }} />
      <span className="psp-total">{loading ? '读取中…' : `${total} 个文件`}</span>
    </div>
    {loadError && <p className="persistent-error" role="alert">{loadError}<button className="button-quiet" onClick={() => setReload(value => value + 1)}>重试</button></p>}
    {!loadError && <div className="psp-list" role="list">
      {files.map(entry => {
        const ready = Boolean(entry.active_revision);
        const checked = selected.includes(entry.id);
        const linked = linkedEditions.has(entry.edition_id);
        return <label key={entry.id} role="listitem" className={`psp-row ${checked ? 'is-selected' : ''} ${ready ? '' : 'is-disabled'}`}>
          <input type="checkbox" aria-label={`选择 ${entry.title || entry.relative_path}`} disabled={!ready || busy} checked={checked} onChange={() => toggle(entry)} />
          <FileText size={16} />
          <span className="psp-row-main">
            <strong>{entry.title || entry.relative_path.split('/').at(-1)}</strong>
            <small>{entry.relative_path}{entry.group_name ? ` · ${entry.group_name}` : ''}</small>
          </span>
          {linked && <span className="psp-badge">已在项目</span>}
          {!ready && <span className="psp-badge is-warn">正文处理中</span>}
          {entry.item_type === 'ancient' && ready && <span className="psp-badge is-quiet">古籍</span>}
        </label>;
      })}
      {!loading && !files.length && <p className="psp-empty">{query ? '没有找到匹配的文件' : '当前范围没有文件；可切换分类或导入文件'}</p>}
    </div>}
    {loading && <p className="psp-loading" role="status"><LoaderCircle size={16} className="spin" />正在读取文献…</p>}
    {nextOffset !== null && !loading && <button type="button" className="button-secondary psp-more" disabled={busy} onClick={() => void loadMore()}>加载更多</button>}
  </Drawer>;
}
