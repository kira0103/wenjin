const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { normalizeUrl, loadConnection, saveConnection } = require('../electron/connection.cjs');
const { uploadSelection } = require('../electron/uploads.cjs');

test('client accepts service origins and rejects credentials and URL suffixes', () => {
  assert.equal(normalizeUrl('https://example.com/'), 'https://example.com');
  assert.equal(normalizeUrl('http://127.0.0.1:8765'), 'http://127.0.0.1:8765');
  for (const url of ['file:///tmp', 'https://key@example.com', 'https://example.com/api', 'https://example.com/?key=x', 'https://example.com/#key']) assert.throws(() => normalizeUrl(url));
});

test('saved connection encrypts the credential and supports ephemeral credentials', () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'wenjin-client-'));
  const filename = path.join(folder, 'connection.json');
  const encryption = { isEncryptionAvailable: () => true, encryptString: value => Buffer.from('encrypted:' + value), decryptString: bytes => bytes.toString().slice(10) };
  try {
    assert.equal(loadConnection(filename, encryption), null);
    saveConnection(filename, {url:'https://example.com',token:'private-access-key'}, encryption);
    const value = JSON.parse(fs.readFileSync(filename));
    assert.equal(value.token, undefined);
    assert.ok(value.encryptedToken);
    assert.equal(loadConnection(filename, encryption).token, 'private-access-key');
    saveConnection(filename, {url:'https://example.com',token:'ephemeral-key'}, {...encryption,isEncryptionAvailable:()=>false});
    assert.ok(!fs.readFileSync(filename,'utf8').includes('ephemeral-key'));
    assert.equal(loadConnection(filename, encryption).token, '');
  } finally { fs.rmSync(folder, {recursive:true,force:true}); }
});

test('desktop directory upload resumes bytes after a failed connection without persisting credentials', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'wenjin-upload-'));
  const source = path.join(folder, 'sources'), checkpoint = path.join(folder, 'checkpoints');
  fs.mkdirSync(source); fs.writeFileSync(path.join(source, '原文.txt'), 'abcdefghij');
  const previousFetch = global.fetch;
  const id = 'a'.repeat(32);
  let received = '', fail = true, creations = 0;
  global.fetch = async (url, options = {}) => {
    assert.equal(options.headers['X-App-Token'], 'private-token');
    if (url.endsWith('/api/uploads')) {
      creations++;
      assert.deepEqual(JSON.parse(options.body).files, [{relative_path:'原文.txt',size:10}]);
      return Response.json({id,chunk_size:4,files:[{offset:0}]});
    }
    if (options.method === 'GET') return Response.json({id,chunk_size:4,files:[{offset:received.length}]});
    if (url.endsWith('/preview')) return Response.json({id:'batch',ignored:0,files:1});
    if (received.length === 4 && fail) { fail = false; throw Error('connection lost'); }
    assert.ok(url.endsWith('offset=' + received.length));
    received += Buffer.from(options.body).toString();
    return Response.json({offset:received.length});
  };
  try {
    await assert.rejects(uploadSelection({url:'https://example.com'},'private-token','directory',[source],checkpoint), /connection lost/);
    const result = await uploadSelection({url:'https://example.com'},'private-token','directory',[source],checkpoint);
    assert.equal(result.files, 1); assert.equal(received, 'abcdefghij'); assert.equal(creations, 1);
    for (const name of fs.readdirSync(checkpoint)) assert.ok(!fs.readFileSync(path.join(checkpoint,name),'utf8').includes('private-token'));
  } finally {
    global.fetch = previousFetch;
    assert.equal(path.dirname(folder), path.resolve(os.tmpdir()));
    fs.rmSync(folder,{recursive:true,force:true});
  }
});
