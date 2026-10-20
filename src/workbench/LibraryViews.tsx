import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { api, type Material, type MaterialDetail, type MaterialRole } from './api';
import DocumentReader from './DocumentReader';

export const roleNames: Record<MaterialRole, string> = { base: '底本 / 原文', witness: '参校本', citation: '引文出处', reference: '研究参考', auxiliary: '辅助资料' };
export const projectNames = { general: '通用研究', collation: '古籍整理与点校', literature: '文献研究', translation: '翻译' };

export function GlobalDocument({ material, onBack, onAdd }: { material: Material; onBack(): void; onAdd(): void }) {
  const [detail, setDetail] = useState<MaterialDetail | null>(null), [error, setError] = useState(''), [page, setPage] = useState(1);
  useEffect(() => { let cancelled = false; void api.material(material.id).then(value => { if (!cancelled) setDetail(value); }).catch(e => { if (!cancelled) setError(e.message); }); return () => { cancelled = true; }; }, [material.id]);
  return <div className="global-document"><div className="workspace-header"><div><button className="button-quiet" onClick={onBack}><ArrowLeft size={16} />返回文献库</button><h1>{material.title}</h1></div><button className="button-secondary" onClick={onAdd}>前往项目材料</button></div>{error ? <p className="persistent-error">{error}</p> : <DocumentReader material={material} detail={detail} page={page} onPage={setPage} />}</div>;
}
