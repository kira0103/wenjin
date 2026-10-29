import { workspaceStorage as localStorage } from './storage';
import { call } from './transport';
import type { SourcePreview } from './knowledge/documentTypes';

export type SelectedDocument = { name: string; data: ArrayBuffer | Blob };
const extensions = /\.(pdf|docx?|txt|md|png|jpe?g|tiff?)$/i;

function chooseFiles(directory = false): Promise<File[]> {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file'; input.multiple = true;
    input.accept = '.pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg,.tif,.tiff';
    if (directory) input.setAttribute('webkitdirectory', '');
    input.style.display = 'none';
    const finish = (files: File[]) => { input.remove(); resolve(files); };
    input.addEventListener('change', () => finish(Array.from(input.files || [])), { once: true });
    input.addEventListener('cancel', () => finish([]), { once: true });
    document.body.appendChild(input); input.click();
  });
}

type UploadState = { id: string; chunk_size: number; files: { offset: number; size: number }[] };

async function uploadLiterature(mode: 'files' | 'directory'): Promise<SourcePreview | null> {
  const selected = await chooseFiles(mode === 'directory');
  if (!selected.length) return null;
  const files = selected.filter(file => extensions.test(file.name));
  if (!files.length) throw new Error('没有找到支持的文献文件');
  const folder = selected[0].webkitRelativePath.split('/')[0];
  const manifest = files.map(file => ({ relative_path: mode === 'directory' ? file.webkitRelativePath.split('/').slice(1).join('/') : file.name, size: file.size }));
  // Retain only the upload ID and manifest signature; the access credential stays in an HttpOnly cookie.
  const fingerprint = JSON.stringify([mode, manifest, files.map(file => file.lastModified)]);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(fingerprint)))).map(byte => byte.toString(16).padStart(2, '0')).join('');
  const key = 'wenjin-upload-' + digest;
  let state: UploadState | undefined;
  let previous: string | null = null;
  try { previous = localStorage.getItem(key); } catch { /* Uploads work without local storage. */ }
  if (previous) {
    try { state = await call<UploadState>({ path: '/api/uploads/' + previous }); } catch { /* Create a new upload if the old service no longer has it. */ }
  }
  state ||= await call<UploadState>({ path: '/api/uploads', method: 'POST', body: { mode, label: mode === 'directory' ? folder : '所选文件', files: manifest } });
  try { localStorage.setItem(key, state.id); } catch { /* The current upload still completes. */ }
  for (let index = 0; index < files.length; index++) {
    let offset = state.files[index].offset;
    while (offset < files[index].size) {
      const result = await call<{ offset: number }>({ path: `/api/uploads/${state.id}/files/${index}?offset=${offset}`, method: 'PUT', body: files[index].slice(offset, offset + state.chunk_size) });
      offset = result.offset;
    }
  }
  const preview = await call<SourcePreview>({ path: `/api/uploads/${state.id}/preview`, method: 'POST' });
  return { ...preview, ignored: (preview.ignored || 0) + selected.length - files.length };
}

export const platform = {
  isDesktop: !!window.desktop,
  setTheme: (theme: 'light' | 'dark') => window.desktop?.setTheme?.(theme) || Promise.resolve(),
  serviceStatus: () => window.desktop?.serviceStatus?.() || Promise.resolve({ stage: 'connecting', message: '正在连接资料服务', elapsed: 0 }),
  onServiceReady(callback: (url: string) => void) {
    if (window.desktop) return window.desktop.onServiceReady(callback);
    const handler = () => callback(location.origin);
    window.addEventListener('wenjin:connected', handler);
    return () => window.removeEventListener('wenjin:connected', handler);
  },
  async pickDocuments(): Promise<SelectedDocument[]> {
    if (window.desktop) return window.desktop.pickDocuments();
    return (await chooseFiles()).map(file => ({ name: file.name, data: file }));
  },
  chooseLiteratureSource: (mode: 'files' | 'directory') => window.desktop ? window.desktop.chooseLiteratureSource(mode) : uploadLiterature(mode),
  async saveFile(file: { filename: string; data: ArrayBuffer }) {
    if (window.desktop) return window.desktop.saveFile(file);
    const url = URL.createObjectURL(new Blob([file.data]));
    const link = document.createElement('a'); link.href = url; link.download = file.filename;
    document.body.appendChild(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  },
  async openExternal(url: string) {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('只支持 HTTP 和 HTTPS 链接');
    if (window.desktop) return window.desktop.openExternal(url);
    window.open(parsed.href, '_blank', 'noopener,noreferrer');
  },
  async isRemote() {
    if (!window.desktop) return true;
    return window.desktop.isRemote ? window.desktop.isRemote() : false;
  },
  workspaceInfo: () => window.desktop.workspaceInfo(),
  openWorkspace: () => window.desktop.openWorkspace(),
  changeWorkspace: (mode: 'migrate' | 'switch') => window.desktop.changeWorkspace(mode),
  onWorkspaceProgress: (callback: Parameters<Window['desktop']['onWorkspaceProgress']>[0]) => window.desktop.onWorkspaceProgress(callback),
  restoreBackup: () => window.desktop.restoreBackup(),
};
