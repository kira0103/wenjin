import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { call } from './transport';

export default function ServiceConnection({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [started, setStarted] = useState(false);
  const [checking, setChecking] = useState(true);
  const [needCredentials, setNeedCredentials] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [remoteDesktop, setRemoteDesktop] = useState(false), [url, setUrl] = useState('');
  const [token, setToken] = useState(''), [error, setError] = useState('');
  const inspect = useCallback(async () => {
    if (window.desktop) {
      const info = await window.desktop.connectionInfo?.() || { remote: false, url: '', connected: true };
      if (!info.remote) setRemoteDesktop(false);
      else setRemoteDesktop(!!info.remote);
      setUrl(info.url);
      return { authenticated: info.connected, remote: !!info.remote };
    }
    const value = await call<{ authenticated: boolean }>({ path: '/api/session', silent: true });
    return { authenticated: value.authenticated, remote: false };
  }, []);
  useEffect(() => {
    let live = true;
    setChecking(true);
    void inspect().then(value => {
      if (!live) return;
      setConnected(value.authenticated);
      setStarted(value.authenticated);
      if (!value.authenticated) setNeedCredentials(value.remote);
    }).catch(() => {
      if (live) { setNeedCredentials(false); setError('无法连接资料服务，请检查服务是否启动'); }
    }).finally(() => { if (live) setChecking(false); });
    const expired = () => { setConnected(false); setNeedCredentials(true); setError('登录已过期，请重新输入连接凭据；当前页面中的修改已保留'); };
    window.addEventListener('wenjin:unauthorized', expired);
    return () => { live = false; window.removeEventListener('wenjin:unauthorized', expired); };
  }, [inspect, attempt]);
  async function connect() {
    setChecking(true); setError('');
    try {
      if (remoteDesktop && window.desktop.connectService) await window.desktop.connectService({ url, token });
      else await call({ path: '/api/session', method: 'POST', body: { token }, silent: true });
      setToken(''); setNeedCredentials(false); setConnected(true); setStarted(true);
      window.dispatchEvent(new Event('wenjin:connected'));
    } catch (e) { setError(e instanceof Error ? e.message : '连接未完成'); }
    finally { setChecking(false); }
  }
  const retry = () => { setError(''); setAttempt(value => value + 1); };
  return <>{started && children}{!connected && <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="连接资料服务"><form className="modal-panel" onSubmit={event => { event.preventDefault(); if (needCredentials) void connect(); else retry(); }}><h2>连接资料服务</h2>{checking && !error ? <p role="status">正在连接资料服务…</p> : needCredentials ? <><p>输入资料服务连接凭据，问问你的文献、项目与后台任务。</p>{remoteDesktop && <label className="form-label">服务地址<input type="url" placeholder="https://192.168.1.10:8765" value={url} onChange={event => setUrl(event.target.value)} disabled={checking}/></label>}<label className="form-label">连接凭据<input autoFocus type="password" autoComplete="current-password" value={token} onChange={event => setToken(event.target.value)} disabled={checking}/></label>{error && <p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions"><button type="button" className="button-secondary" disabled={checking} onClick={retry}>重试连接</button><button className="button-primary" type="submit" disabled={checking || !token || remoteDesktop && !url}>{checking ? '正在连接…' : '连接'}</button></div></> : <><p role="status">无法连接资料服务，请检查服务是否启动</p>{error && <p role="alert" className="persistent-error">{error}</p>}<div className="modal-actions">{remoteDesktop && <button type="button" className="button-secondary" onClick={() => { setError(''); setNeedCredentials(true); }}>输入连接凭据</button>}<button className="button-primary" type="submit" disabled={checking}>{checking ? '正在重试…' : '重试连接'}</button></div></>}</form></div>}</>;
}
