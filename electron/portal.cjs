// The desktop is an authenticated portal window. Business services run on the server.
const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { DEFAULT_PORTAL_URL, portalUrl } = require('./portal-url.cjs');
function start({ profile = path.join(app.getPath('appData'), 'wenjin-portal-client'), show = true, initialUrl } = {}) {
  app.setPath('userData', profile);
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  let window, origin, connecting = false;
  const configuration = path.join(app.getPath('userData'), 'connection.json');
  const setupFile = path.join(__dirname, 'portal-connection.html');
  function record(stage) {
    fs.mkdirSync(profile, { recursive: true });
    fs.writeFileSync(path.join(profile, 'startup.json'), JSON.stringify({ stage, pid: process.pid, address: origin || DEFAULT_PORTAL_URL,
      page: window && !window.isDestroyed() ? window.webContents.getURL() : '',
      title: window && !window.isDestroyed() ? window.webContents.getTitle() : '',
      visible: Boolean(window && !window.isDestroyed() && window.isVisible()) }));
  }
  app.on('second-instance', () => {
    if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  });

  function showConnection() {
    connecting = false;
    record('connection');
    return window.loadFile(setupFile, { query: { url: origin || DEFAULT_PORTAL_URL } });
  }

  async function connect(value) {
    if (connecting) return { error: '正在连接，请稍候' };
    try {
      const next = portalUrl(value);
      connecting = true;
      const health = await window.webContents.session.fetch(next + '/health', { signal: AbortSignal.timeout(5000) });
      if (!health.ok || (await health.json()).mode !== 'portal') throw new Error('该地址不是问津后台服务');
      origin = next;
      fs.mkdirSync(path.dirname(configuration), { recursive: true });
      fs.writeFileSync(configuration, JSON.stringify({ url: origin }));
      await window.loadURL(origin + '/app/');
      record('ready');
      return {};
    } catch (error) {
      return { error: '无法连接后台。请先运行 npm start，并检查服务地址。' };
    } finally { connecting = false; }
  }

  const ready = app.whenReady().then(async () => {
    try { origin = portalUrl(initialUrl || process.env.SHIXUE_PORTAL_URL || JSON.parse(fs.readFileSync(configuration, 'utf8')).url); }
    catch { origin = DEFAULT_PORTAL_URL; }
    window = new BrowserWindow({ show, width: 1440, height: 960, minWidth: 1000, minHeight: 700, title: '问津客户端',
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, preload: path.join(__dirname, 'portal-preload.cjs') } });
    record('window-created');
    if (show) window.focus();
    ipcMain.handle('portal:connect', (event, value) => {
      if (event.sender !== window.webContents || !event.senderFrame.url.startsWith('file:') || new URL(event.senderFrame.url).pathname !== new URL(require('node:url').pathToFileURL(setupFile).href).pathname) return { error: '无效的连接请求' };
      return connect(value);
    });
    window.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:/.test(url)) void shell.openExternal(url);
      return { action: 'deny' };
    });
    window.webContents.on('will-navigate', (event, url) => {
      if (new URL(url).origin !== origin) { event.preventDefault(); if (/^https?:/.test(url)) void shell.openExternal(url); }
    });
    window.webContents.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {
      if (mainFrame && code !== -3 && !window.webContents.getURL().startsWith('file:')) showConnection();
    });
    // A local connection screen appears immediately, including when the server is offline.
    await showConnection();
    const result = await connect(origin);
    if (result.error && !window.isDestroyed()) await showConnection();
  }).catch(error => {
    console.error('客户端启动失败：', error.message);
    try { record('failed'); } catch {}
    if (show) dialog.showErrorBox('问津客户端启动失败', '客户端未能打开。请重新启动客户端，并检查后台连接地址。');
    app.exit(1);
  });
  app.on('window-all-closed', () => app.quit());
  return ready;
}
module.exports = { start };
