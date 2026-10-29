const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const supported = /\.(pdf|docx?|txt|md|png|jpe?g|tiff?)$/i;
async function uploadSelection(config, token, mode, paths, checkpointDir) {
  const files = [];
  let ignored = 0;
  async function collect(folder, prefix = '') {
    for (const entry of await fs.promises.readdir(folder, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) { ignored++; continue; }
      const absolute = path.join(folder, entry.name), relative = prefix + entry.name;
      if (entry.isSymbolicLink()) { ignored++; continue; }
      if (entry.isDirectory()) await collect(absolute, relative + '/');
      else if (entry.isFile()) await add(absolute, relative);
    }
  }
  async function add(absolute, relative) {
    if (!supported.test(relative)) { ignored++; return; }
    const stat = await fs.promises.lstat(absolute);
    if (stat.isSymbolicLink() || !stat.isFile()) throw Error('请选择实际文献文件');
    if (!stat.size || stat.size > 250 * 1024**2) throw Error('文件为空或超过 250 MB：' + relative);
    files.push({ absolute, relative_path: relative, size: stat.size, mtime: stat.mtimeMs });
    if (files.length > 50000) throw Error('每批最多 50,000 个文件');
  }
  if (mode === 'directory') await collect(paths[0]);
  else for (const absolute of paths) await add(absolute, path.basename(absolute));
  if (!files.length) throw Error('没有找到支持的文献文件');
  async function request(endpoint, method = 'GET', body, binary = false) {
    const headers = { 'X-App-Token': token };
    if (body !== undefined) headers['Content-Type'] = binary ? 'application/octet-stream' : 'application/json';
    const response = await fetch(config.url + endpoint, { method, headers, body: body === undefined ? undefined : binary ? body : JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) { const error = Error(typeof result.detail === 'string' ? result.detail : '文献上传失败'); error.status = response.status; throw error; }
    return result;
  }
  const fingerprint = crypto.createHash('sha256').update(JSON.stringify([config.url, mode, files])).digest('hex');
  const checkpoint = checkpointDir && path.join(checkpointDir, fingerprint + '.json');
  let state;
  if (checkpoint && fs.existsSync(checkpoint)) {
    const previous = JSON.parse(await fs.promises.readFile(checkpoint, 'utf8'));
    if (!/^[a-f0-9]{32}$/.test(previous.id)) throw Error('上传记录无效，请清理客户端上传记录后重试');
    try { state = await request('/api/uploads/' + previous.id); } catch (error) { if (error.status !== 404) throw error; }
  }
  if (!state) {
    state = await request('/api/uploads', 'POST', { mode, label: mode === 'directory' ? path.basename(paths[0]) : '所选文件', files: files.map(({ relative_path, size }) => ({ relative_path, size })) });
    if (checkpoint) {
      await fs.promises.mkdir(checkpointDir, { recursive: true });
      await fs.promises.writeFile(checkpoint + '.tmp', JSON.stringify({ id: state.id }));
      await fs.promises.rename(checkpoint + '.tmp', checkpoint);
    }
  }
  for (let index = 0; index < files.length; index++) {
    const file = files[index];
    const handle = await fs.promises.open(file.absolute, 'r');
    try {
      const stat = await handle.stat();
      if (stat.size !== file.size || stat.mtimeMs !== file.mtime) throw Error('文件在上传前发生变化，请重新选择');
      let offset = state.files[index].offset;
      const buffer = Buffer.alloc(state.chunk_size);
      while (offset < file.size) {
        const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, file.size - offset), offset);
        if (!bytesRead) throw Error('读取文献失败');
        const result = await request(`/api/uploads/${state.id}/files/${index}?offset=${offset}`, 'PUT', buffer.subarray(0, bytesRead), true);
        offset = result.offset;
      }
      const after = await handle.stat();
      if (after.size !== stat.size || after.mtimeMs !== stat.mtimeMs) throw Error('文件在上传期间发生变化，请重新选择');
    } finally { await handle.close(); }
  }
  const preview = await request(`/api/uploads/${state.id}/preview`, 'POST');
  return { ...preview, ignored: preview.ignored + ignored };
}

module.exports = { uploadSelection };
