import { useEffect, useRef, useState } from 'react';
import { request } from '../api';

type CatalogEdition = {
  version: number; work_version: number; id: string; work_id: string; title: string;
  item_type: string; authors_json: string[]; date_label: string; label: string;
  publisher: string; publication_year?: number | null; doi: string; source_note: string;
  tags: string[]; active_revision: string | null; file_count?: number; index_state?: string;
};

export default function UnclassifiedCatalog({ onOpen, onEdit }: {
  onOpen(id: string): void;
  onEdit(edition: CatalogEdition): void;
}) {
  const [query, setQuery] = useState(''), [year, setYear] = useState(''), [state, setState] = useState('all');
  const [items, setItems] = useState<CatalogEdition[]>([]), [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(true);
  const generation = useRef(0);

  async function load(next: string | null = null) {
    const ticket = ++generation.current;
    setBusy(true); setError('');
    try {
      const params = new URLSearchParams({ kind: 'unclassified', query, year, state, limit: '50', ...(next ? { cursor: next } : {}) });
      const value = await request<{ items: CatalogEdition[]; next_cursor: string | null }>('/api/knowledge/catalog?' + params);
      if (ticket === generation.current) {
        setItems(previous => next ? [...previous, ...value.items] : value.items);
        setCursor(value.next_cursor);
      }
    } catch (e) {
      if (ticket === generation.current) setError(e instanceof Error ? e.message : '无法读取待归类文献');
    } finally {
      if (ticket === generation.current) setBusy(false);
    }
  }

  useEffect(() => {
    setBusy(true); setItems([]); setCursor(null);
    const timer = setTimeout(() => void load(), 250);
    return () => { clearTimeout(timer); generation.current++; };
  }, [query, year, state]);

  return <section>
    <div className="kb-toolbar">
      <input aria-label="论文书目搜索" placeholder="搜索题名、作者或版本" value={query} onChange={e => setQuery(e.target.value)} />
      <input aria-label="论文年份筛选" placeholder="年份" value={year} onChange={e => setYear(e.target.value)} />
      <select aria-label="论文处理状态" value={state} onChange={e => setState(e.target.value)}>
        <option value="all">所有状态</option><option value="metadata">仅有书目</option>
        <option value="text">正文可检索</option><option value="ready">语义索引就绪</option>
      </select>
    </div>
    {error && <p className="persistent-error" role="alert">{error}</p>}
    <div className="kb-paper-table"><table>
      <thead><tr><th>题名</th><th>作者</th><th>年份</th><th>刊物／出版者</th><th>资料状态</th><th>操作</th></tr></thead>
      <tbody>{items.map(edition => <tr key={edition.id}>
        <td><strong>{edition.title}</strong><small>{edition.label}</small></td>
        <td>{edition.authors_json.join('、') || '待补'}</td>
        <td>{edition.publication_year || edition.date_label || '待补'}</td>
        <td>{edition.publisher || '待补'}</td>
        <td><small>原件：{edition.file_count ? '已有' : '待补'}</small>
          <small>正文：{edition.active_revision ? '可检索' : '待提取'}</small>
          <small>语义索引：{edition.index_state === 'ready' ? '就绪' : '未就绪'}</small></td>
        <td><button className="button-quiet" onClick={() => onOpen(edition.id)}>阅读</button>
          <button className="button-quiet" onClick={() => onEdit(edition)}>核对分类</button></td>
      </tr>)}</tbody>
    </table></div>
    {!busy && !items.length && <p className="empty-block">没有待归类的历史文献。</p>}
    {busy && <p role="status">正在读取书目…</p>}
    {cursor && <button className="button-secondary" disabled={busy} onClick={() => void load(cursor)}>加载更多论文</button>}
  </section>;
}
