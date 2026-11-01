/* Exercise the desktop window and account login against a disposable portal. */
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'), net = require('node:net');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const project = path.resolve(__dirname, '..'), output = path.join(project, '.local', 'desktop-portal-gui', crypto.randomUUID());
fs.mkdirSync(output, { recursive: true });
app.setPath('userData', path.join(output, 'profile'));
const password = crypto.randomBytes(24).toString('hex');
const log = fs.openSync(path.join(output, 'fixture.log'), 'w');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const freePort = () => new Promise(resolve => { const probe = net.createServer(); probe.listen(0, '127.0.0.1', () => { const port = probe.address().port; probe.close(() => resolve(port)); }); });
async function until(check, label) { const end = Date.now() + 100000; while (Date.now() < end) { try { if (await check()) return; } catch {} await delay(100); } throw Error('Timed out: ' + label); }
let fixture, window;
const js = code => window.webContents.executeJavaScript(code);

(async () => {
  const port = await freePort(), base = `http://127.0.0.1:${port}`;
  fixture = spawn(path.join(project, '.venv', 'Scripts', 'python.exe'), [path.join(project, 'scripts', 'portal_fixture.py'), '--root', path.join(output, 'data'), '--port', String(port)],
    { cwd: project, env: { ...process.env, SHIXUE_GUI_PASSWORD: password }, windowsHide: true, stdio: ['ignore', log, log] });
  await until(async () => (await fetch(base + '/health')).ok, 'fixture');
  const started = Date.now();
  require('../electron/portal.cjs').start({ profile: path.join(output, 'profile'), show: false, initialUrl: base });
  await until(() => (window = BrowserWindow.getAllWindows()[0]), 'desktop window');
  await until(() => js("!!document.querySelector('input[autocomplete=username]')"), 'desktop login');
  const loginMs = Date.now() - started;
  assert.equal(await js('typeof window.desktop'), 'undefined');
  assert.equal(await js('location.pathname'), '/app/');
  await js(`(()=>{for(const [name,value] of [['username','alice'],['current-password',${JSON.stringify(password)}]]){const input=document.querySelector('input[autocomplete="'+name+'"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));}})()`);
  await delay(100);
  await js("document.querySelector('form').requestSubmit()");
  const accountStart = Date.now();
  await until(() => js("document.querySelector('.statusbar')?.textContent.includes('资料服务已连接')"), 'authenticated desktop workspace');
  const firstWorkspaceMs = Date.now() - accountStart;
  assert.equal(await js("fetch('/api/admin/users').then(value=>value.status)"), 403);
  assert.equal(await js("window.portalConnection.connect('http://untrusted.invalid').then(value=>!!value.error)"), true);
  const timings = await js("(async()=>{const samples=[];for(let i=0;i<5;i++){const start=performance.now();await fetch('/api/projects');samples.push(Math.round(performance.now()-start));}return samples;})()");
  fs.writeFileSync(path.join(output, 'desktop.png'), (await window.webContents.capturePage()).toPNG());
  const stored = fs.readFileSync(path.join(output, 'profile', 'connection.json'), 'utf8');
  assert.equal(stored.includes(password), false);
  assert.deepEqual(JSON.parse(stored), { url: base });
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({ desktop_entry: true, account_login: true, business_bridge_absent: true, administrator_denied: true, connection_ipc_restricted: true, login_ms: loginMs, first_workspace_ms: firstWorkspaceMs, warm_api_ms: timings }, null, 2));
  console.log(JSON.stringify({ verified: true, output, login_ms: loginMs, first_workspace_ms: firstWorkspaceMs, warm_api_ms: timings }));
})().then(() => cleanup(0)).catch(async error => { console.error(error.stack); if (window && !window.isDestroyed()) fs.writeFileSync(path.join(output, 'failure.txt'), await js('document.body.innerText')); await cleanup(1); });

async function cleanup(code) {
  if (fixture && fixture.exitCode === null) {
    fs.writeFileSync(path.join(output, 'data', 'gui.stop'), 'stop');
    await Promise.race([new Promise(resolve => fixture.once('exit', resolve)), delay(120000)]);
    if (fixture.exitCode === null) { fixture.kill(); code = 1; }
  }
  fs.closeSync(log);
  app.exit(code);
}
