const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  dotClick: () => ipcRenderer.send('dot-click'),
  dragStart: () => ipcRenderer.send('drag-start'),
  dragMove: () => ipcRenderer.send('drag-move'),
  dragEnd: () => ipcRenderer.send('drag-end'),
  dotSize: (mode) => ipcRenderer.send('dot-size', mode),
  dotMenu: () => ipcRenderer.send('dot-menu'),
  hidePanel: () => ipcRenderer.send('panel-hide'),
  setPinned: (value) => ipcRenderer.send('panel-pin', value),
  quit: () => ipcRenderer.send('app-quit'),
  onAlert: (cb) => ipcRenderer.on('dot-alert', () => cb()),
  onDotReset: (cb) => ipcRenderer.on('dot-reset', () => cb()),
  onPanelShown: (cb) => ipcRenderer.on('panel-shown', () => cb()),
  onVoiceToggle: (cb) => ipcRenderer.on('voice-toggle', () => cb()),
  onReminder: (cb) => ipcRenderer.on('reminder', (_event, r) => cb(r)),
});