import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { BookMarked, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Check, ChevronDown, ChevronRight, CircleAlert, Command, FileText, FolderKanban, History, Layers3, LibraryBig, LoaderCircle, Plus, Search, Settings2, ShieldCheck, X, type LucideIcon } from 'lucide-react';
import { api, request, artifactBody, type MaterialRole, type Project, type Bootstrap, type MaterialDetail, type ModelSettings, type SourceContext, type TaskArtifact, type TaskKind, type WorkArtifact, type WorkTask, taskNames } from './api';
import { materialView, readSession, saveSession, searchMaterials, sourceInfo, type MaterialView } from './viewState';
import { GlobalDocument, projectNames } from './LibraryViews';
import { ProjectOverview, ProjectConfigDialog, CollationWorkspace, ResearchList, ResearchEditor, WorkResults } from './ResearchViews';
import { BatchHistory } from './BatchViews';
import { CollationDebug } from './CollationDebug';
import { ArtifactsPage, SettingsPage, TaskPage, Workspace, Modal } from './Pages';
import KnowledgeSettings from './knowledge/KnowledgeSettings';
import KnowledgeWorkbench from './knowledge/KnowledgeWorkbench';
import ProjectLibrary from './knowledge/ProjectLibrary';
import type { LibraryScope } from './knowledge/documentTypes';
import WorkspaceStorage from './WorkspaceStorage';
import SubmissionWorkbench, { type SubmissionDraft } from './SubmissionWorkbench';
import ProjectAssistantPanel from './knowledge/ProjectAssistantPanel';
import AppearanceSettings from './AppearanceSettings';
import PanelDivider from './PanelDivider';
import {applyTheme,readTheme,saveTheme,readLayout,saveLayout} from './appearance';

type Page = 'research-assistant' | 'submissions' | 'knowledge' | 'collation-debug' | 'collation-batch' | 'volume-preparation' | 'global-library' | 'project-materials' | 'research' | 'home' | 'library' | 'materials' | 'tasks' | 'artifacts' | 'settings';
const titles: Record<Page, string> = { 'research-assistant': '研究助手', submissions: '论文投稿管理', knowledge: '文献库', 'collation-debug': '点校调试', 'collation-batch': '批量点校', 'volume-preparation': '准备卷文本', 'global-library': '文献库', 'project-materials': '项目材料', research: '研究工作', home: '项目概览', library: '文献库', materials: '材料阅读', tasks: '任务记录', artifacts: '工作成果', settings: '模型与设置' };
function errorText(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试'; }

export default function App() {
  const [session, setSession] = useState(readSession);
  const projectId = session.projectId;
  const [page, setPageState] = useState<Page>(() => { const saved = session.pageByProject?.[session.projectId] as Page; return saved==='research-assistant'?'home':saved in titles?saved:'home'; });
  const [theme,setTheme]=useState(readTheme),[layout,setLayout]=useState(readLayout),[viewport,setViewport]=useState(innerWidth),[globalAssistant,setGlobalAssistant]=useState(false);
  useEffect(()=>{applyTheme(theme);const match=matchMedia('(prefers-color-scheme: dark)'),sync=()=>{if(theme==='system')applyTheme(theme);};match.addEventListener('change',sync);return()=>match.removeEventListener('change',sync);},[theme]);
  useEffect(()=>{saveLayout(layout);},[layout]);
  useEffect(()=>{const resize=()=>setViewport(innerWidth);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[]);
  const [data, setData] = useState<Bootstrap | null>(null);
  const [startup,setStartup]=useState({stage:'starting',message:'正在启动资料服务',elapsed:0});
  useEffect(()=>{if(data||!window.desktop.serviceStatus)return;let live=true;const check=()=>void window.desktop.serviceStatus!().then(value=>{if(live)setStartup(value);}).catch(()=>{});check();const timer=setInterval(check,500);return()=>{live=false;clearInterval(timer);};},[!!data]);
  const [selectedId, setSelectedId] = useState<string | null>(session.selectedByProject[projectId] || null);
  const [detail, setDetail] = useState<MaterialDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);

  const [taskCache, setTaskCache] = useState<Record<string, WorkTask>>({});
  const [taskRevision, setTaskRevision] = useState(0);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [artifactId, setArtifactId] = useState<string | null>(null);
  const [, setFilter] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [toast, setToast] = useState('');
  const [serviceError, setServiceError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<ModelSettings & { api_key?: string }>({ base_url: '', model: '', provider_name: '兼容接口', api_key: '' });
  const [dirtySettings, setDirtySettings] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [testing, setTesting] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [projectMenu, setProjectMenu] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [projectDialog, setProjectDialog] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectType, setProjectType] = useState<Project['project_type']>('general');
  const [projectConfigOpen, setProjectConfigOpen] = useState(false);
  const [unitId, setUnitId] = useState<string | null>(session.unitByProject?.[session.projectId] || null);
  const [batchRun, setBatchRun] = useState<string | undefined>();
  const settingsReturn = useRef<Page>(page === 'settings' ? 'home' : page);
  const debugReturn = useRef<Page>(page === 'collation-debug' ? 'collation-batch' : page);
  const [debugAttempt, setDebugAttempt] = useState<string | undefined>();
  const [globalReaderId, setGlobalReaderId] = useState<string | null>(null);
  const [globalSourceDetail,setGlobalSourceDetail]=useState<MaterialDetail|null>(null);
  const leaveWork = useRef<(() => Promise<boolean>) | null>(null);
  const navigationVersion = useRef(0);
  const registerLeave = useCallback((callback: (() => Promise<boolean>) | null) => { leaveWork.current = callback; }, []);
  const [creatingProject, setCreatingProject] = useState(false);
  const [settingsSection, setSettingsSection] = useState<'model'|'data'|'assistants'|'retrieval'|'catalog'|'appearance'>('model');
  const [libraryReset,setLibraryReset]=useState(0);
  const [libraryIncoming, setLibraryIncoming] = useState<{id:string;asset?:string;page?:number}|null>(null);
  const clearLibraryIncoming = useCallback(() => setLibraryIncoming(null), []);
  const [assistantDraft, setAssistantDraft] = useState<{query:string;id?:string;filter?:LibraryScope}|null>(null);
  const clearAssistantDraft=useCallback(()=>setAssistantDraft(null),[]);
  const [submissionDraft, setSubmissionDraft] = useState<SubmissionDraft | null>(null);
  const clearSubmissionDraft = useCallback(() => setSubmissionDraft(null), []);
  const [pendingTool, setPendingTool] = useState<TaskKind | 'references' | null>(null);
  const projectRef = useRef(projectId);
  const dirtyRef = useRef(false);
  const requestVersion = useRef(0);
  const settingsVersion = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  projectRef.current = projectId;

  const globalMaterials = data?.global_materials || [];
  const units = data?.units || [];
  const materials = (data?.materials || []).filter(m => m.project_id === projectId);
  const tasks = (data?.tasks || []).filter(t => t.project_id === projectId);
  const artifacts = (data?.artifacts || []).filter(a => a.project_id === projectId);
  const material = materials.find(m => m.id === selectedId);
  const currentProject = data?.projects.find(p => p.id === projectId);
  const assistantVisible=!!currentProject&&layout.rightOpen&&(!['settings','submissions','knowledge','global-library'].includes(page)||globalAssistant&&['knowledge','global-library'].includes(page));
  const leftWidth=Math.min(layout.leftWidth,Math.max(184,viewport-(assistantVisible?320:0)-496));
  const rightMaximum=Math.max(320,viewport-(layout.leftOpen?leftWidth+8:0)-488);
  const rightWidth=Math.min(layout.rightWidth,rightMaximum);
  const gridStyle={'--left-width':layout.leftOpen?leftWidth+'px':'0px','--left-divider':layout.leftOpen?'8px':'0px','--right-width':assistantVisible?rightWidth+'px':'0px','--right-divider':assistantVisible?'8px':'0px'} as CSSProperties;
  function openAssistant(){if(page==='settings'||page==='submissions')void navigate('home');setGlobalAssistant(true);setLayout(v=>({...v,rightOpen:true}));}
  const view = materialView(selectedId ? session.views[`${projectId}:${selectedId}`] || session.views[selectedId] : undefined, material?.page_count || 1);
  const materialTasks = tasks.filter(t => t.material_id === selectedId);
  const taskId = view.taskId && materialTasks.some(t => t.id === view.taskId) ? view.taskId : materialTasks[0]?.id;
  const materialTask = taskId && taskCache[taskId]?.material_id === selectedId && taskCache[taskId]?.project_id === projectId ? taskCache[taskId] : null;
  const openTask = openTaskId ? taskCache[openTaskId] || tasks.find(t => t.id === openTaskId) : undefined;
  const openArtifact = artifacts.find(a => a.id === artifactId);
  const currentDetail = detail?.id === selectedId ? detail : null;

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    const result = await api.bootstrap(projectId);
    if (projectRef.current !== projectId || version !== requestVersion.current) return;
    if (!result.projects.some(p => p.id === projectId && !p.archived)) {
      const fallback = result.projects.find(p => !p.archived);
      if (fallback) { projectRef.current = fallback.id; setSession(s => ({ ...s, projectId: fallback.id })); setPageState('home'); setUnitId(null); setSelectedId(null); return; }
    }
    setData(result); setServiceError('');
    if (!dirtyRef.current) setSettings({ ...result.settings, api_key: '' });
    setSelectedId(current => result.materials.some(m => m.id === current && m.project_id === projectId) ? current : result.materials[0]?.id || null);
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    let working = false;
    const boot = async () => {
      if (working) return;
      working = true;
      try { await refresh(); }
      catch (error) { if (!cancelled && projectRef.current === projectId) setServiceError(errorText(error)); }
      finally { working = false; }
    };
    void boot();
    const unsubscribe = window.desktop.onServiceReady(() => { void boot(); });
    const interval = window.setInterval(() => { void boot(); }, 4200);
    return () => { cancelled = true; ++requestVersion.current; unsubscribe(); window.clearInterval(interval); };
  }, [projectId, refresh]);



  useEffect(() => { saveSession(session); }, [session]);
  useEffect(() => { setSession(s => s.pageByProject?.[projectId] === page && (!unitId || s.unitByProject?.[projectId] === unitId) ? s : { ...s, pageByProject: { ...s.pageByProject, [projectId]: page }, unitByProject: unitId ? { ...s.unitByProject, [projectId]: unitId } : s.unitByProject }); }, [page, projectId, unitId]);
  useEffect(() => {
    if (selectedId) setSession(s => s.selectedByProject[projectId] === selectedId ? s : { ...s, selectedByProject: { ...s.selectedByProject, [projectId]: selectedId } });
  }, [selectedId, projectId]);

  useEffect(() => {
    setDetail(null); setDetailError('');
    if (!selectedId) return;
    let cancelled = false;
    void api.material(selectedId).then(value => { if (!cancelled) setDetail(value); }).catch(error => { if (!cancelled) setDetailError(errorText(error)); });
    return () => { cancelled = true; };
  }, [selectedId, detailRevision]);

  const observedId = page === 'tasks' ? openTaskId : page === 'materials' ? taskId : null;
  const observedStatus = tasks.find(t => t.id === observedId)?.status;
  useEffect(() => {
    if (!observedId) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const value = await api.task(observedId);
        if (cancelled || value.project_id !== projectRef.current) return;
        setTaskCache(cache => ({ ...cache, [value.id]: value }));
        if (['running', 'queued'].includes(value.status)) timer = window.setTimeout(() => { void load(); }, 1300);
      } catch (error) { if (!cancelled) setToast(errorText(error)); }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [observedId, observedStatus, projectId, taskRevision]);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(v => !v); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f' && ['library', 'project-materials', 'global-library'].includes(page)) { event.preventDefault(); document.querySelector<HTMLInputElement>('.inline-search input')?.focus(); }
      if (event.key === 'Escape') { setSearchOpen(false); setProjectMenu(false); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [page]);
  useEffect(() => { if (searchOpen) searchRef.current?.focus(); }, [searchOpen]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 6500); return () => window.clearTimeout(timer); }, [toast]);

  function updateView(patch: Partial<MaterialView>, id = selectedId, pages = material?.page_count || 1) {
    if (!id) return;
    setSession(s => ({ ...s, views: { ...s.views, [`${projectId}:${id}`]: { ...materialView(s.views[`${projectId}:${id}`] || s.views[id], pages), ...patch } } }));
  }
  async function navigate(target: Page) {
    if(target==='research-assistant'){openAssistant();return true;}
    const version = ++navigationVersion.current;
    if (leaveWork.current && !(await leaveWork.current())) return false;
    if (version !== navigationVersion.current) return false;
    if (target === 'settings' && page !== 'settings') settingsReturn.current = page;
    setPageState(target); return true;
  }
  async function changed() { await refresh(); setDetailRevision(v => v + 1); }
  async function openUnit(id?: string) { if (!(await navigate('research'))) return; setUnitId(id || null); }
  async function openPreparation() { if (!(await navigate('volume-preparation'))) return; setUnitId(null); }
  async function openBatch(run?: string) { if (!(await navigate('collation-batch'))) return; setUnitId(null); setBatchRun(run); }
  async function openDebug(attempt?: string) { debugReturn.current = page; setDebugAttempt(attempt); await navigate('collation-debug'); }
  async function openResearch() { if (currentProject?.project_type === 'collation' && !units.length) await openPreparation(); else await openUnit(); }
  async function archiveProject() {
    if (leaveWork.current && !(await leaveWork.current())) return;
    await api.archiveProject(projectId);
    const next = data?.projects.find(p => p.id !== projectId && !p.archived);
    setProjectConfigOpen(false);
    if (next) await switchProject(next.id);
    await changed();
  }
  async function deleteProject(preview: import('./DeleteDialogs').DeletePreview) {
    if (leaveWork.current && !(await leaveWork.current())) return;
    const result = await api.deleteProject(preview.id, preview.name, preview.preview_token);
    setProjectConfigOpen(false);
    localStorage.removeItem(`shixue-debug-${preview.id}`); localStorage.removeItem(`shixue-batch-${preview.id}`);
    await switchProject(result.next_project_id); await changed();
    setToast(result.cleanup_errors.length ? '项目已删除：' + result.cleanup_errors.join('；') : '项目已删除，文献库原文件保留');
  }
  async function restoreProject(id: string) { try { await api.restoreProject(id); await changed(); } catch (error) { setToast(errorText(error)); } }
  async function configureProject(project: Project) { await api.configureProject(project); await changed(); setProjectConfigOpen(false); }
  async function switchProject(id: string) {
    if (id === projectId) { setProjectMenu(false); return; }
    if (!(await navigate('home'))) return;
    setAssistantDraft(null);setGlobalAssistant(false);setUnitId(null); setGlobalReaderId(null); setBatchRun(undefined); settingsReturn.current = 'home';
    projectRef.current = id; ++requestVersion.current;
    setData(null); setSelectedId(session.selectedByProject[id] || null); setDetail(null);
    setOpenTaskId(null); setArtifactId(null); setFilter(''); setSearch(''); setSearchOpen(false); setPendingTool(null);
    setSession(s => ({ ...s, projectId: id })); navigate('home'); setProjectMenu(false);
  }
  async function selectMaterial(id: string, tool = pendingTool) {
    if (!(await navigate('materials'))) return;
    const selected = materials.find(m => m.id === id);
    if (!selected) return;
    setSelectedId(id); navigate('materials'); setSearchOpen(false); setPendingTool(null);
    if (tool === 'references') updateView({ tab: 'references', sideOpen: false }, id, selected.page_count);
    else if (tool) updateView({ taskKind: tool, tab: 'reader', sideOpen: true }, id, selected.page_count);
  }
  async function doImport(entryId?: string, destinationProject: string | null = projectId, role: MaterialRole = 'reference') {
    if (uploading) return;
    const importProject = destinationProject;
    try {
      const files = await (window.desktop.pickDocuments || window.desktop.pickPdf)();
      if (!files.length) return;
      setUploading(true);
      let latest: string | null = null;
      const failures: string[] = [];
      let count = 0;
      for (const file of files) {
        try { const imported = await api.importDocument(file.data, file.name, importProject || undefined, role, entryId); latest = imported.id; count++; }
        catch (error) { failures.push(`${file.name}：${errorText(error)}`); }
      }
      await changed();
      if (importProject === projectRef.current && latest) {
        setSelectedId(latest);
        if (pendingTool) { const imported = await api.material(latest); updateView(pendingTool === 'references' ? { tab: 'references', sideOpen: false } : { taskKind: pendingTool, sideOpen: true }, latest, imported.page_count); setPendingTool(null); await navigate('materials'); }
        else await navigate('project-materials');
      }
      setToast(failures.length ? `已导入 ${count} 份；${failures.join('；')}` : `已保存 ${count} 份资料${importProject ? '并关联目标项目' : '到文献库'}`);
    } catch (error) { setToast(errorText(error)); }
    finally { setUploading(false); }
  }
  async function createProject() {
    if (!projectName.trim() || creatingProject) return;
    setCreatingProject(true);
    try { const value = await api.createProject({ name: projectName.trim(), project_type: projectType }); await switchProject(value.id); setProjectDialog(false); setProjectName(''); }
    catch (error) { setToast(errorText(error)); }
    finally { setCreatingProject(false); }
  }
  async function runTask() {
    if (busy || !material || !currentDetail || serviceError) return;
    const info = sourceInfo(currentDetail, view);
    const problem = !data?.settings.configured ? '请先保存模型连接设置' : view.pageFrom > view.pageTo ? '起始页不能大于结束页' : view.sourceMode === 'paste' ? !view.pastedSource.trim() ? '请粘贴需要处理的原文' : view.pastedSource.length > 30000 ? '粘贴原文超过 30,000 字，请分批处理' : '' : !info.hasText ? '所选页面没有文字，请切换到粘贴原文' : info.characters > 42000 ? '所选文字超过 42,000 字，请缩小页码范围' : '';
    if (problem) { setToast(problem); return; }
    const runProject = projectId, runMaterial = material.id;
    setBusy(true);
    try {
      const created = await api.createTask({ project_id: runProject, material_id: runMaterial, task_type: view.taskKind, instructions: view.message.trim(), target_language: view.targetLanguage.trim() || '中文', source_text: view.sourceMode === 'paste' ? view.pastedSource.trim() : '', page_from: view.pageFrom, page_to: view.pageTo });
      setTaskCache(cache => ({ ...cache, [created.id]: created }));
      updateView({ taskId: created.id, message: '', sideOpen: true }, runMaterial, material.page_count);
      if (projectRef.current === runProject) await refresh();
      setToast(`${taskNames[view.taskKind]}任务已提交，可在任务记录中查看`);
    } catch (error) { setToast(errorText(error)); }
    finally { setBusy(false); }
  }
  async function retryTask(id: string) {
    try { await api.resumeTask(id); setTaskCache(cache => { const next = { ...cache }; delete next[id]; return next; }); setTaskRevision(v => v + 1); await refresh(); setToast('任务已重新执行'); }
    catch (error) { setToast(errorText(error)); }
  }
  async function saveSettings() {
    if (savingSettings) return;
    const version = settingsVersion.current;
    setSavingSettings(true);
    try {
      const { api_key, ...configuration } = settings;
      const saved = await api.saveSettings({ ...configuration, ...(api_key ? { api_key } : {}) });
      if (version === settingsVersion.current) { dirtyRef.current = false; setDirtySettings(false); setSettings({ ...saved, api_key: '' }); }
      setConnectionResult(null); await refresh(); setToast('模型设置已保存，可测试连接');
    } catch (error) { setToast(errorText(error)); }
    finally { setSavingSettings(false); }
  }
  async function testConnection() {
    if (dirtySettings || testing) return;
    setTesting(true); setConnectionResult(null);
    const version = settingsVersion.current;
    try { const result = await api.testConnection(); if (version === settingsVersion.current) setConnectionResult({ ok: true, text: `连接测试成功：${result.model}` }); }
    catch (error) { if (version === settingsVersion.current) setConnectionResult({ ok: false, text: errorText(error) }); }
    finally { setTesting(false); }
  }
  async function referenceAction(id: string, confirm = false) {
    try { if (confirm) await api.confirmReferenceVersion(id); else await api.addToLibrary(id); setDetailRevision(v => v + 1); await changed(); setToast(confirm ? '已记录人工版本核验' : '已加入本机共享书目库，仍需核验版本'); }
    catch (error) { setToast(errorText(error)); throw error; }
  }
  async function showSource(materialId: string | null | undefined, context?: SourceContext) {
    if (!materialId) { setToast('该成果来自粘贴文本，没有关联 PDF'); return; }
    try {
      const source = await api.material(materialId);
      if(source.kind==='working_input'||!globalMaterials.some(m=>m.id===source.id)) {if(await navigate('global-library')){setGlobalSourceDetail(source);setGlobalReaderId(source.id);}return;}
      const sourceProject = source.project_ids?.includes(projectId) ? projectId : undefined;
      if (!sourceProject) { if (await navigate('global-library')) setGlobalReaderId(source.id); return; }
      if (sourceProject !== projectId) await switchProject(sourceProject);
      setSelectedId(source.id); navigate('materials');
      setSession(s => ({ ...s, selectedByProject: { ...s.selectedByProject, [sourceProject]: source.id }, views: { ...s.views, [`${sourceProject}:${source.id}`]: { ...materialView(s.views[`${sourceProject}:${source.id}`] || s.views[source.id], source.page_count), readerPage: context?.page_from || Number(/第\s*(\d+)/.exec(context?.selection || '')?.[1]) || 1, tab: 'reader' } } }));
    } catch (error) { setToast(errorText(error)); }
  }
  async function exportArtifact(artifact: WorkArtifact | TaskArtifact) {
    try {
      const body = 'body' in artifact ? artifactBody(artifact.body) : artifact;
      const context = body.source_context;
      const content = `# ${artifact.title}\n\n${context?.material_title ? `材料：${context.material_title}\n\n` : ''}${context?.selection ? `处理范围：${context.selection}\n\n` : ''}${context?.model ? `模型：${context.model}\n\n` : ''}${context?.missing_pages?.length ? `未提取到文字的页码：${context.missing_pages.join('、')}\n\n` : ''}请对照原文复核 AI 结果。\n\n${body.content}`;
      const filename = `${artifact.title.replace(/[<>:"/\\|?*]/g, '_') || '研究成果'}.md`;
      const saved = await window.desktop.saveFile({ filename, data: new TextEncoder().encode(content).buffer });
      if (saved) setToast('成果已导出');
    } catch (error) { setToast(errorText(error)); }
  }
  function openTaskRecord(id: string) { setOpenTaskId(id); navigate('tasks'); }
  function openArtifactRecord(artifact: WorkArtifact) { setArtifactId(artifact.id); navigate('artifacts'); }
  async function showTaskArtifact(task: WorkTask) {
    const artifact = artifacts.find(a => a.task_id === task.id);
    if (artifact) { openArtifactRecord(artifact); return; }
    try {
      const next = await api.artifacts(task.project_id);
      if (projectRef.current !== task.project_id) return;
      const result = next.find(a => a.task_id === task.id);
      if (!result) { setToast('此任务尚未生成成果，请在任务记录中查看状态'); return; }
      setData(current => current ? { ...current, artifacts: next } : current);
      openArtifactRecord(result);
    } catch (error) { setToast(errorText(error)); }
  }

  return <div className="app-shell" style={gridStyle}>
    <header className="app-titlebar"><div className="titlebar-start"><button className="icon-btn" aria-label={layout.leftOpen?'折叠导航':'展开导航'} onClick={()=>setLayout(v=>({...v,leftOpen:!v.leftOpen}))}>{layout.leftOpen?<PanelLeftClose size={18}/>:<PanelLeftOpen size={18}/>}</button><BookMarked size={17}/><span>史学工作台</span></div><span className="titlebar-project">{currentProject?.name||'研究工作区'}</span><div className="titlebar-end"><button className="button-quiet" aria-label="研究助手" aria-expanded={assistantVisible} onClick={()=>{if(assistantVisible)setLayout(v=>({...v,rightOpen:false}));else openAssistant();}}>{assistantVisible?<PanelRightClose size={17}/>:<PanelRightOpen size={17}/>}<span>研究助手</span></button></div></header>
    <aside className="sidebar" aria-label="工作台导航" hidden={!layout.leftOpen}>

      <div className="project-heading"><span>研究项目</span><button className="icon-btn subtle" aria-label="新建研究项目" onClick={() => setProjectDialog(true)}><Plus size={16} /></button></div>
      <button className="project-switch" aria-expanded={projectMenu} onClick={() => setProjectMenu(v => !v)}><FolderKanban size={16} /><span>{currentProject?.name || '正在加载项目…'}</span><ChevronDown size={14} /></button>
      {projectMenu && <div className="project-menu">{data?.projects.filter(p => !p.archived).map(p => <button key={p.id} onClick={() => switchProject(p.id)}><FolderKanban size={14} /><span>{p.name}</span>{p.id === projectId && <Check size={14} />}</button>)}<button onClick={() => { setProjectMenu(false); setProjectDialog(true); }}><Plus size={14} />新建研究项目</button><button onClick={() => setShowArchived(v => !v)}>已归档项目 {data?.projects.filter(p => p.archived).length || 0}</button>{showArchived && data?.projects.filter(p => p.archived).map(p => <button key={p.id} onClick={() => void restoreProject(p.id)}><History size={14} /><span>{p.name}</span>恢复</button>)}</div>}
      <nav className="nav-list"><NavItem icon={LibraryBig} label="文献库" active={page === 'global-library' || page === 'knowledge'} onClick={() => void (async()=>{if(await navigate('global-library')){setGlobalAssistant(false);setGlobalReaderId(null);setLibraryReset(v=>v+1);}})()} /><NavItem icon={FileText} label="论文投稿管理" active={page === 'submissions'} onClick={() => void navigate('submissions')} /><div className="nav-scope-label">当前项目</div><NavItem icon={Command} label="项目概览" active={page === 'home'} onClick={() => void navigate('home')} /><NavItem icon={LibraryBig} label="项目材料" active={page === 'project-materials' || page === 'library'} count={materials.length} onClick={() => { setPendingTool(null); void navigate('project-materials'); }} /><NavItem icon={FileText} label={currentProject?.project_type === 'collation' ? '古籍点校' : '研究工作'} active={page === 'research' || page === 'volume-preparation' || page === 'collation-batch' || page === 'collation-debug'} count={units.length} onClick={() => void openResearch()} /><NavItem icon={Layers3} label="工作成果" active={page === 'artifacts'} count={artifacts.length + units.length} onClick={() => { setArtifactId(null); void navigate('artifacts'); }} /><NavItem icon={History} label="执行记录" active={page === 'tasks'} count={tasks.filter(t => ['running', 'queued', 'failed', 'paused'].includes(t.status)).length} onClick={() => { setOpenTaskId(null); void navigate('tasks'); }} /></nav>

      <div className="sidebar-heading"><span>最近材料</span><button className="icon-btn subtle" aria-label="导入项目资料" disabled={uploading} onClick={() => void doImport()}><Plus size={16} /></button></div>
      <div className="material-nav-list">{[...materials].sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId)).slice(0, 8).map(m => <button key={m.id} title={m.title} aria-current={page === 'materials' && selectedId === m.id ? 'page' : undefined} className={`material-nav ${page === 'materials' && selectedId === m.id ? 'material-nav-active' : ''}`} onClick={() => selectMaterial(m.id)}><FileText size={15} /><span>{m.title}</span></button>)}{!materials.length && <div className="sidebar-empty">尚未添加材料</div>}<button className="sidebar-all button-quiet" onClick={() => { setFilter(''); void navigate('project-materials'); }}>查看项目材料 <ChevronRight size={14} /></button></div>
      <div className="sidebar-bottom"><div className="storage-meter"><ShieldCheck size={16} /><div><strong>本机工作区</strong><small>原件、工作稿与成果保存在本机</small></div></div><NavItem icon={Settings2} label="模型与设置" active={page === 'settings'} onClick={() => navigate('settings')} /></div>
    </aside>
    {layout.leftOpen&&<PanelDivider side="left" width={leftWidth} max={Math.min(320,Math.max(184,viewport-(assistantVisible?rightWidth+8:0)-488))} onWidth={v=>setLayout(s=>({...s,leftWidth:v}))}/>}
    <main className="main-column">
      <header className="topbar"><div className="breadcrumbs"><button className="breadcrumb-project" onClick={() => navigate('home')}>{['submissions','research-assistant','global-library','knowledge'].includes(page) ? '全局工作区' : currentProject?.name || '研究项目'}</button><ChevronRight size={14} /><span className="breadcrumb-current">{page === 'materials' ? material?.title || titles[page] : currentProject?.project_type === 'collation' && (page === 'research' || page === 'volume-preparation' || page === 'collation-batch' || page === 'collation-debug') ? '古籍点校' : (page === 'volume-preparation' || page === 'collation-batch') ? '研究工作' : titles[page]}</span></div><div className="topbar-actions"><button className="search-trigger" aria-expanded={searchOpen} onClick={() => setSearchOpen(v => !v)}><Search size={15} /><span>搜索项目材料</span><kbd>Ctrl K</kbd></button><button className="provider-chip" onClick={() => navigate('settings')} title="打开模型设置">{data?.settings.configured ? `${data.settings.model} · 已配置` : '配置模型'}</button></div></header>
      {searchOpen && <div className="search-popover" role="dialog" aria-label="搜索当前项目材料"><Search size={16} /><input ref={searchRef} aria-label="搜索题名或文件名" placeholder="搜索题名或文件名…" value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { const found = searchMaterials(materials, search)[0]; if (found) selectMaterial(found.id); } }} /><button className="icon-btn" aria-label="关闭搜索" onClick={() => setSearchOpen(false)}><X size={16} /></button><div className="search-results">{searchMaterials(materials, search).map(m => <button key={m.id} onClick={() => selectMaterial(m.id)}><FileText size={15} /><span>{m.title}</span><span className="search-result-meta">{m.page_count} 页</span></button>)}{!searchMaterials(materials, search).length && <p className="search-empty">没有符合条件的材料。请尝试其他题名或文件名。</p>}</div></div>}
      {serviceError && <div className="service-error" role="alert"><CircleAlert size={16} /><span>资料服务暂时不可用：{serviceError}</span><button className="button-quiet" onClick={() => { void refresh().catch(error => setServiceError(errorText(error))); }}>重新连接</button></div>}
      {data?.catalog_error && <div className="service-error" role="alert"><CircleAlert size={16}/><span>{data.catalog_error}</span><button className="button-quiet" onClick={() => void navigate("global-library")}>打开文献库状态</button></div>}
      <div className="content-scroll">
        {!data ? <div className="page-loading" role={startup.stage==='failed'?'alert':'status'}><LoaderCircle className="spin" size={22} />{startup.stage==='ready'?'正在打开项目资料':startup.message}{startup.elapsed>=3&&<span> · {startup.elapsed} 秒</span>}</div> : <>
      {page === 'submissions' && <SubmissionWorkbench projects={data.projects} draft={submissionDraft} onDraftUsed={clearSubmissionDraft} onRegisterLeave={registerLeave} />}
      {page === 'home' && currentProject && <ProjectOverview activity={data.collation_activity} onBatch={() => { if (data.collation_activity) localStorage.setItem(`shixue-batch-${projectId}`, data.collation_activity.id); void openBatch(); }} preparedCount={data.prepared_material_count || 0} onPreprocess={() => void openPreparation()} selectedUnitId={session.unitByProject?.[projectId]} project={currentProject} materials={materials} units={units} tasks={tasks} onWork={id => void (id ? openUnit(id) : openResearch())} onMaterials={() => void navigate('project-materials')} onImport={() => void doImport(undefined, projectId, currentProject.project_type === 'collation' && !materials.some(m => m.roles?.includes('base')) ? 'base' : 'reference')} onConfigure={() => setProjectConfigOpen(true)} onTask={openTaskRecord} />}
          {(page === 'project-materials' || page === 'library') && currentProject && <ProjectLibrary onRegisterLeave={registerLeave} key={projectId} project={currentProject} materials={materials} uploading={uploading} onImport={() => doImport(undefined, projectId)} onSelect={id => void selectMaterial(id)} onChanged={changed} onLibrary={() => { setGlobalReaderId(null); void navigate('global-library'); }} />}
          {page === 'global-library' && globalReaderId && (globalMaterials.find(m => m.id === globalReaderId) || globalSourceDetail?.id === globalReaderId) && <GlobalDocument key={globalReaderId} material={(globalMaterials.find(m => m.id === globalReaderId) || globalSourceDetail)!} onBack={() => setGlobalReaderId(null)} onAdd={() => void navigate('project-materials')} />}
          {page === 'collation-debug' && currentProject && <CollationDebug key={projectId} project={currentProject} materials={materials} attemptId={debugAttempt} onBack={() => void navigate(debugReturn.current)} onSettings={() => void navigate('settings')} />}
          {(page === 'research' || page === 'volume-preparation' || page === 'collation-batch') && currentProject && (
            page === 'research' && unitId ? <ResearchEditor key={projectId + unitId} id={unitId} project={currentProject} materials={materials} tasks={tasks} onBack={() => void openUnit()} onChanged={changed} registerLeave={registerLeave} onTask={openTaskRecord} onDebug={() => void openDebug()} /> :
            currentProject.project_type === 'collation' ? <CollationWorkspace key={projectId} project={currentProject} units={units} materials={materials} stage={page === 'volume-preparation' ? 'prepare' : page === 'collation-batch' ? 'batch' : 'drafts'} batchRun={batchRun} onBatch={run => void openBatch(run)} onSettings={() => void navigate('settings')} onDebug={id => void openDebug(id)} onPrepare={() => void openPreparation()} onDrafts={() => void openUnit()} onSelect={id => void openUnit(id)} onCreated={async unit => { await changed(); await openUnit(unit.id); }} onMaterials={() => void navigate('project-materials')} /> :
            <ResearchList project={currentProject} units={units} materials={materials} onPreprocess={() => void openPreparation()} onSelect={id => void openUnit(id)} onCreated={async unit => { await changed(); await openUnit(unit.id); }} onMaterials={() => void navigate('project-materials')} />
          )}
          {page === 'materials' && <Workspace material={material} detail={currentDetail} error={detailError} onReload={() => setDetailRevision(v => v + 1)} view={view} updateView={updateView} task={materialTask} tasks={materialTasks} onTask={openTaskRecord} onResult={showTaskArtifact} onSource={showSource} configured={!!data.settings.configured} serviceReady={!serviceError} onSettings={() => navigate('settings')} onBack={() => { setFilter(''); void navigate('project-materials'); }} onImport={() => void doImport()} uploading={uploading} onRun={runTask} busy={busy} onReference={referenceAction} onExport={exportArtifact} />}
          {page === 'tasks' && <>{currentProject && <BatchHistory project={currentProject} onOpen={id => { localStorage.setItem(`shixue-batch-${projectId}`, id); void openBatch(); }} />}<TaskPage tasks={tasks} task={openTask?.project_id === projectId ? openTask : undefined} selectedId={openTaskId} onSelect={openTaskRecord} onBack={() => setOpenTaskId(null)} onRetry={retryTask} onSource={showSource} onResult={showTaskArtifact} onSettings={() => navigate('settings')} onExport={exportArtifact} /></>}
          {page === 'artifacts' && <>{!openArtifact && <WorkResults units={units} onOpen={id => void openUnit(id)} onCreateSubmission={unit => { void navigate('submissions').then(ok => { if (ok) setSubmissionDraft({ title: unit.title, project_id: unit.project_id, source_kind: 'unit', source_id: unit.id }); }); }} />}<ArtifactsPage onResearchSource={anchor=>{void (async()=>{try{const source=await request<{edition_id:string;deleted:boolean;locator:{asset_id:string;page_index:number}}>('/api/knowledge/anchors/'+anchor);if(source.deleted)throw Error('原始来源已删除，成果中的引文快照仍保留');if(await navigate('global-library'))setLibraryIncoming({id:source.edition_id,asset:source.locator.asset_id,page:source.locator.page_index});}catch(error){setToast(errorText(error));}})();}} onCreateSubmission={artifact => { void navigate('submissions').then(ok => { if (ok) setSubmissionDraft({ title: artifact.title, project_id: artifact.project_id, source_kind: 'artifact', source_id: artifact.id }); }); }} artifacts={artifacts} selected={openArtifact} onSelect={openArtifactRecord} onBack={() => setArtifactId(null)} onSource={showSource} onTask={openTaskRecord} onExport={exportArtifact} /></>}
          {page === 'settings' && <div className="page-container settings-hub-tabs" role="tablist" aria-label="设置类别">{([['model','模型设置'],['appearance','外观'],['data','数据与备份'],['assistants','助手与 Skill'],['retrieval','检索设置'],['catalog','资料维护']] as const).map(([id,label])=><button key={id} role="tab" aria-selected={settingsSection===id} className={settingsSection===id?'button-primary':'button-secondary'} onClick={()=>void (async()=>{if(leaveWork.current&&!(await leaveWork.current()))return;setSettingsSection(id);})()}>{label}</button>)}</div>}
          {page === 'settings' && settingsSection === 'model' && <SettingsPage settings={settings} dirty={dirtySettings} onChange={value => { dirtyRef.current = true; ++settingsVersion.current; setSettings(value); setDirtySettings(true); setConnectionResult(null); }} onSave={saveSettings} saving={savingSettings} onTest={testConnection} testing={testing} connectionResult={connectionResult} onReturn={() => void navigate(settingsReturn.current)} />}
          {page === 'settings' && settingsSection === 'appearance' && <AppearanceSettings theme={theme} onChange={v=>{setTheme(v);saveTheme(v);}}/>}
          {page === 'settings' && settingsSection === 'data' && <><WorkspaceStorage /><KnowledgeSettings /></>}
          {page === 'settings' && settingsSection !== 'model' && settingsSection !== 'appearance' && <KnowledgeWorkbench key={settingsSection} section="management" initialTab={settingsSection==='data'?'maintenance':settingsSection==='assistants'?'assistant-settings':settingsSection==='retrieval'?'retrieval-settings':'unclassified'} projects={data.projects} projectId={projectId} onChanged={changed} onSettings={() => void navigate('settings')} onRegisterLeave={registerLeave} />}

          {(page === 'knowledge' || page === 'global-library' && !globalReaderId) && <KnowledgeWorkbench onWorkingInput={async(id,destination)=>{if(destination!==projectId)await switchProject(destination);await changed();if(await navigate('materials')){setSelectedId(id);setPendingTool(null);updateView({taskKind:'punctuate',tab:'reader',sideOpen:true},id);}}} key={'library-'+libraryReset} incomingSource={libraryIncoming} onIncomingUsed={clearLibraryIncoming} onNavigateAssistant={(query,id,filter)=>{setAssistantDraft({query,id,filter});openAssistant();}} projects={data.projects} projectId={projectId} onChanged={changed} onSettings={() => void navigate('settings')} onRegisterLeave={registerLeave} />}
        </>}
      </div>
      <footer className="statusbar"><div><span className={`status-live-dot ${serviceError ? 'status-offline' : ''}`} />{serviceError ? '资料服务连接失败' : data ? '资料服务已连接' : '正在连接资料服务'}</div><span>当前项目 · {materials.length} 份材料</span><span className="statusbar-shortcut">按 <kbd>Ctrl K</kbd> 搜索</span></footer>
    </main>
    {assistantVisible&&<PanelDivider side="right" width={rightWidth} max={Math.min(620,rightMaximum)} onWidth={v=>setLayout(s=>({...s,rightWidth:v}))}/>}
    {currentProject&&<aside className="project-assistant-panel" aria-label="项目研究助手" hidden={!assistantVisible}><header className="project-assistant-heading"><strong>研究助手</strong><button className="icon-btn" aria-label="收起研究助手" onClick={()=>setLayout(v=>({...v,rightOpen:false}))}><PanelRightClose size={17}/></button></header><ProjectAssistantPanel key={projectId} project={currentProject} draft={assistantDraft} onDraftUsed={clearAssistantDraft} onChanged={changed} onSettings={()=>void navigate('settings')} onOpenSource={async(id,asset,p)=>{setGlobalAssistant(true);if(await navigate('global-library'))setLibraryIncoming({id,asset,page:p});}}/></aside>}
    {toast && <div className="toast" role="status" aria-live="polite"><CircleAlert size={17} /><span>{toast}</span><button className="icon-btn" aria-label="关闭提示" onClick={() => setToast('')}><X size={16} /></button></div>}
    {projectConfigOpen && currentProject && <ProjectConfigDialog project={currentProject} onClose={() => setProjectConfigOpen(false)} onSave={configureProject} onArchive={archiveProject} onDelete={deleteProject} />}
    {projectDialog && <Modal title="新建研究项目" onClose={() => { if (!creatingProject) setProjectDialog(false); }}><form onSubmit={e => { e.preventDefault(); void createProject(); }}><label className="form-label">项目名称<input autoFocus value={projectName} maxLength={100} onChange={e => setProjectName(e.target.value)} placeholder="例如：明清地方社会研究" /></label><label className="form-label">项目类型<select value={projectType} onChange={e => setProjectType(e.target.value as Project['project_type'])}>{Object.entries(projectNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><p className="scope-note">可以先创建，再添加底本、参校资料和研究规则。类型不会限制其他研究工具。</p><div className="modal-actions"><button type="button" className="button-secondary" disabled={creatingProject} onClick={() => setProjectDialog(false)}>取消</button><button className="button-primary" disabled={!projectName.trim() || creatingProject}>{creatingProject ? '创建中…' : '创建项目'}</button></div></form></Modal>}
  </div>;
}

function NavItem({ icon: Icon, label, active, count, onClick }: { icon: LucideIcon; label: string; active: boolean; count?: number; onClick(): void }) {
  return <button className={`nav-item ${active ? 'nav-item-active' : ''}`} aria-label={label} title={label} aria-current={active ? 'page' : undefined} onClick={onClick}><Icon size={17} /><span>{label}</span>{count ? <span className="nav-count">{count}</span> : null}</button>;
}
