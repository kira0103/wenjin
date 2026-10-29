/* Browser-only UI and optional packaged thin-client smoke check. No research data. */
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');

const project = path.resolve(__dirname, '..');
const output = path.join(project, '.local', 'client-server-gui', crypto.randomUUID());
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'browser-profile'));
app.on('window-all-closed', () => {});
const token = crypto.randomBytes(32).toString('hex');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const port = () => new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const chosen = server.address().port; server.close(() => resolve(chosen)); }); });
async function until(check, label, seconds = 40) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) { try { const value = await check(); if (value) return value; } catch {} await delay(100); }
  throw Error('Timed out: ' + label);
}
let service, client, window;
const errors = [];
const log = fs.openSync(path.join(output, 'service.log'), 'w');

async function debuggerClient(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let identifier = 0;
  const pending = new Map();
  socket.addEventListener('message', event => { const value = JSON.parse(event.data); const handler = pending.get(value.id); if (handler) { pending.delete(value.id); handler(value); } });
  return {
    async evaluate(expression) {
      const id = ++identifier;
      const response = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(Error('Debugger timeout')); }, 10000);
        pending.set(id, value => { clearTimeout(timer); value.error || value.result?.exceptionDetails ? reject(Error('Client evaluation failed')) : resolve(value.result.result.value); });
      });
      socket.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
      return response;
    },
    close: () => socket.close(),
  };
}

app.whenReady().then(async () => {
  const selectedPort = await port(), base = `http://127.0.0.1:${selectedPort}`;
  const environment = { ...process.env, SHIXUE_SERVER_TOKEN: token, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring', HF_HUB_OFFLINE: '1' };
  delete environment.SHIXUE_KB_DATABASE_URL; delete environment.SHIXUE_MODEL_API_KEY;
  service = spawn(path.join(project, '.venv', 'Scripts', 'python.exe'), ['-m', 'backend.server', '--data-dir', path.join(output, 'data'), '--ui-dir', path.join(project, 'dist'), '--port', String(selectedPort)], { cwd: project, env: environment, windowsHide: true, stdio: ['ignore', log, log] });
  await until(async () => (await fetch(base + '/health')).ok, 'API startup');
  window = new BrowserWindow({ show: false, width: 1500, height: 1000, webPreferences: { offscreen: true, backgroundThrottling: false, contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.on('console-message', (_event, _level, message) => { if (/Uncaught|ReferenceError|TypeError/.test(message)) errors.push(message); });
  const js = source => window.webContents.executeJavaScript(source);
  await window.loadURL(base);
  await until(() => js("!!document.querySelector('input[type=password]:not(:disabled)')"), 'browser login');
  assert.equal(await js('typeof window.desktop'), 'undefined');
  await js(`(()=>{const input=document.querySelector('input[type=password]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(token)});input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await delay(100);
  await js("document.querySelector('form').requestSubmit()");
  await until(() => js("document.body.innerText.includes('我的研究')"), 'browser workspace');
  async function click(text) {
    await js(`(()=>{const button=[...document.querySelectorAll('button')].find(item=>item.textContent.trim()===${JSON.stringify(text)});if(!button||button.disabled)throw Error('Button unavailable');button.click();})()`);
    await delay(100);
  }
  await click('我的研究'); await click('新建研究项目');
  await js("(()=>{const input=document.querySelector('input[placeholder=\"例如：明清地方社会研究\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'前后端分离验证');input.dispatchEvent(new Event('input',{bubbles:true}));})()");
  await delay(100); await click('创建项目');
  await until(() => js("document.body.innerText.includes('前后端分离验证')"), 'browser project creation');
  assert.ok(await js("fetch('/api/projects').then(response=>response.json()).then(items=>items.some(item=>item.name==='前后端分离验证'))"));
  fs.writeFileSync(path.join(output, 'browser.png'), (await window.webContents.capturePage()).toPNG());
  const report = { browser_without_desktop_bridge: true, session_login: true, project_saved_through_api: true, packaged_client: false, server_survives_client_close: false, renderer_errors: errors };
  window.destroy(); window = null;
  const binaryIndex = process.argv.indexOf('--client-binary');
  if (binaryIndex >= 0) {
    const binary = path.resolve(process.argv[binaryIndex + 1]), debugPort = await port();
    assert.ok(!fs.existsSync(path.join(path.dirname(binary), 'resources', 'backend')));
    assert.ok(!fs.existsSync(path.join(path.dirname(binary), 'resources', 'postgres')));
    client = spawn(binary, [`--remote-debugging-port=${debugPort}`, '--remote-debugging-address=127.0.0.1'], { cwd: project, env: { ...environment, SHIXUE_SERVER_URL: base, SHIXUE_HEADLESS: '1', SHIXUE_DESKTOP_PROFILE: path.join(output, 'client-profile') }, windowsHide: true, stdio: 'ignore' });
    const page = await until(async () => (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(item=>item.type==='page'), 'thin client debugger');
    const debuggerSession = await debuggerClient(page.webSocketDebuggerUrl);
    await until(() => debuggerSession.evaluate("document.body.innerText.includes('我的研究')"), 'thin client workspace');
    assert.equal(await debuggerSession.evaluate('window.desktop.isRemote()'), true);
    assert.equal(await debuggerSession.evaluate("window.desktop.request({path:'/api/projects'}).then(result=>result.body.some(item=>item.name==='前后端分离验证'))"), true);
    await debuggerSession.evaluate('window.close();true').catch(()=>{});
    debuggerSession.close();
    await until(() => client.exitCode !== null, 'thin client close', 15);
    assert.equal(client.exitCode, 0);
    assert.ok((await fetch(base + '/health')).ok);
    report.packaged_client = true; report.server_survives_client_close = true;
  }
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, output }));
}).then(() => cleanup(0)).catch(error => { console.error(error.message); cleanup(1); });

async function cleanup(code) {
  if (window && !window.isDestroyed()) window.destroy();
  for (const child of [client, service]) if (child && child.exitCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); }
  fs.closeSync(log); app.exit(code);
}
