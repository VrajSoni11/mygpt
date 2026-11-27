const { app, BrowserWindow, globalShortcut, ipcMain, screen, Notification, Menu, session } = require('electron');
const path = require('path');
const fs = require('fs');

const API = 'http://127.0.0.1:8000';
const DOT_BIG = 104;    // window size while the sphere is showing (on hover)
const DOT_SMALL = 26;   // window size for the tiny dot at rest
const PANEL_W = 440;
const HOTKEY = 'Control+Alt+Space';
const VOICE_HOTKEY = 'Control+Alt+V';

let dotWin = null;
let panelWin = null;
let panelVisible = false;
let pinned = false;
let dragOrigin = null;
let dotSize = DOT_SMALL;

if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}
app.setAppUserModelId('com.loki.personal');

const dotPosFile = () => path.join(app.getPath('userData'), 'dot-position.json');

// Where the dot was last left (its centre), if that spot is still on a screen
function loadDotCenter() {
  try {
    const { cx, cy } = JSON.parse(fs.readFileSync(dotPosFile(), 'utf8'));
    const onScreen = screen.getAllDisplays().some((d) => {
      const w = d.workArea;
      return cx >= w.x && cx <= w.x + w.width && cy >= w.y && cy <= w.y + w.height;
    });
    if (onScreen) return { cx, cy };
  } catch (e) {
    // no saved position yet
  }
  const wa = screen.getPrimaryDisplay().workArea;
  return { cx: wa.x + wa.width - 56, cy: wa.y + Math.round(wa.height / 2) };
}

function saveDotCenter() {
  if (!dotWin || dotWin.isDestroyed()) return;
  const b = dotWin.getBounds();
  try {
    fs.writeFileSync(dotPosFile(), JSON.stringify({ cx: b.x + b.width / 2, cy: b.y + b.height / 2 }));
  } catch (e) {
    // not critical
  }
}

// Grow or shrink the dot's window around its centre (so the dot never appears to jump)
function setDotSize(size) {
  if (!dotWin || dotWin.isDestroyed() || size === dotSize) return;
  const b = dotWin.getBounds();
  const cx = b.x + b.width / 2;
  const cy = b.y + b.height / 2;
  dotSize = size;
  dotWin.setBounds({ x: Math.round(cx - size / 2), y: Math.round(cy - size / 2), width: size, height: size });
}

function createDot() {
  const { cx, cy } = loadDotCenter();
  dotWin = new BrowserWindow({
    width: DOT_SMALL,
    height: DOT_SMALL,
    x: Math.round(cx - DOT_SMALL / 2),
    y: Math.round(cy - DOT_SMALL / 2),
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
  setDotSize(DOT_SMALL);
  dotWin.webContents.send('dot-reset');
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
  dragOrigin = { cx: c.x, cy: c.y, mx: b.x + b.width / 2, my: b.y + b.height / 2 };
});
ipcMain.on('drag-move', () => {
  if (!dragOrigin) return;
  const c = screen.getCursorScreenPoint();
  const mx = dragOrigin.mx + c.x - dragOrigin.cx;
  const my = dragOrigin.my + c.y - dragOrigin.cy;
  // fixed size on every call avoids window-size drift on scaled displays
  dotWin.setBounds({ x: Math.round(mx - dotSize / 2), y: Math.round(my - dotSize / 2), width: dotSize, height: dotSize });
});
ipcMain.on('drag-end', () => {
  dragOrigin = null;
  saveDotCenter();
});
ipcMain.on('dot-size', (_e, mode) => setDotSize(mode === 'big' ? DOT_BIG : DOT_SMALL));

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