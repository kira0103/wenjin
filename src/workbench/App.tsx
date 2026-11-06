import {isAppDialogOpen} from './dialogQueue';
import PageHeading from './PageHeading';
import UserLibrary from '../portal/UserLibrary';
import { workspaceStorage as localStorage } from './storage';
import { platform } from './platform';
import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { BookMarked, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Check, ChevronDown, ChevronRight, CircleAlert, Command, FileText, FolderKanban, History, Info, Layers3, LibraryBig, LoaderCircle, Plus, Search, Settings2, ShieldCheck, X, type LucideIcon } from 'lucide-react';
import { api, request, type MaterialRole, type Project, type Bootstrap, type MaterialDetail, type ModelSettings, type SourceContext, type TaskArtifact, type TaskKind, type WorkArtifact, type WorkTask, taskNames } from './api';
import { clampPage, followedRange, materialView, readSession, saveSession, searchMaterials, sourceInfo, type MaterialView } from './viewState';
import { GlobalDocument, projectNames } from './LibraryViews';
import { ProjectOverview, ProjectConfigDialog, CollationWorkspace, ResearchList, ResearchEditor, WorkResults } from './ResearchViews';
import { BatchHistory } from './BatchViews';
import { CollationDebug } from './CollationDebug';
import { ArtifactsPage, SettingsPage, TaskPage, Workspace, Modal, ModelEnvironmentPanel } from './Pages';
import KnowledgeSettings from './knowledge/KnowledgeSettings';
import KnowledgeWorkbench from './knowledge/KnowledgeWorkbench';
import ProjectLibrary from './knowledge/ProjectLibrary';
import { ensureSoleBase, listProjectSources, patchProjectSource } from './knowledge/projectSourceApi';
import type { LibraryScope } from './knowledge/documentTypes';
import WorkspaceStorage from './WorkspaceStorage';
import SubmissionWorkbench, { type SubmissionDraft } from './SubmissionWorkbench';
import ProjectAssistantPanel from './knowledge/ProjectAssistantPanel';
import AppearanceSettings from './AppearanceSettings';
import PanelDivider from './PanelDivider';
import AccountSettings from '../portal/AccountSettings';
import type { PortalUser } from '../portal/types';
import {applyTheme,readTheme,saveTheme,readLayout,saveLayout} from './appearance';

type Page = 'research-assistant' | 'submissions' | 'knowledge' | 'collation-debug' | 'collation-batch' | 'volume-preparation' | 'global-library' | 'project-materials' | 'research' | 'home' | 'library' | 'materials' | 'tasks' | 'artifacts' | 'settings' | 'management';
type SettingsSection = 'model' | 'data' | 'assistants' | 'retrieval' | 'catalog' | 'appearance' | 'account' | 'about' | 'research';
type ToastTone = 'success' | 'error' | 'neutral';
const titles: Record<Page, string> = { management: '管理台', 'research-assistant': '研究助手', submissions: '论文投稿管理', knowledge: '文献库', 'collation-debug': '点校调试', 'collation-batch': '批量点校', 'volume-preparation': '准备卷文本', 'global-library': '文献库', 'project-materials': '项目材料', research: '研究工作', home: '项目概览', library: '文献库', materials: '材料阅读', tasks: '执行记录', artifacts: '研究成果', settings: '模型与设置' };
function errorText(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试'; }
const PAGE_IDS = Object.keys(titles) as Page[];
function routeFromHash(): { page: Page; detail: string | null } | null { try { const match = /^#\/([a-z-]+)(?:\/([^/]+))?$/.exec(window.location.hash); if (!match || !PAGE_IDS.includes(match[1] as Page)) return null; return { page: match[1] as Page, detail: match[2] ? decodeURIComponent(match[2]) : null }; } catch { return null; } }
function detailFromHash(page: Page): string | null { const route = routeFromHash(); return route?.page === page ? route.detail : null; }
function routeHash(page: Page, detail: string | null): string { return detail ? `#/${page}/${encodeURIComponent(detail)}` : `#/${page}`; }
function writePageHash(page: Page, replace = false): void { try { const value = `#/${page}`; if (window.location.hash === value) return; if (replace) window.history.replaceState(null, '', value); else window.history.pushState(null, '', value); } catch { /* 不支持 History 的环境忽略 */ } }

export default function App({portalUser,onLogout}:{portalUser?:PortalUser;onLogout?:()=>Promise<void>}={}) {
  const [session, setSession] = useState(readSession);
  const projectId = session.projectId;
  const [page, setPageState] = useState<Page>(() => { const route = routeFromHash(); if (route && (route.page !== 'management' || !portalUser)) return route.page; const saved = session.pageByProject?.[session.projectId] as Page; const initial = saved === 'management' && portalUser ? 'home' : saved; return initial==='research-assistant'?'home':initial in titles?initial:'home'; });
  const [managementTab,setManagementTab]=useState<'assistants'|'retrieval'|'catalog'>('assistants');
  const [theme,setTheme]=useState(readTheme),[layout,setLayout]=useState(readLayout),[viewport,setViewport]=useState(innerWidth),[globalAssistant,setGlobalAssistant]=useState(false);
  useEffect(()=>{applyTheme(theme);const match=matchMedia('(prefers-color-scheme: dark)'),sync=()=>{if(theme==='system')applyTheme(theme);};match.addEventListener('change',sync);return()=>match.removeEventListener('change',sync);},[theme]);
  useEffect(()=>{saveLayout(layout);},[layout]);
  useEffect(()=>{const resize=()=>setViewport(innerWidth);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[]);
  const [mobileNav,setMobileNav]=useState(false),[mobileAssistant,setMobileAssistant]=useState(false);
  const compact=!!portalUser&&viewport<980;
  const [libraryScope,setLibraryScope]=useState<'system'|'personal'>('system');
  const [data, setData] = useState<Bootstrap | null>(null);
  const [startup,setStartup]=useState({stage:'starting',message:'正在启动资料服务',elapsed:0});
  useEffect(()=>{if(data||!platform.serviceStatus)return;let live=true;const check=()=>void platform.serviceStatus!().then(value=>{if(live)setStartup(value);}).catch(()=>{});check();const timer=setInterval(check,500);return()=>{live=false;clearInterval(timer);};},[!!data]);
  const [selectedId, setSelectedId] = useState<string | null>(() => (detailFromHash('materials') ?? session.selectedByProject[projectId]) || null);
  const [detail, setDetail] = useState<MaterialDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);

  const [taskCache, setTaskCache] = useState<Record<string, WorkTask>>({});
  const [taskRevision, setTaskRevision] = useState(0);
  const [openTaskId, setOpenTaskId] = useState<string | null>(() => detailFromHash('tasks'));
  const [artifactId, setArtifactId] = useState<string | null>(() => detailFromHash('artifacts'));
  const [, setFilter] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [toast, setToastState] = useState<{ message: string; tone: ToastTone } | null>(null);
  const [toastPaused, setToastPaused] = useState(false);
  /** 显式携带 tone：成功/失败不再靠文案猜测。 */
  function setToast(message: string, tone: ToastTone = 'neutral') { setToastState(message.trim() ? { message, tone } : null); }
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
  const [unitId, setUnitId] = useState<string | null>(() => (detailFromHash('research') ?? session.unitByProject?.[session.projectId]) || null);
  const [batchRun, setBatchRun] = useState<string | undefined>();
  const settingsReturn = useRef<Page>(page === 'settings' ? 'home' : page);
  const debugReturn = useRef<Page>(page === 'collation-debug' ? 'collation-batch' : page);
  const [debugAttempt, setDebugAttempt] = useState<string | undefined>();
  const [globalReaderId, setGlobalReaderId] = useState<string | null>(() => detailFromHash('global-library'));
  const [globalSourceDetail,setGlobalSourceDetail]=useState<MaterialDetail|null>(null);
  const leaveWork = useRef<(() => Promise<boolean>) | null>(null);
  const navigationVersion = useRef(0);
  const registerLeave = useCallback((callback: (() => Promise<boolean>) | null) => { leaveWork.current = callback; }, []);
  const [creatingProject, setCreatingProject] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(portalUser?'appearance':'model');
  const settingsTabs:[SettingsSection,string][]=portalUser?[['appearance','外观'],['research','研究环境'],['account','账号与安全'],['about','关于']]:[['model','模型设置'],['appearance','外观'],['data','数据与备份']];
  const settingsTabActive = settingsTabs.some(([id]) => id === settingsSection) ? settingsSection : settingsTabs[0][0];
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
  const modelPool = data?.model_pool;
  const poolModels = modelPool?.models || [];
  const poolDefaultModel = poolModels.find(model => model.id === modelPool?.default_model_id);
  const projectPoolModel = currentProject?.model_id ? poolModels.find(model => model.id === currentProject.model_id) : undefined;
  const effectiveModel = {
    name: projectPoolModel?.name || data?.settings.model_name || data?.settings.model || poolDefaultModel?.name || '',
    configured: !!data?.settings.configured,
  };
  const modelChipLabel = effectiveModel.name ? `${effectiveModel.name} · ${effectiveModel.configured ? '已配置' : '未就绪'}` : '配置模型';
  const modelStatusHint = portalUser ? (portalUser.role === 'admin' ? '请到管理后台配置模型服务。' : '模型服务由管理员配置，尚未就绪，请联系管理员。') : '请先在“模型与设置”中配置模型服务。';
  const assistantHiddenPages=portalUser?['settings','management','submissions','knowledge','global-library','materials','project-materials','home','tasks','artifacts','research','volume-preparation','collation-batch','collation-debug']:['settings','management','submissions','knowledge','global-library','materials'];
  const assistantExplicitPages=portalUser?['knowledge','global-library','materials','project-materials','home','tasks','artifacts','research','volume-preparation','collation-batch','collation-debug']:['knowledge','global-library','materials'];
  const assistantVisible=!!currentProject&&(compact?mobileAssistant:layout.rightOpen)&&(!assistantHiddenPages.includes(page)||globalAssistant&&assistantExplicitPages.includes(page));
  useEffect(()=>{if(portalUser&&assistantHiddenPages.includes(page))setGlobalAssistant(false);},[page,portalUser]);
  const leftWidth=Math.min(layout.leftWidth,Math.max(184,viewport-(assistantVisible?320:0)-496));
  const rightMaximum=Math.max(320,viewport-(layout.leftOpen?leftWidth+8:0)-488);
  const rightWidth=Math.min(layout.rightWidth,rightMaximum);
  const gridStyle={'--left-width':layout.leftOpen?leftWidth+'px':'0px','--left-divider':layout.leftOpen?'8px':'0px','--right-width':assistantVisible?rightWidth+'px':'0px','--right-divider':assistantVisible?'8px':'0px'} as CSSProperties;
  function closeAssistant(){if(compact)setMobileAssistant(false);else setLayout(v=>({...v,rightOpen:false}));}
  function openAssistant(){if(compact){setMobileAssistant(true);setMobileNav(false);}if(page==='settings'||page==='management'||page==='submissions')void navigate('home');setGlobalAssistant(true);setLayout(v=>({...v,rightOpen:true}));}
  useEffect(()=>{if(page==='research'&&unitId)setLayout(v=>v.rightOpen?{...v,rightOpen:false}:v);},[page,unitId]);
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
    const unsubscribe = platform.onServiceReady(() => { void boot(); });
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
      } catch (error) { if (!cancelled) setToast(errorText(error), 'error'); }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [observedId, observedStatus, projectId, taskRevision]);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if(isAppDialogOpen())return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(v => !v); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f' && ['library', 'project-materials', 'global-library'].includes(page)) { event.preventDefault(); document.querySelector<HTMLInputElement>('.inline-search input')?.focus(); }
      if (event.key === 'Escape') { setSearchOpen(false); setProjectMenu(false); setMobileNav(false);setMobileAssistant(false); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [page]);
  useEffect(() => {
    const onHash = () => {
      const route = routeFromHash();
      if (!route || (route.page === 'management' && portalUser)) return;
      setPageState(route.page);
      if (route.page === 'materials') setSelectedId(route.detail);
      if (route.page === 'tasks') setOpenTaskId(route.detail);
      if (route.page === 'artifacts') setArtifactId(route.detail);
      if (route.page === 'global-library') setGlobalReaderId(route.detail);
      if (route.page === 'research') setUnitId(route.detail);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [portalUser]);
  const routeDetail = page === 'materials' ? selectedId : page === 'tasks' ? openTaskId : page === 'artifacts' ? artifactId : page === 'global-library' ? globalReaderId : page === 'research' ? unitId : null;
  useEffect(() => { const target = routeHash(page, routeDetail); if (window.location.hash !== target) { try { window.history.replaceState(null, '', target); } catch { /* 不支持 History 的环境忽略 */ } } }, [page, routeDetail]);
  useEffect(() => { if (searchOpen) searchRef.current?.focus(); }, [searchOpen]);
  useEffect(() => { if (!toast || toastPaused) return; const timer = window.setTimeout(() => setToastState(null), 6500); return () => window.clearTimeout(timer); }, [toast, toastPaused]);

  function updateView(patch: Partial<MaterialView>, id = selectedId, pages = material?.page_count || 1) {
    if (!id) return;
    setSession(s => {
      const current = materialView(s.views[`${projectId}:${id}`] || s.views[id], pages);
      const next: Partial<MaterialView> = { ...patch };
      if (patch.readerPage !== undefined && (patch.rangePinned ?? current.rangePinned ?? false) === false) Object.assign(next, followedRange(patch.readerPage, pages));
      if ((patch.pageFrom !== undefined || patch.pageTo !== undefined) && patch.rangePinned === undefined) next.rangePinned = true;
      if (patch.rangePinned === false) Object.assign(next, followedRange(current.readerPage, pages));
      return { ...s, views: { ...s.views, [`${projectId}:${id}`]: { ...current, ...next } } };
    });
  }
  async function navigate(target: Page, section?: SettingsSection) {
    if(target==='research-assistant'){openAssistant();return true;}
    const version = ++navigationVersion.current;
    if (leaveWork.current && !(await leaveWork.current())) return false;
    if (version !== navigationVersion.current) return false;
    if (target === 'settings' && page !== 'settings') {settingsReturn.current = page;if(!section&&portalUser)setSettingsSection('account');}
    if (target === 'settings' && section) setSettingsSection(section);
    if(target==='materials'&&target!==page)setGlobalAssistant(false);
    if(target!==page&&assistantHiddenPages.includes(target))setGlobalAssistant(false);
    if (target !== page) writePageHash(target);
    setPageState(target); setMobileNav(false); return true;
  }
  async function openSettings(section: SettingsSection = 'research') {
    const target: SettingsSection = portalUser ? section : section === 'research' ? 'model' : section;
    if (!(await navigate('settings', target))) return;
    setSettingsSection(target);
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
    setToast(result.cleanup_errors.length ? '项目已删除：' + result.cleanup_errors.join('；') : '项目已删除，文献库原文件保留', result.cleanup_errors.length ? 'error' : 'success');
  }
  async function restoreProject(id: string) { try { await api.restoreProject(id); await changed(); } catch (error) { setToast(errorText(error), 'error'); } }
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
    if (tool === 'references') updateView({ sideOpen: false }, id, selected.page_count);
    else if (tool) updateView({ taskKind: tool, sideOpen: true }, id, selected.page_count);
  }
  async function doImport(entryId?: string, destinationProject: string | null = projectId, role: MaterialRole = 'reference', linkProject = false): Promise<boolean | null> {
    // 门户里普通导入仍去“我的文献库”；只有从项目材料/项目概览发起的“导入并关联项目”才在本项目内导入并建立项目来源。
    if (portalUser && !linkProject) { if(await navigate('global-library')){setGlobalReaderId(null);setLibraryScope('personal');setToast('在“我的文献库”导入资料，只有你可见');} return null; }
    if (uploading) return null;
    const importProject = destinationProject;
    try {
      const files = await platform.pickDocuments();
      if (!files.length) return null;
      setUploading(true);
      let latest: string | null = null;
      const failures: string[] = [];
      const imported: { editionId: string; assetId: string; title: string; pages: number }[] = [];
      let count = 0;
      const beforeSources = linkProject && importProject ? await listProjectSources(importProject).catch(() => []) : [];
      for (const file of files) {
        try {
          const importedMaterial = await api.importDocument(file.data, file.name, importProject || undefined, role, entryId);
          latest = importedMaterial.id; count++;
          const assetId = importedMaterial.id.includes('~') ? importedMaterial.id.split('~')[1] : '';
          if (importedMaterial.entry_id && assetId) imported.push({ editionId: importedMaterial.entry_id, assetId, title: importedMaterial.title || file.name, pages: importedMaterial.page_count });
        }
        catch (error) { failures.push(`${file.name}：${errorText(error)}`); }
      }
      let compensationWarning = '';
      if (linkProject && importProject && imported.length) {
        // 导入接口按“整个版本”写来源且会覆盖同范围角色；这里补偿角色/简称并保证底本唯一。
        try {
          const after = await listProjectSources(importProject);
          for (const previous of beforeSources) {
            const current = after.find(source => source.id === previous.id);
            if (!current) continue;
            const roles = Array.from(new Set([...current.roles, ...previous.roles]));
            const witnessLabel = previous.witness_label && !current.witness_label ? previous.witness_label : current.witness_label;
            const scopeNote = previous.scope_note && !current.scope_note ? previous.scope_note : current.scope_note;
            if (roles.length !== current.roles.length || witnessLabel !== current.witness_label || scopeNote !== current.scope_note) {
              await patchProjectSource(importProject, current, { roles, witness_label: witnessLabel, scope_note: scopeNote });
            }
          }
          if (role === 'base') {
            const refreshed = await listProjectSources(importProject);
            const editions = new Set(imported.map(item => item.editionId));
            const base = [...refreshed].reverse().find(source => editions.has(source.edition_id) && source.roles.includes('base'));
            if (base) await ensureSoleBase(importProject, base.id);
          }
        } catch { compensationWarning = '；项目用途自动整理失败，请在项目材料中确认'; }
      }
      await changed().catch(() => { /* 列表刷新失败不影响已完成的导入。 */ });
      if (importProject === projectRef.current && latest) {
        setSelectedId(latest);
        if (pendingTool) { const imported = await api.material(latest); updateView(pendingTool === 'references' ? { sideOpen: false } : { taskKind: pendingTool, sideOpen: true }, latest, imported.page_count); setPendingTool(null); await navigate('materials'); }
        else await navigate('project-materials');
      }
      setToast(failures.length ? `已导入 ${count} 份；${failures.join('；')}${compensationWarning}` : `已保存 ${count} 份材料${importProject ? '并关联目标项目' : '到文献库'}${compensationWarning}`, failures.length ? 'error' : compensationWarning ? 'neutral' : 'success');
      return count > 0;
    } catch (error) { setToast(errorText(error), 'error'); return false; }
    finally { setUploading(false); }
  }
  async function createProject() {
    if (!projectName.trim() || creatingProject) return;
    setCreatingProject(true);
    try { const value = await api.createProject({ name: projectName.trim(), project_type: projectType }); await switchProject(value.id); setProjectDialog(false); setProjectName(''); }
    catch (error) { setToast(errorText(error), 'error'); }
    finally { setCreatingProject(false); }
  }
  async function runTask() {
    if (busy || !material || !currentDetail || serviceError) return;
    const info = sourceInfo(currentDetail, view);
    const problem = !data?.settings.configured ? (portalUser?'研究模型尚未就绪，请联系管理员':'请先保存模型连接设置') : view.pageFrom > view.pageTo ? '起始页不能大于结束页' : view.sourceMode === 'paste' ? !view.pastedSource.trim() ? '请粘贴需要处理的原文' : view.pastedSource.length > 30000 ? '粘贴原文超过 30,000 字，请分批处理' : '' : !info.hasText ? '所选页面没有文字，请切换到粘贴原文' : info.characters > 42000 ? '所选文字超过 42,000 字，请缩小页码范围' : '';
    if (problem) { setToast(problem, 'error'); return; }
    const runProject = projectId, runMaterial = material.id;
    setBusy(true);
    try {
      const created = await api.createTask({ project_id: runProject, material_id: runMaterial, task_type: view.taskKind, instructions: view.message.trim(), target_language: view.targetLanguage.trim() || '中文', source_text: view.sourceMode === 'paste' ? view.pastedSource.trim() : '', page_from: view.pageFrom, page_to: view.pageTo });
      setTaskCache(cache => ({ ...cache, [created.id]: created }));
      updateView({ taskId: created.id, message: '', sideOpen: true }, runMaterial, material.page_count);
      if (projectRef.current === runProject) await refresh();
      setToast(`${taskNames[view.taskKind]}任务已提交，可在执行记录中查看`, 'success');
    } catch (error) { setToast(errorText(error), 'error'); }
    finally { setBusy(false); }
  }
  async function retryTask(id: string) {
    try { await api.resumeTask(id); setTaskCache(cache => { const next = { ...cache }; delete next[id]; return next; }); setTaskRevision(v => v + 1); await refresh(); setToast('任务已重新执行', 'success'); }
    catch (error) { setToast(errorText(error), 'error'); }
  }
  async function saveSettings() {
    if (savingSettings) return;
    const version = settingsVersion.current;
    setSavingSettings(true);
    try {
      const { api_key, ...configuration } = settings;
      const saved = await api.saveSettings({ ...configuration, ...(api_key ? { api_key } : {}) });
      if (version === settingsVersion.current) { dirtyRef.current = false; setDirtySettings(false); setSettings({ ...saved, api_key: '' }); }
      setConnectionResult(null); await refresh(); setToast('模型设置已保存，可测试连接', 'success');
    } catch (error) { setToast(errorText(error), 'error'); }
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
    try { if (confirm) await api.confirmReferenceVersion(id); else await api.addToLibrary(id); setDetailRevision(v => v + 1); await changed(); setToast(confirm ? '已记录人工版本核验' : '已加入本机共享书目库，仍需核验版本', 'success'); }
    catch (error) { setToast(errorText(error), 'error'); throw error; }
  }
  async function showSource(materialId: string | null | undefined, context?: SourceContext) {
    if (!materialId) { setToast('该研究成果来自粘贴文本，没有关联 PDF'); return; }
    try {
      const source = await api.material(materialId);
      if(source.kind==='working_input'||!globalMaterials.some(m=>m.id===source.id)) {if(await navigate('global-library')){setGlobalSourceDetail(source);setGlobalReaderId(source.id);}return;}
      const sourceProject = source.project_ids?.includes(projectId) ? projectId : undefined;
      if (!sourceProject) { if (await navigate('global-library')) setGlobalReaderId(source.id); return; }
      if (sourceProject !== projectId) await switchProject(sourceProject);
      setSelectedId(source.id); navigate('materials');
      setSession(s => {
        const base = materialView(s.views[`${sourceProject}:${source.id}`] || s.views[source.id], source.page_count);
        const targetPage = context?.page_from || Number(/第\s*(\d+)/.exec(context?.selection || '')?.[1]) || 1;
        const followed = (base.rangePinned ?? false) ? null : followedRange(targetPage, source.page_count);
        const readerPage = followed ? followed.pageFrom : clampPage(targetPage, source.page_count);
        return { ...s, selectedByProject: { ...s.selectedByProject, [sourceProject]: source.id }, views: { ...s.views, [`${sourceProject}:${source.id}`]: { ...base, readerPage, ...(followed || {}) } } };
      });
    } catch (error) { setToast(errorText(error), 'error'); }
  }
  async function exportArtifact(artifact: WorkArtifact | TaskArtifact) {
    try {
      const file = await api.exportArtifact(artifact.id);
      const saved = await platform.saveFile({ filename:file.filename, data:new TextEncoder().encode(file.content).buffer });
      if (saved) setToast('研究成果已导出', 'success');
    } catch (error) { setToast(errorText(error), 'error'); }
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
      if (!result) { setToast('此任务尚未生成研究成果，可在执行记录中查看状态'); return; }
      setData(current => current ? { ...current, artifacts: next } : current);
      openArtifactRecord(result);
    } catch (error) { setToast(errorText(error), 'error'); }
  }

  function subnavKeys(event: ReactKeyboardEvent<HTMLElement>) {
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0) return;
    const next = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? (index + 1) % buttons.length : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? (index + buttons.length - 1) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
    if (next >= 0) { event.preventDefault(); buttons[next].focus(); buttons[next].click(); }
  }
  return <div className={`app-shell ${portalUser?'portal-workspace':''}`} style={gridStyle}>
    <a className="skip-link" href="#workspace-content" onClick={event => { event.preventDefault(); const main = document.getElementById('workspace-content'); main?.focus(); main?.scrollIntoView({ block: 'start' }); }}>跳到主内容</a>
    <header className="app-titlebar"><div className="titlebar-start"><button className="icon-btn" aria-label={(compact?mobileNav:layout.leftOpen)?'折叠导航':'展开导航'} aria-expanded={compact?mobileNav:layout.leftOpen} onClick={()=>{if(compact){setMobileNav(value=>!value);setMobileAssistant(false);}else setLayout(v=>({...v,leftOpen:!v.leftOpen}));}}>{layout.leftOpen?<PanelLeftClose size={18}/>:<PanelLeftOpen size={18}/>}</button><BookMarked size={18}/><span>{portalUser?'问津':'史学工作台'}</span>{portalUser&&<small className="workspace-wordmark">WENJIN</small>}</div>{!portalUser&&<span className="titlebar-project">{currentProject?.name||'研究工作区'}</span>}<div className="titlebar-end">{portalUser&&<button className="search-trigger" aria-label="搜索当前项目材料" title="搜索当前项目材料（Ctrl K）" aria-expanded={searchOpen} onClick={()=>setSearchOpen(value=>!value)}><Search size={16}/><span>项目材料</span><kbd>Ctrl K</kbd></button>}{portalUser&&<><button className="button-quiet portal-account-button" onClick={()=>{setSettingsSection('account');void navigate('settings');}}>{portalUser.display_name}</button>{portalUser.role==='admin'&&<a className="portal-admin-link" href="/admin/">管理后台</a>}{!compact&&page!=='collation-debug'&&<button className="provider-chip" onClick={() => void openSettings('research')} title="查看研究环境">{modelChipLabel}</button>}</>}<button className="button-quiet" aria-label="研究助手" aria-expanded={assistantVisible} onClick={()=>{if(assistantVisible)closeAssistant();else openAssistant();}}>{assistantVisible?<PanelRightClose size={18}/>:<PanelRightOpen size={18}/>}<span>研究助手</span></button></div></header>
    <aside className={`sidebar ${compact&&mobileNav?'is-open':''}`} aria-label="工作台导航" hidden={!compact&&!layout.leftOpen}>

      <div className="project-heading"><span>研究项目</span><button className="icon-btn subtle" aria-label="新建研究项目" onClick={() => setProjectDialog(true)}><Plus size={16} /></button></div>
      <button className="project-switch" aria-expanded={projectMenu} onClick={() => setProjectMenu(v => !v)}><FolderKanban size={16} /><span>{currentProject?.name || '正在加载项目…'}</span><ChevronDown size={16} /></button>
      {projectMenu && <div className="project-menu">{data?.projects.filter(p => !p.archived).map(p => <button key={p.id} onClick={() => switchProject(p.id)}><FolderKanban size={16} /><span>{p.name}</span>{p.id === projectId && <Check size={16} />}</button>)}<button onClick={() => { setProjectMenu(false); setProjectDialog(true); }}><Plus size={16} />新建研究项目</button><button onClick={() => setShowArchived(v => !v)}>已归档项目 {data?.projects.filter(p => p.archived).length || 0}</button>{showArchived && data?.projects.filter(p => p.archived).map(p => <button key={p.id} onClick={() => void restoreProject(p.id)}><History size={16} /><span>{p.name}</span>恢复</button>)}</div>}
      <nav className="nav-list"><NavItem icon={LibraryBig} label="文献库" active={page === 'global-library' || page === 'knowledge'} onClick={() => void (async()=>{if(await navigate('global-library')){setGlobalAssistant(false);setGlobalReaderId(null);setLibraryReset(v=>v+1);}})()} /><NavItem icon={FileText} label="论文投稿管理" active={page === 'submissions'} onClick={() => void navigate('submissions')} /><div className="nav-scope-label">当前项目</div><NavItem icon={Command} label="项目概览" active={page === 'home'} onClick={() => void navigate('home')} /><NavItem icon={LibraryBig} label="项目材料" active={page === 'project-materials' || page === 'library'} count={materials.length} onClick={() => { setPendingTool(null); void navigate('project-materials'); }} /><NavItem icon={FileText} label={currentProject?.project_type === 'collation' ? '古籍点校' : '研究工作'} active={page === 'research' || page === 'volume-preparation' || page === 'collation-batch' || page === 'collation-debug'} count={units.length} onClick={() => void openResearch()} /><NavItem icon={Layers3} label="研究成果" active={page === 'artifacts'} count={artifacts.length + units.length} onClick={() => { setArtifactId(null); void navigate('artifacts'); }} /><NavItem icon={History} label="执行记录" active={page === 'tasks'} count={tasks.filter(t => ['running', 'queued', 'failed', 'paused'].includes(t.status)).length} onClick={() => { setOpenTaskId(null); void navigate('tasks'); }} /></nav>

      <div className="sidebar-heading"><span>最近材料</span><button className="icon-btn subtle" aria-label={portalUser?'导入个人文献':'导入项目材料'} disabled={uploading} onClick={() => void doImport()}><Plus size={16} /></button></div>
      <div className="material-nav-list">{[...materials].sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId)).slice(0, 8).map(m => <button key={m.id} title={m.title} aria-current={page === 'materials' && selectedId === m.id ? 'page' : undefined} className={`material-nav ${page === 'materials' && selectedId === m.id ? 'material-nav-active' : ''}`} onClick={() => selectMaterial(m.id)}><FileText size={16} /><span>{m.title}</span></button>)}{!materials.length && <div className="sidebar-empty">尚未添加材料</div>}<button className="sidebar-all button-quiet" onClick={() => { setFilter(''); void navigate('project-materials'); }}>查看项目材料 <ChevronRight size={16} /></button></div>
      <div className="sidebar-bottom"><div className="storage-meter"><ShieldCheck size={16} /><div><strong>{portalUser?'个人工作区':data?.settings.service_mode==='server'?'服务端工作区':'本机工作区'}</strong><small>{portalUser?'系统文献共享，个人文献与研究独立保存':data?.settings.service_mode==='server'?'原件、工作稿与成果保存在资料服务中':'原件、工作稿与成果保存在本机'}</small></div></div>{!portalUser&&<NavItem icon={ShieldCheck} label="管理台" active={page === 'management'} onClick={() => void navigate('management')} />}<NavItem icon={Settings2} label={portalUser?'个人设置':'模型与设置'} active={page === 'settings'} onClick={() => navigate('settings')} /></div>
    </aside>
    {compact&&mobileNav&&<button className="workspace-panel-backdrop" aria-label="关闭工作区导航" onClick={()=>setMobileNav(false)}/>}
    {layout.leftOpen&&!compact&&<PanelDivider side="left" width={leftWidth} max={Math.min(320,Math.max(184,viewport-(assistantVisible?rightWidth+8:0)-488))} onWidth={v=>setLayout(s=>({...s,leftWidth:v}))}/>}
    <main className="main-column">
      {page !== 'materials' && (!portalUser || page === 'collation-debug') && <header className="topbar"><div className="breadcrumbs"><button className="breadcrumb-project" onClick={() => navigate('home')}>{['submissions','research-assistant','global-library','knowledge','management'].includes(page) ? '全局工作区' : currentProject?.name || '研究项目'}</button><ChevronRight size={16} /><span className="breadcrumb-current">{currentProject?.project_type === 'collation' && (page === 'research' || page === 'volume-preparation' || page === 'collation-batch' || page === 'collation-debug') ? '古籍点校' : (page === 'volume-preparation' || page === 'collation-batch') ? '研究工作' : page==='settings'&&portalUser?'个人设置':titles[page]}</span></div><div className="topbar-actions"><button className="search-trigger" aria-expanded={searchOpen} onClick={() => setSearchOpen(v => !v)}><Search size={16} /><span>搜索项目材料</span><kbd>Ctrl K</kbd></button><button className="provider-chip" onClick={() => void openSettings()} title={portalUser ? '查看研究环境' : '打开模型设置'}>{modelChipLabel}</button></div></header>}
      {searchOpen && <div className="search-popover" role="dialog" aria-label="搜索当前项目材料"><Search size={16} /><input ref={searchRef} aria-label="搜索题名或文件名" placeholder="搜索题名或文件名…" value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { const found = searchMaterials(materials, search)[0]; if (found) selectMaterial(found.id); } }} /><button className="icon-btn" aria-label="关闭搜索" onClick={() => setSearchOpen(false)}><X size={16} /></button><div className="search-results">{searchMaterials(materials, search).map(m => <button key={m.id} onClick={() => selectMaterial(m.id)}><FileText size={16} /><span>{m.title}</span><span className="search-result-meta">{m.page_count} 页</span></button>)}{!searchMaterials(materials, search).length && <p className="search-empty">没有符合条件的材料；请尝试其他题名或文件名</p>}</div></div>}
      {serviceError && <div className="service-error" role="alert"><CircleAlert size={16} /><span>资料服务暂时不可用：{serviceError}</span><button className="button-quiet" onClick={() => { void refresh().catch(error => setServiceError(errorText(error))); }}>重新连接</button></div>}
      {data?.catalog_error && <div className="service-error" role="alert"><CircleAlert size={16}/><span>{data.catalog_error}</span><button className="button-quiet" onClick={() => void navigate("global-library")}>打开文献库状态</button></div>}
      <div className="content-scroll" id="workspace-content" tabIndex={-1}>
        {!data ? <div className="page-loading" role={startup.stage==='failed'?'alert':'status'}><LoaderCircle className="spin" size={22} />{startup.stage==='ready'?'正在打开项目资料':startup.message}{startup.elapsed>=3&&<span> · {startup.elapsed} 秒</span>}</div> : <>
      {page === 'submissions' && <SubmissionWorkbench projects={data.projects} draft={submissionDraft} onDraftUsed={clearSubmissionDraft} onRegisterLeave={registerLeave} onCreateFromArtifact={() => { void navigate('artifacts'); }} />}
      {page === 'home' && currentProject && <ProjectOverview activity={data.collation_activity} onBatch={() => { if (data.collation_activity) localStorage.setItem(`shixue-batch-${projectId}`, data.collation_activity.id); void openBatch(); }} preparedCount={data.prepared_material_count || 0} onPreprocess={() => void openPreparation()} selectedUnitId={session.unitByProject?.[projectId]} project={currentProject} materials={materials} units={units} tasks={tasks} onWork={id => void (id ? openUnit(id) : openResearch())} onMaterials={() => void navigate('project-materials')} onImport={() => { void doImport(undefined, projectId, currentProject.project_type === 'collation' && !materials.some(m => m.roles?.includes('base')) ? 'base' : 'reference', true); }} onConfigure={() => setProjectConfigOpen(true)} onTask={openTaskRecord} />}
          {(page === 'project-materials' || page === 'library') && currentProject && <ProjectLibrary onRegisterLeave={registerLeave} key={projectId} project={currentProject} materials={materials} uploading={uploading} portal={!!portalUser} onImport={role => doImport(undefined, projectId, role, true)} onSelect={id => void selectMaterial(id)} onChanged={changed} onPreprocess={() => void openPreparation()} onNotify={setToast} />}
          {page === 'global-library' && globalReaderId && (globalMaterials.find(m => m.id === globalReaderId) || globalSourceDetail?.id === globalReaderId) && <GlobalDocument key={globalReaderId} material={(globalMaterials.find(m => m.id === globalReaderId) || globalSourceDetail)!} onBack={() => setGlobalReaderId(null)} onAdd={() => void navigate('project-materials')} />}
          {page === 'collation-debug' && currentProject && <CollationDebug key={projectId} project={currentProject} materials={materials} attemptId={debugAttempt} onBack={() => void navigate(debugReturn.current)} onSettings={() => void navigate('settings')} />}
          {(page === 'research' || page === 'volume-preparation' || page === 'collation-batch') && currentProject && (
            page === 'research' && unitId ? <ResearchEditor key={projectId + unitId} id={unitId} project={currentProject} materials={materials} tasks={tasks} onBack={() => void openUnit()} onChanged={changed} registerLeave={registerLeave} onTask={openTaskRecord} onDebug={() => void openDebug()} /> :
            currentProject.project_type === 'collation' ? <CollationWorkspace key={projectId} project={currentProject} units={units} materials={materials} preparedCount={data.prepared_material_count || 0} activity={data.collation_activity} stage={page === 'volume-preparation' ? 'prepare' : page === 'collation-batch' ? 'batch' : 'drafts'} batchRun={batchRun} modelName={effectiveModel.name} modelConfigured={effectiveModel.configured} modelHint={modelStatusHint} onBatch={run => void openBatch(run)} onSettings={() => void openSettings()} onDebug={id => void openDebug(id)} onPrepare={() => void openPreparation()} onDrafts={() => void openUnit()} onSelect={id => void openUnit(id)} onCreated={async unit => { await changed(); await openUnit(unit.id); }} onChanged={changed} onMaterials={() => void navigate('project-materials')} /> :
            <ResearchList project={currentProject} units={units} materials={materials} onPreprocess={() => void openPreparation()} onSelect={id => void openUnit(id)} onCreated={async unit => { await changed(); await openUnit(unit.id); }} onMaterials={() => void navigate('project-materials')} />
          )}
          {page === 'materials' && <Workspace material={material} detail={currentDetail} error={detailError} onReload={() => setDetailRevision(v => v + 1)} view={view} updateView={updateView} task={materialTask} tasks={materialTasks} onTask={openTaskRecord} onResult={showTaskArtifact} onSource={showSource} configured={!!data.settings.configured} serviceReady={!serviceError} onSettings={() => navigate('settings')} onBack={() => { setFilter(''); void navigate('project-materials'); }} onImport={() => void doImport()} uploading={uploading} onRun={runTask} busy={busy} onReference={referenceAction} onExport={exportArtifact} />}
          {page === 'tasks' && <><TaskPage history={!openTaskId&&currentProject?<BatchHistory project={currentProject} onOpen={id => { localStorage.setItem(`shixue-batch-${projectId}`, id); void openBatch(); }} />:null} tasks={tasks} task={openTask?.project_id === projectId ? openTask : undefined} selectedId={openTaskId} onSelect={openTaskRecord} onBack={() => setOpenTaskId(null)} onRetry={retryTask} onSource={showSource} onResult={showTaskArtifact} onSettings={() => navigate('settings')} onExport={exportArtifact} /></>}
          {page === 'artifacts' && <><ArtifactsPage onOpenResearch={() => void openResearch()} draftCount={units.length} drafts={units.length?<WorkResults units={units} onOpen={id => void openUnit(id)} onCreateSubmission={unit => { void navigate('submissions').then(ok => { if (ok) setSubmissionDraft({ title: unit.title, project_id: unit.project_id, source_kind: 'unit', source_id: unit.id }); }); }} />:null} onResearchSource={anchor=>{void (async()=>{try{const source=await request<{edition_id:string;deleted:boolean;locator:{asset_id:string;page_index:number}}>('/api/knowledge/anchors/'+anchor);if(source.deleted)throw Error('原始来源已删除，成果中的引文快照仍保留');if(await navigate('global-library'))setLibraryIncoming({id:source.edition_id,asset:source.locator.asset_id,page:source.locator.page_index});}catch(error){setToast(errorText(error), 'error');}})();}} onCreateSubmission={artifact => { void navigate('submissions').then(ok => { if (ok) setSubmissionDraft({ title: artifact.title, project_id: artifact.project_id, source_kind: 'artifact', source_id: artifact.id }); }); }} artifacts={artifacts} selected={openArtifact} onSelect={openArtifactRecord} onBack={() => setArtifactId(null)} onSource={showSource} onTask={openTaskRecord} onExport={exportArtifact} /></>}
          {page === 'settings' && portalUser && <div className="page-container settings-page-header"><PageHeading title={<>个人设置</>} description={<>管理账号安全、界面外观与研究偏好</>}/></div>}
          {page === 'settings' && <div className="page-container settings-shell"><nav className="settings-subnav" aria-label="设置类别" role="tablist" onKeyDown={subnavKeys}>{settingsTabs.map(([id,label])=><button key={id} role="tab" id={`settings-tab-${id}`} aria-controls="settings-panel" aria-selected={settingsSection===id} tabIndex={settingsSection===id?0:-1} className={`settings-nav-item ${settingsSection===id?'is-active':''}`} onClick={()=>void (async()=>{if(leaveWork.current&&!(await leaveWork.current()))return;setSettingsSection(id);})()}>{label}</button>)}</nav><div className="settings-content" id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${settingsTabActive}`}>
          {page === 'settings' && !portalUser && settingsSection === 'model' && <SettingsPage settings={settings} dirty={dirtySettings} onChange={value => { dirtyRef.current = true; ++settingsVersion.current; setSettings(value); setDirtySettings(true); setConnectionResult(null); }} onSave={saveSettings} saving={savingSettings} onTest={testConnection} testing={testing} connectionResult={connectionResult} onReturn={() => void navigate(settingsReturn.current)} />}
          {page === 'settings' && portalUser && settingsSection === 'account' && onLogout && <div className="portal-user-settings"><AccountSettings user={portalUser} onLogout={onLogout} embedded/></div>}
          {page === 'settings' && portalUser && settingsSection === 'research' && <ModelEnvironmentPanel settings={data.settings} pool={data.model_pool} project={currentProject} isAdmin={portalUser.role === 'admin'} />}
          {page === 'settings' && portalUser && settingsSection === 'about' && <section className="settings-about"><h2>运行环境</h2><dl className="settings-facts"><dt>研究模型</dt><dd>{data.settings.model || '尚未配置'}</dd><dt>资料服务</dt><dd>{data.settings.service_mode === 'server' ? '服务端工作区' : '本机工作区'}</dd></dl><p className="scope-note">研究模型与资料服务由管理员配置</p></section>}
          {page === 'settings' && settingsSection === 'appearance' && <AppearanceSettings embedded={!!portalUser} theme={theme} onChange={v=>{setTheme(v);saveTheme(v);}}/>}
          {page === 'settings' && !portalUser && settingsSection === 'data' && <><WorkspaceStorage /><KnowledgeSettings /></>}
          </div></div>}

          {page === 'management' && !portalUser && <div className="page-container settings-page-header"><PageHeading title={<>管理台</>} description={<>助手与 Skill、检索配置和资料维护</>}/></div>}
          {page === 'management' && !portalUser && <div className="page-container settings-shell"><nav className="settings-subnav" aria-label="管理台类别" role="tablist" onKeyDown={subnavKeys}>{(['assistants','retrieval','catalog'] as const).map(tab => <button key={tab} role="tab" id={`management-tab-${tab}`} aria-controls="management-panel" aria-selected={managementTab===tab} tabIndex={managementTab===tab?0:-1} className={`settings-nav-item ${managementTab===tab?'is-active':''}`} onClick={() => setManagementTab(tab)}>{tab==='assistants'?'助手与 Skill':tab==='retrieval'?'检索设置':'资料维护'}</button>)}</nav><div className="settings-content" id="management-panel" role="tabpanel" aria-labelledby={`management-tab-${managementTab}`}>
          {page === 'management' && !portalUser && <KnowledgeWorkbench key={managementTab} embedded section="management" initialTab={managementTab==='assistants'?'assistant-settings':managementTab==='retrieval'?'retrieval-settings':'unclassified'} projects={data.projects} projectId={projectId} onChanged={changed} onSettings={() => void navigate('management')} onRegisterLeave={registerLeave} />}</div></div>}
          {(page === 'knowledge' || page === 'global-library' && !globalReaderId) && <LibraryView portal={!!portalUser} scope={libraryScope} onScope={setLibraryScope} key={'library-'+libraryReset} incomingSource={libraryIncoming} onIncomingUsed={clearLibraryIncoming} onNavigateAssistant={(query,id,filter)=>{setAssistantDraft({query,id,filter});openAssistant();}} projects={data.projects} projectId={projectId} onChanged={changed} onSettings={() => void navigate('settings')} onRegisterLeave={registerLeave} />}
        </>}
      </div>
      <footer className="statusbar"><div><span className={`status-live-dot ${serviceError ? 'status-offline' : ''}`} />{serviceError ? '资料服务连接失败' : data ? '资料服务已连接' : '正在连接资料服务'}</div><span>当前项目 · {materials.length} 份材料</span><span className="statusbar-shortcut">按 <kbd>Ctrl K</kbd> 搜索</span></footer>
    </main>
    {compact&&assistantVisible&&<button className="workspace-panel-backdrop" aria-label="关闭研究助手遮罩" onClick={closeAssistant}/>}
    {assistantVisible&&!compact&&<PanelDivider side="right" width={rightWidth} max={Math.min(620,rightMaximum)} onWidth={v=>setLayout(s=>({...s,rightWidth:v}))}/>}
    {currentProject&&<aside className="project-assistant-panel" aria-label="项目研究助手" hidden={!assistantVisible}><header className="project-assistant-heading"><strong>研究助手</strong><button className="icon-btn" aria-label="收起研究助手" onClick={closeAssistant}><PanelRightClose size={18}/></button></header><ProjectAssistantPanel key={projectId} project={currentProject} draft={assistantDraft} onDraftUsed={clearAssistantDraft} onChanged={changed} onSettings={()=>void navigate('settings')} onOpenSource={async(id,asset,p)=>{setGlobalAssistant(true);if(await navigate('global-library'))setLibraryIncoming({id,asset,page:p});}}/></aside>}
    {toast && <div className={`toast${toast.tone === 'neutral' ? '' : ` is-${toast.tone}`}`} data-tone={toast.tone} role={toast.tone === 'error' ? 'alert' : 'status'} aria-live={toast.tone === 'error' ? 'assertive' : 'polite'} onMouseEnter={() => setToastPaused(true)} onMouseLeave={() => setToastPaused(false)} onFocus={() => setToastPaused(true)} onBlur={() => setToastPaused(false)}>{toast.tone === 'success' ? <Check size={18} /> : toast.tone === 'error' ? <CircleAlert size={18} /> : <Info size={18} />}<span>{toast.message}</span><button className="icon-btn" aria-label="关闭提示" onClick={() => setToastState(null)}><X size={16} /></button></div>}
    {projectConfigOpen && currentProject && <ProjectConfigDialog project={currentProject} modelPool={data?.model_pool} defaultModelName={poolDefaultModel?.name || data?.settings.model_name || data?.settings.model || ''} onClose={() => setProjectConfigOpen(false)} onSave={configureProject} onArchive={archiveProject} onDelete={deleteProject} />}
    {projectDialog && <Modal title="新建研究项目" onClose={() => { if (!creatingProject) setProjectDialog(false); }}><form onSubmit={e => { e.preventDefault(); void createProject(); }}><label className="form-label">项目名称<input autoFocus value={projectName} maxLength={100} onChange={e => setProjectName(e.target.value)} placeholder="例如：明清地方社会研究" /></label><label className="form-label">项目类型<select value={projectType} onChange={e => setProjectType(e.target.value as Project['project_type'])}>{Object.entries(projectNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><p className="scope-note">可以先创建，再添加底本、参校资料和研究规则；类型不会限制其他研究工具</p><div className="modal-actions"><button type="button" className="button-secondary" disabled={creatingProject} onClick={() => setProjectDialog(false)}>取消</button><button className="button-primary" disabled={!projectName.trim() || creatingProject}>{creatingProject ? '创建中…' : '创建项目'}</button></div></form></Modal>}
  </div>;
}

function NavItem({ icon: Icon, label, active, count, onClick }: { icon: LucideIcon; label: string; active: boolean; count?: number; onClick(): void }) {
  return <button className={`nav-item ${active ? 'nav-item-active' : ''}`} aria-label={label} title={label} aria-current={active ? 'page' : undefined} onClick={onClick}><Icon size={18} /><span>{label}</span>{count ? <span className="nav-count">{count}</span> : null}</button>;
}

function LibraryView({portal,scope,onScope,...props}:React.ComponentProps<typeof KnowledgeWorkbench>&{portal:boolean;scope:'system'|'personal';onScope(scope:'system'|'personal'):void}) {return portal?<UserLibrary {...props} scope={scope} onScope={onScope}/>:<KnowledgeWorkbench {...props}/>;}
