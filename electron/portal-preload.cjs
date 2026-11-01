const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('portalConnection', {
  connect: value => ipcRenderer.invoke('portal:connect', value),
});
