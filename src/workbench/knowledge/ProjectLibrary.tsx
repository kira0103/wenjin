import { useEffect, useState } from 'react';
import { Check, FileText, LibraryBig, Plus, Search, Upload, X } from 'lucide-react';
import PageHeading from '../PageHeading';
import { Empty } from '../Pages';
import { confirmDialog, confirmDiscard } from '../dialogQueue';
import type { Material, MaterialRole, Project } from '../api';
import { roleNames } from '../LibraryViews';
import ProjectSourcePicker from './ProjectSourcePicker';
import {
  ensureSoleBase,
  listProjectSources,
  patchProjectSource,
  removeProjectSource,
  setSourceAsBase,
  type ProjectSource,
} from './projectSourceApi';
import './projectLibrary.css';

type Tone = 'success' | 'error' | 'neutral';

export default function ProjectLibrary({
  project,
  materials,
  uploading,
  portal,
  onImport,
  onSelect,
  onChanged,
  onRegisterLeave,
  onPreprocess,
  onNotify,
}: {
  project: Project;
  materials: Material[];
  uploading: boolean;
  portal: boolean;
  onImport(role: MaterialRole): Promise<boolean | null>;
  onSelect(id: string): void;
  onChanged(): Promise<void>;
  onRegisterLeave(callback: (() => Promise<boolean>) | null): void;
  onPreprocess(): void;
  onNotify(message: string, tone?: Tone): void;
}) {
  const [sources, setSources] = useState<ProjectSource[]>([]);
  const [editing, setEditing] = useState<ProjectSource | null>(null);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');

  async function refresh() {
    setSources(await listProjectSources(project.id));
  }
  useEffect(() => { void refresh().catch(error => setError(error instanceof Error ? error.message : '项目材料读取失败')); }, [project.id, materials]);
  useEffect(() => {
    onRegisterLeave(async () => (!editing && !picking) || await confirmDiscard('项目材料有未完成的选择或修改。放弃并离开？'));
    return () => onRegisterLeave(null);
  }, [editing, picking, onRegisterLeave]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); await refresh(); await onChanged(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '操作失败，请重试'); }
    finally { setBusy(false); }
  }

  async function makeBase(source: ProjectSource) {
    if (source.roles.includes('base')) return;
    const current = sources.find(item => item.roles.includes('base'));
    const noun = isCollation ? '底本' : '原文';
    if (current && !await confirmDialog({ title: `替换${noun}？`, description: `当前${noun}为《${current.title}》。继续会把${noun}改为《${source.title}》。`, confirmLabel: `替换${noun}`, tone: 'danger' })) return;
    await act(async () => {
      await setSourceAsBase(project.id, source.id);
      onNotify(`已将《${source.title}》设为${noun}`, 'success');
    });
  }

  async function saveEditing() {
    if (!editing) return;
    const draft = editing;
    await act(async () => {
      const saved = await patchProjectSource(project.id, draft, {
        roles: draft.roles,
        mode: draft.mode,
        ranges: draft.mode === 'ranges' ? draft.ranges : [],
        witness_label: draft.witness_label,
        scope_note: draft.scope_note,
      });
      if (draft.roles.includes('base')) await ensureSoleBase(project.id, saved.id);
      setEditing(null);
      onNotify('项目用途与范围已保存', 'success');
    });
  }

  async function removeSource(source: ProjectSource) {
    if (!await confirmDialog({ title: `移出「${source.title}」？`, description: '只从本项目移除这份文献的使用范围；文献库原文件和其他项目不受影响。已有工作稿和任务输入仍保留。', confirmLabel: '移出', tone: 'danger' })) return;
    await act(async () => {
      await removeProjectSource(project.id, source.id);
      onNotify('已从项目移出这份文献', 'neutral');
    });
  }

  const baseSources = sources.filter(source => source.roles.includes('base'));
  const baseMaterials = materials.filter(material => material.roles?.includes('base'));
  const baseReady = baseMaterials.some(material => material.page_count > 0);
  const isCollation = project.project_type === 'collation';
  const visible = sources.filter(source => (source.title + ' ' + source.label + ' ' + source.scope_note).includes(filter));
  const pickerRole: MaterialRole = isCollation && !baseSources.length ? 'base' : 'reference';

  return <div className="page-container library-v2">
    <PageHeading
      title={<>项目材料</>}
      description={isCollation ? <>先指定底本（点校的原文依据），再按卷准备文本；参校本与出处可随后补充</> : <>从我的文献库或系统文献库选择本项目使用的版本和范围</>}
      actions={<div className="action-row">
        <button className="button-secondary" onClick={() => setPicking(true)}><Plus size={16} />{isCollation ? '选择底本 / 文献' : '从文献库选择'}</button>
        <button className="button-primary" disabled={uploading || busy} onClick={() => void onImport(pickerRole)}><Upload size={16} />{uploading ? '导入中…' : '导入并关联项目'}</button>
      </div>}
    />
    {error && <p className="persistent-error" role="alert">{error}</p>}
    {baseSources.length > 0 && <section className="prep-notice project-base-ready">
      <div>
        <strong>{isCollation ? '底本' : '原文'}：{baseSources.map(source => source.title).join('、')}</strong>
        <p className="scope-note">{isCollation
          ? (baseReady ? '已完成“添加底本”。下一步按卷拆分并清洗底文，之后可批量或逐卷点校。' : '文件已关联为底本，正文仍在后台解析；解析完成后即可准备卷文本（本页会自动刷新）。')
          : '已完成添加原文，可在“研究工作”中建立工作单元。'}</p>
      </div>
      {isCollation && (baseReady ? <button className="button-primary" onClick={onPreprocess}>下一步：准备卷文本</button> : <button className="button-secondary" disabled>等待正文解析…</button>)}
    </section>}
    {isCollation && !baseSources.length && sources.length > 0 && <p className="prep-notice">还没有设置底本。点下方任一材料的“设为底本”，或点“选择底本 / 文献”重新选择。</p>}
    {!!sources.length && <div className="library-filter-row">
      <label className="inline-search"><Search size={16} /><input aria-label="搜索项目材料" placeholder="题名、版本或用途说明" value={filter} onChange={event => setFilter(event.target.value)} />{!!filter && <button type="button" className="icon-btn" aria-label="清除搜索" onClick={() => setFilter('')}><X size={16} /></button>}</label>
      <span className="library-search-count">{filter ? `${visible.length} / ${sources.length} 条` : `${sources.length} 条`}</span>
    </div>}
    <div className="entry-list">{visible.map(source => {
      const files = materials.filter(material => material.entry_id === source.edition_id);
      const isBase = source.roles.includes('base');
      return <article className={`entry-card ${isBase ? 'is-base' : ''}`} key={source.id}>
        <div className="entry-header">
          <div>
            <h2>{source.title}{isBase && <span className="entry-flag">底本</span>}</h2>
            <p><span className="scope-note entry-version">{source.label || '版本待补'}</span>{source.roles.length ? ' · ' + source.roles.map(role => roleNames[role]).join('、') : ''}{source.witness_label ? ' · 参校本简称：' + source.witness_label : ''}</p>
          </div>
          {!isBase && <button className="button-secondary" disabled={busy} onClick={() => void makeBase(source)}><Check size={16} />{isCollation ? '设为底本' : '设为原文'}</button>}
          <button className="button-secondary" onClick={() => setEditing({ ...source, ranges: source.ranges.map(range => ({ ...range })) })}>设置用途与范围</button>
          <button className="button-danger" disabled={busy} onClick={() => void removeSource(source)}>移出这一范围</button>
        </div>
        <p className="scope-note">{source.mode === 'edition' ? '选用整个版本' : source.ranges.map(range => `${files.find(file => file.id.endsWith('~' + range.asset_id))?.filename || '附件'}：${range.page_from}–${range.page_to} 页 / 段`).join('；')}{source.scope_note ? ' · ' + source.scope_note : ''}</p>
        {files.filter(file => source.mode === 'edition' || source.ranges.some(range => file.id.endsWith('~' + range.asset_id))).map(file => <button className="entry-file-open" key={file.id} onClick={() => onSelect(file.id)}><FileText size={16} />{file.filename}</button>)}
        {!files.length && <p className="scope-note">仅有书目，尚未添加全文原件</p>}
      </article>;
    })}</div>
    {!sources.length && !error && <Empty title="还没有为项目选择文献" icon={LibraryBig}>
      <span>{isCollation ? '先指定底本：可从我的文献库或系统文献库选择，也可以直接导入文件并关联到本项目。' : '可从我的文献库或系统文献库选择，也可以直接导入文件并关联到本项目。'}</span>
      <div className="action-row">
        <button className="button-primary" onClick={() => setPicking(true)}>{isCollation ? '选择底本 / 文献' : '从文献库选择'}</button>
        <button className="button-secondary" disabled={uploading || busy} onClick={() => void onImport(pickerRole)}>导入文件并关联</button>
      </div>
    </Empty>}
    {!!sources.length && !visible.length && <Empty title="没有匹配的项目材料" icon={Search}>
      <span>清除搜索或更换关键词后再试</span>
      <button className="button-secondary" onClick={() => setFilter('')}>清除搜索</button>
    </Empty>}
    {editing && <div className="kb-editor">
      <h2>{editing.title} · 项目用途与范围</h2>
      <div className="kb-toolbar">{(Object.keys(roleNames) as MaterialRole[]).map(role => <label key={role}><input type="checkbox" checked={editing.roles.includes(role)} onChange={event => setEditing({ ...editing, roles: event.target.checked ? [...new Set([...editing.roles, role])] : editing.roles.filter(item => item !== role) })} />{roleNames[role]}</label>)}</div>
      <select aria-label="使用范围" value={editing.mode} onChange={event => {
        const mode = event.target.value as ProjectSource['mode'];
        const file = materials.find(material => material.entry_id === editing.edition_id);
        setEditing({ ...editing, mode, ranges: mode === 'ranges' && !editing.ranges.length && file ? [{ asset_id: file.id.split('~')[1], page_from: 1, page_to: file.page_count }] : editing.ranges });
      }}>
        <option value="edition">整个版本</option>
        <option value="ranges">指定附件与页段</option>
      </select>
      {editing.mode === 'ranges' && <>
        {editing.ranges.map((range, index) => <div className="kb-toolbar" key={index}>
          <select aria-label="范围附件" value={range.asset_id} onChange={event => setEditing({ ...editing, ranges: editing.ranges.map((item, itemIndex) => itemIndex === index ? { ...item, asset_id: event.target.value } : item) })}>{materials.filter(file => file.entry_id === editing.edition_id).map(file => <option key={file.id} value={file.id.split('~')[1]}>{file.filename}</option>)}</select>
          {(['page_from', 'page_to'] as const).map(key => <label key={key}>{key === 'page_from' ? '起始' : '结束'}<input type="number" min={1} value={range[key]} onChange={event => setEditing({ ...editing, ranges: editing.ranges.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: Number(event.target.value) } : item) })} /></label>)}
          <button className="button-quiet" onClick={() => setEditing({ ...editing, ranges: editing.ranges.filter((_, itemIndex) => itemIndex !== index) })}>移除此段</button>
        </div>)}
        <button className="button-secondary" onClick={() => {
          const file = materials.find(material => material.entry_id === editing.edition_id);
          if (file) setEditing({ ...editing, ranges: [...editing.ranges, { asset_id: file.id.split('~')[1], page_from: 1, page_to: file.page_count }] });
        }}>增加范围</button>
      </>}
      <label>参校本简称<input value={editing.witness_label} onChange={event => setEditing({ ...editing, witness_label: event.target.value })} /></label>
      <label>用途与范围说明<textarea value={editing.scope_note} onChange={event => setEditing({ ...editing, scope_note: event.target.value })} /></label>
      <p className="scope-note">勾选“底本 / 原文”会同时清理其他材料的底本标记；修改影响后续检索与新任务，已有工作稿和任务输入仍保留</p>
      <div className="action-row">
        <button className="button-primary" disabled={busy || !editing.roles.length} onClick={() => void saveEditing()}>保存</button>
        <button className="button-secondary" disabled={busy} onClick={() => setEditing(null)}>取消</button>
      </div>
    </div>}
    {picking && <ProjectSourcePicker
      projectId={project.id}
      sources={sources}
      initialRole={pickerRole}
      allowSystemLibrary={portal}
      onClose={() => setPicking(false)}
      onChanged={onChanged}
      onNotify={onNotify}
      onImport={onImport}
    />}
  </div>;
}
