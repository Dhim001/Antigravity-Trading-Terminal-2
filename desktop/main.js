/**
 * Electron shell — opens the Vite dev UI in a standalone window (no browser chrome).
 * Backend + Vite are started by scripts/start-desktop.ps1 before this process launches.
 */

const { app, BrowserWindow, shell, Menu } = require('electron');
const http = require('http');
const path = require('path');

// GPU: full disableHardwareAcceleration() often opens a blank window on Windows.
// Default to normal GPU with sandbox disabled; opt into SwiftShader via env if needed.
if (process.platform === 'win32') {
  app.commandLine.appendSwitch('disable-gpu-sandbox');
  if (process.env.TERMINAL_ELECTRON_SOFTWARE_GPU === '1') {
    app.disableHardwareAcceleration();
    app.commandLine.appendSwitch('use-angle', 'swiftshader');
  }
}

const PROFILES = {
  sim: { dev: 5173, label: 'Simulated' },
  ib: { dev: 5174, label: 'IB' },
  massive: { dev: 5175, label: 'Massive' },
};

function parseProfile() {
  const fromArg = process.argv.find((a) => a.startsWith('--profile='));
  if (fromArg) {
    const key = fromArg.split('=')[1]?.toLowerCase();
    if (PROFILES[key]) return key;
  }
  const fromEnv = (process.env.TERMINAL_PROFILE || process.env.VITE_TERMINAL_PROFILE || 'sim').toLowerCase();
  return PROFILES[fromEnv] ? fromEnv : 'sim';
}

function profileUrl(profileKey) {
  const port = PROFILES[profileKey].dev;
  return `http://127.0.0.1:${port}`;
}

function waitForHttp(url, timeoutMs = 120_000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();

    const attempt = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) {
          resolve();
          return;
        }
        schedule();
      });
      req.on('error', schedule);
      req.setTimeout(2_000, () => {
        req.destroy();
        schedule();
      });
    };

    const schedule = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Timed out waiting for ${url}`));
        return;
      }
      setTimeout(attempt, 500);
    };

    attempt();
  });
}

const profileKey = parseProfile();
const profile = PROFILES[profileKey];
const appUrl = profileUrl(profileKey);

// Allow sim + ib + massive desktop windows in parallel.
app.setPath('userData', path.join(app.getPath('appData'), 'AntigravityTerminal', profileKey));

let mainWindow = null;

function buildMenu() {
  const template = [
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
    {
      label: 'Window',
      submenu: [{ role: 'minimize' }, { role: 'close' }],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: `Antigravity Trading Terminal (${profile.label})`,
    backgroundColor: '#0f0f14',
    autoHideMenuBar: true,
    show: false,
    icon: path.join(__dirname, '..', 'frontend', 'public', 'icons', 'icon-512.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // Dev UI is localhost-only; allow Vite module/HMR loads in Electron.
      webSecurity: false,
    },
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error(`Load failed [${errorCode}] ${errorDescription} — ${validatedURL}`);
  });

  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (level >= 2) {
      console.error(`[renderer] ${message} (${sourceId}:${line})`);
    }
  });

  mainWindow.loadURL(appUrl);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    // Same-origin app windows (standalone ML Lab at ?panel=ml-lab, etc.).
    const sameOrigin = url.startsWith(appUrl);
    const blank = !url || url === 'about:blank' || url.startsWith('about:blank');
    if (sameOrigin || blank) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width: sameOrigin ? 1120 : 1120,
          height: sameOrigin ? 820 : 820,
          minWidth: 720,
          minHeight: 480,
          backgroundColor: '#0f0f14',
          autoHideMenuBar: true,
          title: sameOrigin ? 'ML Lab · Antigravity' : 'Antigravity Panel',
          webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,
            webSecurity: false,
          },
        },
      };
    }
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = url.startsWith(appUrl) || url.startsWith(`${appUrl}/`);
    if (!allowed) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  buildMenu();
  try {
    await waitForHttp(appUrl);
  } catch (err) {
    console.error(err.message);
    app.exit(1);
    return;
  }
  createWindow();
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
