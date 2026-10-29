const fs = require('node:fs');
const path = require('node:path');

function normalizeUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw Error('请输入完整的资料服务地址'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw Error('服务地址应为 HTTP 或 HTTPS 根地址');
  return url.origin;
}

function loadConnection(filename, encryption) {
  if (!fs.existsSync(filename)) return null;
  const value = JSON.parse(fs.readFileSync(filename, 'utf8'));
  return { url: normalizeUrl(value.url), token: value.encryptedToken && encryption.isEncryptionAvailable() ? encryption.decryptString(Buffer.from(value.encryptedToken, 'base64')) : '' };
}

function saveConnection(filename, config, encryption) {
  const value = { url: normalizeUrl(config.url) };
  if (encryption.isEncryptionAvailable() && encryption.getSelectedStorageBackend?.() !== 'basic_text') value.encryptedToken = encryption.encryptString(config.token).toString('base64');
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const temporary = filename + '.tmp';
  fs.writeFileSync(temporary, JSON.stringify(value), { mode: 0o600 });
  fs.renameSync(temporary, filename);
}

module.exports = { normalizeUrl, loadConnection, saveConnection };
