import { useEffect, useState } from 'react';
import { Database, LoaderCircle } from 'lucide-react';
import { request } from '../api';

type Settings = { mode?: string; enabled: boolean; host: string; port: number; database: string; username: string;
  has_password: boolean; environment_override: boolean; credential_available: boolean };
type Health = { status: string; connected: boolean; ready: boolean; message: string;
  server_version?: string; schema_revision?: string | null; extensions: Record<string, string> };

export default function KnowledgeSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [password, setPassword] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void request<Settings>('/api/knowledge/settings').then(value => {
      if (active) { setSettings(value); setError(''); }
    }).catch(e => { if (active) setError(String(e.message)); });
    return () => { active = false; };
  }, [revision]);
  const update = (value: Partial<Settings>) => {
    if (settings) setSettings({ ...settings, ...value });
    setDirty(true); setHealth(null); setMessage(''); setError('');
  };
  async function save() {
    if (!settings) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const { enabled, host, port, database, username } = settings;
      const saved = await request<Settings>('/api/knowledge/settings', 'PUT',
        { enabled, host, port, database, username, password: password || null });
      setSettings(saved); setPassword(''); setDirty(false); setHealth(null);
      setMessage('连接设置已保存，可以测试连接。');
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setError(''); setMessage('');
    try { setHealth(await request<Health>('/api/knowledge/connection-test', 'POST')); }
    catch (e) { setError(e instanceof Error ? e.message : '连接测试失败'); }
    finally { setBusy(false); }
  }
  return <div className="page-container knowledge-settings"><div className="settings-card">
    <div className="settings-card-heading"><Database size={19} /><div><strong>文献库数据库</strong>
      <span>连接 PostgreSQL，保存文献与研究知识。</span></div></div>
    {!settings ? <><p className="scope-note">{error || '正在读取连接设置…'}</p>
      {error && <button className="button-secondary" onClick={() => setRevision(r => r + 1)}>重新读取</button>}</> :
      <form className="settings-form" onSubmit={e => { e.preventDefault(); void save(); }}>
        {settings.environment_override && <p className="scope-note">{settings.mode==='internal'?'内置数据库随软件启动和停止，无需填写连接信息。':'连接由环境变量配置，此处可测试状态。'}</p>}
        {!settings.credential_available && <p className="persistent-error">系统凭据库暂不可用，请恢复凭据库后保存密码。</p>}
        <fieldset disabled={busy || settings.environment_override} style={{ border: 0, padding: 0, margin: 0 }}>
          <label className="check-label"><input type="checkbox" checked={settings.enabled}
            onChange={e => update({ enabled: e.target.checked })} />启用 PostgreSQL 文献库</label>
          <div className="form-grid">
            <label>主机<input value={settings.host} required maxLength={253} spellCheck={false}
              onChange={e => update({ host: e.target.value })} /></label>
            <label>端口<input type="number" value={settings.port} required min={1} max={65535}
              onChange={e => update({ port: Number(e.target.value) })} /></label>
            <label>数据库名称<input value={settings.database} required maxLength={63}
              onChange={e => update({ database: e.target.value })} /></label>
            <label>运行账号<input value={settings.username} required maxLength={63}
              onChange={e => update({ username: e.target.value })} /></label>
          </div>
          <label>数据库密码<input type="password" value={password} maxLength={1024} autoComplete="new-password"
            placeholder={settings.has_password ? '已保存；同一连接留空保持不变' : '输入运行账号密码'}
            onChange={e => { setPassword(e.target.value); setDirty(true); setHealth(null); setMessage(''); }} /></label>
        </fieldset>
        <p className="scope-note">{settings.mode==='internal'?'内置数据库的凭据由软件保存在受限的本地文件中。文献库升级前自动备份，工作稿使用固定输入快照。':'外部数据库密码保存在系统凭据库，结构升级使用部署命令。请在文献库的升级检查中迁移旧资料。'}</p>
        <div className="settings-actions"><button type="button" className="button-secondary" disabled={busy || dirty}
          onClick={() => void test()}>{busy && <LoaderCircle size={16} className="spin" />}测试连接</button>
          <button className="button-primary" disabled={busy || settings.environment_override}>{busy ? '处理中…' : '保存连接设置'}</button></div>
        {dirty && <p className="scope-note">先保存修改，再测试连接。</p>}
        {message && <p className="connection-success" role="status">{message}</p>}
        {error && <p className="persistent-error" role="alert">{error}</p>}
        {health && <div className={health.ready ? 'connection-success' : 'scope-note'} role="status">
          <p>{health.message}</p>{health.connected && <p>PostgreSQL {health.server_version} · 数据结构 {health.schema_revision || '未初始化'} ·
            pgvector {health.extensions.vector || '未安装'}</p>}</div>}
      </form>}
  </div></div>;
}
