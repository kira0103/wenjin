import { useEffect, useState, type ReactNode } from 'react';
import { call } from './transport';

export default function ServiceConnection({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [started, setStarted] = useState(false);
  const [checking, setChecking] = useState(true);
  const [remoteDesktop, setRemoteDesktop] = useState(false), [url, setUrl] = useState('');
  const [token, setToken] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const inspect = async () => {
      if (window.desktop) {
        const info = await window.desktop.connectionInfo?.() || {remote:false,url:'',connected:true};
        if (live) { setRemoteDesktop(info.remote); setUrl(info.url); }
        return { authenticated: info.connected };
      }
      return call<{ authenticated: boolean }>({ path: '/api/session' });
    };
    void inspect().then(value => {
      if (live) { setConnected(value.authenticated); setStarted(value.authenticated); }
    }).catch(() => { if (live) setError('资料服务暂时无法连接，请检查服务是否启动'); }).finally(() => { if (live) setChecking(false); });
    const expired = () => { setConnected(false); setError('连接已过期，请重新输入凭据。当前页面中的修改已保留。'); };
    window.addEventListener('wenjin:unauthorized', expired);
    return () => { live = false; window.removeEventListener('wenjin:unauthorized', expired); };
  }, []);
  async function connect() {
    setChecking(true); setError('');
    try {
      if (remoteDesktop && window.desktop.connectService) await window.desktop.connectService({url,token});
      else await call({ path: '/api/session', method: 'POST', body: { token } });
      setToken(''); setConnected(true); setStarted(true);
      window.dispatchEvent(new Event('wenjin:connected'));
    } catch (e) { setError(e instanceof Error ? e.message : '连接未完成'); }
    finally { setChecking(false); }
  }
  return <>{started && children}{!connected && <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="连接资料服务"><form className="modal-panel" onSubmit={event => { event.preventDefault(); void connect(); }}><h2>连接问津资料服务</h2><p>输入服务连接凭据，访问你的文献、项目和后台任务。</p>{remoteDesktop && <label className="form-label">服务地址<input type="url" placeholder="https://wenjin.example.com" value={url} onChange={event => setUrl(event.target.value)} disabled={checking}/></label>}<label className="form-label">连接凭据<input autoFocus type="password" autoComplete="current-password" value={token} onChange={event => setToken(event.target.value)} disabled={checking}/></label>{error && <p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions"><button className="button-primary" type="submit" disabled={checking || !token || remoteDesktop && !url}>{checking ? '正在连接…' : '连接'}</button></div></form></div>}</>;
}
