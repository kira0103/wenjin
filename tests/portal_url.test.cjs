const { test } = require('node:test');
const assert = require('node:assert/strict');
const { portalUrl, DEFAULT_PORTAL_URL } = require('../electron/portal-url.cjs');

test('desktop portal connects to one HTTP origin with an account session', () => {
  assert.equal(portalUrl(), DEFAULT_PORTAL_URL);
  assert.equal(portalUrl('https://research.example/app/'), 'https://research.example');
  for (const url of ['file:///C:/private', 'javascript:alert(1)', 'https://user:password@example.org', 'https://example.org/api', 'http://example.org/?token=secret', 'https://example.org/#token']) {
    assert.throws(() => portalUrl(url));
  }
});
