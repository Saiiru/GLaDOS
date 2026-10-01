const { contextBridge, ipcRenderer } = require('electron');

const api = Object.freeze({
  window: Object.freeze({
    minimize: () => ipcRenderer.send('window-minimize'),
    maximize: () => ipcRenderer.send('window-maximize'),
    close: () => ipcRenderer.send('window-close'),
  }),
  openExternal: (url) => ipcRenderer.invoke('ada:open-external', url),
  getSocketAuth: () => ipcRenderer.invoke('ada:socket-auth'),
});

contextBridge.exposeInMainWorld('adaDesktop', api);
