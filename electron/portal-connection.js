document.querySelector('#url').value = new URLSearchParams(location.search).get('url') || 'http://127.0.0.1:8765';
document.querySelector('#connection').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.querySelector('#connect');
  button.disabled = true; button.textContent = '正在连接…';
  try { const result = await window.portalConnection.connect(document.querySelector('#url').value); document.querySelector('#error').textContent = result.error || ''; }
  catch { document.querySelector('#error').textContent = '连接未完成，请重试。'; }
  finally { button.disabled = false; button.textContent = '连接并登录'; }
});
