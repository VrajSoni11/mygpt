const { app, BrowserWindow, globalShortcut, ipcMain, screen, Notification, Menu, session } = require('electron');
const path = require('path');

const API = 'http://127.0.0.1:8000';
const DOT_SIZE = 104;
const PANEL_W = 440;
const HOTKEY = 'Control+Alt+Space';
const VOICE_HOTKEY = 'Control+Alt+V';

let dotWin = null;
let panelWin = null;
let panelVisible = false;
let pinned = false;
let dragOrigin = null;

if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}
app.setAppUserModelId('com.loki.personal');

function createDot() {
  const wa = screen.getPrimaryDisplay().workArea;
  dotWin = new BrowserWindow({
    width: DOT_SIZE,
    height: DOT_SIZE,
    x: wa.x + wa.width - DOT_SIZE - 12,
    y: wa.y + Math.round(wa.height / 2),
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  dotWin.setAlwaysOnTop(true, 'screen-saver');
  dotWin.loadFile(path.join(__dirname, 'dot.html'));
}

function createPanel() {
  const wa = screen.getPrimaryDisplay().workArea;
  panelWin = new BrowserWindow({
    width: PANEL_W,
    height: wa.height,
    x: wa.x + wa.width - PANEL_W,
    y: wa.y,
    show: false,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    backgroundColor: '#000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  panelWin.loadFile(path.join(__dirname, 'panel.html'));
  panelWin.on('blur', () => {
    if (panelVisible && !pinned) hidePanel();
  });
}

function showPanel() {
  const wa = screen.getDisplayMatching(dotWin.getBounds()).workArea;
  panelWin.setBounds({ x: wa.x + wa.width - PANEL_W, y: wa.y, width: PANEL_W, height: wa.height });
  dotWin.hide();
  panelWin.show();
  panelWin.focus();
  panelVisible = true;
  panelWin.webContents.send('panel-shown');
}

function hidePanel() {
  panelWin.hide();
  panelVisible = false;
  dotWin.showInactive();
}

function togglePanel() {
  panelVisible ? hidePanel() : showPanel();
}

// ---------- IPC from the dot and the panel ----------
ipcMain.on('dot-click', showPanel);
ipcMain.on('panel-hide', hidePanel);
ipcMain.on('panel-pin', (_e, value) => {
  pinned = !!value;
});
ipcMain.on('app-quit', () => app.quit());

ipcMain.on('drag-start', () => {
  const c = screen.getCursorScreenPoint();
  const b = dotWin.getBounds();
  dragOrigin = { cx: c.x, cy: c.y, x: b.x, y: b.y };
});
ipcMain.on('drag-move', () => {
  if (!dragOrigin) return;
  const c = screen.getCursorScreenPoint();
  // setBounds with a fixed size avoids window-size drift on scaled displays
  dotWin.setBounds({
    x: dragOrigin.x + c.x - dragOrigin.cx,
    y: dragOrigin.y + c.y - dragOrigin.cy,
    width: DOT_SIZE,
    height: DOT_SIZE,
  });
});

ipcMain.on('dot-menu', () => {
  Menu.buildFromTemplate([
    { label: 'Open LOKI', click: showPanel },
    { type: 'separator' },
    { label: 'Quit LOKI', click: () => app.quit() },
  ]).popup({ window: dotWin });
});

// ---------- Reminders: check the backend and notify ----------
async function checkReminders() {
  try {
    const res = await fetch(API + '/reminders/due');
    if (!res.ok) return;
    const due = await res.json();
    for (const r of due) {
      new Notification({ title: 'LOKI reminder', body: r.text }).show();
      if (dotWin && !dotWin.isDestroyed()) dotWin.webContents.send('dot-alert');
      if (panelWin && !panelWin.isDestroyed()) panelWin.webContents.send('reminder', r);
    }
  } catch (e) {
    // backend not running yet; try again next time
  }
}

app.whenReady().then(() => {
  // Allow the microphone (and nothing else) for our own windows
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'media'));
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media');

  createDot();
  createPanel();
  globalShortcut.register(HOTKEY, togglePanel);
  globalShortcut.register(VOICE_HOTKEY, () => {
    if (!panelVisible) showPanel();
    panelWin.webContents.send('voice-toggle');
  });
  setInterval(checkReminders, 15000);
});

// Keep running in the background (there is no taskbar window to close)
app.on('window-all-closed', () => {});
app.on('will-quit', () => globalShortcut.unregisterAll());