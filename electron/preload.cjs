const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', Object.freeze({
  isRemote: () => ipcRenderer.invoke('connection:is-remote'),
  connectionInfo: () => ipcRenderer.invoke('connection:info'),
  connectService: config => ipcRenderer.invoke('connection:connect', config),
  request: (request) => ipcRenderer.invoke('api:request', request),
  serviceStatus: () => ipcRenderer.invoke('service:status'),
  setTheme: theme => ipcRenderer.invoke('window:theme',theme),
  streamKnowledge: (body, callback) => {
    const id = `kb_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const channel = `knowledge:stream:${id}`;
    const handler = (_event, value) => callback(value);
    ipcRenderer.on(channel, handler);
    const promise = ipcRenderer.invoke('api:knowledge-stream', { id, body }).finally(() => ipcRenderer.removeListener(channel, handler));
    return { promise, cancel: () => { ipcRenderer.send('api:knowledge-stream-cancel', id); ipcRenderer.removeListener(channel, handler); } };
  },
  pickDocuments: () => ipcRenderer.invoke('file:pick-documents'),
  chooseLiteratureSource: mode => ipcRenderer.invoke('library:choose-source',mode),
  saveFile: (file) => ipcRenderer.invoke('file:save', file),
  restoreBackup: () => ipcRenderer.invoke('library:restore-backup'),
  workspaceInfo: () => ipcRenderer.invoke('storage:info'),
  openWorkspace: () => ipcRenderer.invoke('storage:open'),
  changeWorkspace: mode => ipcRenderer.invoke('storage:choose',mode),
  onWorkspaceProgress: callback => { const handler=(_event,value)=>callback(value);ipcRenderer.on('storage:progress',handler);return ()=>ipcRenderer.removeListener('storage:progress',handler); },
  openExternal: (url) => ipcRenderer.invoke('app:open-external', url),
  onServiceReady: (callback) => {
    const handler = (_event, url) => callback(url);
    ipcRenderer.on('service:ready', handler);
    return () => ipcRenderer.removeListener('service:ready', handler);
  }
}));
