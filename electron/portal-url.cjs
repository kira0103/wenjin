const DEFAULT_PORTAL_URL = 'http://127.0.0.1:8765';

function portalUrl(value = DEFAULT_PORTAL_URL) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash || !['/', '/app', '/app/'].includes(url.pathname)) {
    throw new Error('请填写后台的 HTTP 或 HTTPS 地址，例如 http://127.0.0.1:8765');
  }
  return url.origin;
}
module.exports = { DEFAULT_PORTAL_URL, portalUrl };
