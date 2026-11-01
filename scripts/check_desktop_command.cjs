/* Run the real package command, including Electron's package-entry loader. */
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.local', 'desktop-command', crypto.randomUUID());
fs.mkdirSync(output, { recursive: true });
const server = http.createServer((request, response) => {
  if (request.url === '/health') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ ok: true, mode: 'portal' })); }
  else { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end('<!doctype html><title>问津登录验收</title><h1>登录</h1><input autocomplete="username"><input type="password">'); }
});
async function run(name, address, visible=false) {
  const profile = path.join(output, name);
  const child = spawn(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', 'npm run dev -- '+(visible?'--smoke-test-visible':'--smoke-test')], {
    cwd: root, env: { ...process.env, SHIXUE_CLIENT_PROFILE: profile, SHIXUE_PORTAL_URL: address }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', data => { log += data.toString(); }); child.stderr.on('data', data => { log += data.toString(); });
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Real desktop command timed out: ' + name)), 30000);
    child.once('error', reject); child.once('exit', value => { clearTimeout(timer); resolve(value); });
  });
  fs.writeFileSync(path.join(output, name + '.log'), log);
  assert.equal(code, 0, log);
  return JSON.parse(fs.readFileSync(path.join(profile, 'startup.json'), 'utf8'));
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = `http://127.0.0.1:${server.address().port}`;
  const online = await run('online', address);
  assert.equal(online.stage, 'ready'); assert.equal(online.page, address + '/app/'); assert.equal(online.title, '问津登录验收');
  const visible=await run('visible-online',address,true);
  assert.equal(visible.stage,'ready');assert.equal(visible.visible,true);
  await new Promise(resolve => server.close(resolve));
  const offline = await run('offline', address);
  assert.equal(offline.stage, 'connection'); assert.ok(offline.page.startsWith('file:')); assert.equal(offline.title, '连接问津后台');
  const visibleOffline=await run('visible-offline',address,true);
  assert.equal(visibleOffline.stage,'connection');assert.equal(visibleOffline.visible,true);
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({ npm_run_dev: true, online_window: true, offline_connection_window: true }, null, 2));
  console.log(JSON.stringify({ verified: true, output }));
})().catch(error => { console.error(error.stack); process.exitCode = 1; }).finally(() => server.close());
