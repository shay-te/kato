// Kato desktop shell — launches the kato Python orchestrator as a
// child process, polls its built-in Flask webserver until it's
// listening, then opens the planning UI in a BrowserWindow.
//
// Why a wrapper and not a packaged Python runtime: kato pulls in
// hydra + a sandbox-aware Docker pipeline + per-OS git tooling.
// Bundling all of that into a self-contained binary is a rabbit
// hole. Treating the desktop app as a "kato launcher" keeps the
// shipping surface tiny — Electron + a child_process spawn — and
// lets the operator keep their existing ``./kato up`` workflow
// from the terminal when they prefer it.

'use strict';

const { app, BrowserWindow, Menu, shell, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

// Default kato webserver address. Same defaults the Python side
// reads from KATO_WEBSERVER_HOST / KATO_WEBSERVER_PORT — kept in
// sync so the launcher works without configuration on the
// "everything default" path.
const KATO_HOST = process.env.KATO_WEBSERVER_HOST || '127.0.0.1';
const KATO_PORT = Number(process.env.KATO_WEBSERVER_PORT || 5050);
const HEALTHCHECK_TIMEOUT_MS = 60 * 1000;     // give kato up to a minute to boot
const HEALTHCHECK_INTERVAL_MS = 500;

// Resolve the repo root. In development the desktop folder lives
// at ``<repo>/desktop`` so the repo is one level up. In a packaged
// build the repo is copied into ``resources/kato-repo`` (see
// ``extraResources`` in package.json) so we look there first.
function resolveKatoRoot() {
  if (app.isPackaged) {
    const packaged = path.join(process.resourcesPath, 'kato-repo');
    if (fs.existsSync(packaged)) { return packaged; }
  }
  const dev = path.resolve(__dirname, '..');
  return dev;
}

let katoProcess = null;
let mainWindow = null;
let katoStartupLog = '';

function buildKatoSpawn(katoRoot) {
  // Prefer ``./kato up`` (the canonical wrapper) when the bash
  // launcher is present + executable; otherwise fall back to
  // ``python3 -m kato_core_lib.main`` which is what the wrapper
  // ultimately calls.
  const wrapper = path.join(katoRoot, 'kato');
  let command;
  let args;
  if (fs.existsSync(wrapper)) {
    command = wrapper;
    args = ['up'];
  } else {
    command = 'python3';
    args = ['-m', 'kato_core_lib.main'];
  }
  return { command, args };
}

function spawnKato() {
  const katoRoot = resolveKatoRoot();
  const { command, args } = buildKatoSpawn(katoRoot);
  const child = spawn(command, args, {
    cwd: katoRoot,
    env: {
      ...process.env,
      KATO_WEBSERVER_HOST: KATO_HOST,
      KATO_WEBSERVER_PORT: String(KATO_PORT),
      // Force-disable the bypass-permissions confirmation prompts —
      // they require a TTY, which the spawned subprocess doesn't
      // have. Operators who want bypass mode should set this in
      // their .env before running.
    },
    // Inherit stdio so kato's logs are visible in the Electron
    // process's terminal — useful during development. In a
    // packaged build the user runs the .app from Finder so stdout
    // is invisible anyway; the cost is zero there.
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout?.on('data', (chunk) => {
    const text = chunk.toString();
    process.stdout.write(text);
    katoStartupLog += text;
  });
  child.stderr?.on('data', (chunk) => {
    const text = chunk.toString();
    process.stderr.write(text);
    katoStartupLog += text;
  });
  child.on('exit', (code, signal) => {
    katoProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      // If kato dies after the UI is up, show a dialog rather than
      // leaving the operator staring at a stale page.
      const msg = signal
        ? `kato exited with signal ${signal}`
        : `kato exited with code ${code}`;
      dialog.showErrorBox('kato stopped', `${msg}\n\nLast log lines:\n${katoStartupLog.slice(-2000)}`);
    }
  });
  return child;
}

function pingKato() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: KATO_HOST, port: KATO_PORT, path: '/', timeout: 1000 },
      (res) => {
        // Any HTTP response means the server is up. Status code
        // doesn't matter — the planning UI's index returns 200 but
        // a redirect or 404 would still mean "Flask answered".
        res.resume();
        resolve(true);
      },
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function waitForKato() {
  const deadline = Date.now() + HEALTHCHECK_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await pingKato()) { return true; }
    await new Promise((r) => setTimeout(r, HEALTHCHECK_INTERVAL_MS));
  }
  return false;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 960,
    minHeight: 600,
    title: 'Kato',
    backgroundColor: '#0a0a0a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    icon: path.join(__dirname, 'icons', 'icon.png'),
  });
  mainWindow.loadURL(`http://${KATO_HOST}:${KATO_PORT}/`);
  // Open external links (PR URLs, ticket URLs, etc.) in the
  // operator's real browser. Without this they'd open inside the
  // Electron window and the kato UI would become a navigation
  // history we don't manage.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: 'File',
      submenu: [
        { role: isMac ? 'close' : 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' }, { role: 'zoom' },
        ...(isMac
          ? [{ type: 'separator' }, { role: 'front' }, { type: 'separator' }, { role: 'window' }]
          : [{ role: 'close' }]),
      ],
    },
    {
      role: 'help',
      submenu: [
        {
          label: 'Kato on GitHub',
          click: () => shell.openExternal('https://github.com/'),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(async () => {
  buildMenu();
  katoProcess = spawnKato();
  const ready = await waitForKato();
  if (!ready) {
    dialog.showErrorBox(
      'Kato failed to start',
      `Could not reach http://${KATO_HOST}:${KATO_PORT}/ within `
      + `${HEALTHCHECK_TIMEOUT_MS / 1000}s.\n\nLast log lines:\n`
      + katoStartupLog.slice(-2000),
    );
    app.quit();
    return;
  }
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) { createWindow(); }
  });
});

app.on('window-all-closed', () => {
  // Quit on every platform — the desktop app's whole job is the
  // window. Background kato makes no sense here; leave that to
  // the CLI flow.
  app.quit();
});

app.on('before-quit', () => {
  if (katoProcess && !katoProcess.killed) {
    try { katoProcess.kill('SIGTERM'); } catch { /* already gone */ }
  }
});

// Belt-and-braces: if Electron itself crashes, the OS-level
// ``exit`` handler still tries to clean up the child so we don't
// leave an orphan kato + Flask thread bound to port 5050 (which
// would block the next launch with EADDRINUSE).
process.on('exit', () => {
  if (katoProcess && !katoProcess.killed) {
    try { katoProcess.kill('SIGKILL'); } catch { /* ignore */ }
  }
});
