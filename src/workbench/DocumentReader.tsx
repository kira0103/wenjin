import { lazy, Suspense, useEffect, useState } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowRight } from 'lucide-react';
import { api, type Material, type MaterialDetail } from './api';
import type { ReaderZoom } from './viewState';
const PdfReader = lazy(() => import('./PdfReader'));

export const positionName = (material?: Material) => material?.position_kind === 'section' ? '段' : '页';

export default function DocumentReader({ material, detail, page, onPage, zoom = 'fit', onZoom }: { material: Material; detail: MaterialDetail | null; page: number; onPage(value: number): void; zoom?: ReaderZoom; onZoom?(value: ReaderZoom): void }) {
  const [error, setError] = useState('');
  if (detail?.kind === 'working_input') return <FrozenReader detail={detail} page={page} onPage={onPage} zoom={zoom} onZoom={onZoom}/>;
  if (/^(png|jpe?g|tiff?)$/i.test(material.file_format||'')) return <ScanReader material={material} page={page}/>;
  const exportOriginal = async () => { try { setError(''); const result = await window.desktop.request({ path: `/api/materials/${material.id}/file` }); if (result.status !== 200 || !(result.body instanceof ArrayBuffer)) throw new Error('原始文件不可读取'); await window.desktop.saveFile({ filename: material.filename, data: result.body }); } catch (e) { setError((e as Error).message); } };
  if ((material.file_format || 'pdf') === 'pdf') return <Suspense fallback={<div className="page-loading">正在载入原页…</div>}><PdfReader materialId={material.id} materialName={material.filename} pageCount={material.page_count} page={page} onPageChange={onPage} zoom={zoom} onZoomChange={onZoom || (() => {})} /></Suspense>;
  return <div className="text-document-reader"><div className="reader-toolbar"><strong>{material.filename}</strong><span>文字段落</span><button className="icon-btn" aria-label="上一段" disabled={page <= 1} onClick={() => onPage(page - 1)}><ArrowLeft size={17} /></button><label className="page-counter"><input aria-label="文字段号" type="number" min={1} max={material.page_count} value={page} onChange={e => onPage(Math.max(1, Math.min(material.page_count, Number(e.target.value) || 1)))} /> / {material.page_count}</label><button className="icon-btn" aria-label="下一段" disabled={page >= material.page_count} onClick={() => onPage(page + 1)}><ArrowRight size={17} /></button><button className="icon-btn" aria-label="导出原始文件" title="导出原始 Word / TXT 文件" onClick={() => void exportOriginal()}><ArrowDownToLine size={17} /></button></div>{error && <p className="persistent-error" role="alert">{error}</p>}<p className="text-reader-notice">按正文顺序分段，段号不是 Word 排版页码。保留原始文件；图片、批注及版式请在原文件核验。</p><pre className="text-document-body">{detail?.pages.find(p => p.page_number === page)?.text || '正在读取正文…'}</pre></div>;
}

function ScanReader({material,page}:{material:Material;page:number}) {
  const [url,setUrl]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let live=true,objectUrl='';setUrl('');setError('');const [edition,asset]=material.id.split('~');void window.desktop.request({path:`/api/knowledge/editions/${edition}/files/${asset}/pages/${page}/image`}).then(result=>{if(result.status!==200||!(result.body instanceof ArrayBuffer))throw Error('扫描原件无法读取');objectUrl=URL.createObjectURL(new Blob([result.body],{type:'image/png'}));if(live)setUrl(objectUrl);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;if(objectUrl)URL.revokeObjectURL(objectUrl);};},[material.id,page]);
  return <div className="text-document-reader"><p>{material.filename} · 第 {page} 页</p>{error?<p className="persistent-error">{error}</p>:url?<img src={url} alt="扫描原件" style={{maxWidth:'100%'}}/>:<p>正在读取原页…</p>}</div>;
}

function FrozenReader({detail,page,onPage,zoom,onZoom}:{detail:MaterialDetail;page:number;onPage(value:number):void;zoom:ReaderZoom;onZoom?(value:ReaderZoom):void}) {
  const position=detail.source_positions?.[page-1];
  const [original,setOriginal]=useState<MaterialDetail|null>(null),[error,setError]=useState('');
  useEffect(()=>{let live=true;setOriginal(null);setError('');if(position?.asset_id&&detail.edition_id)void api.material(`${detail.edition_id}~${position.asset_id}`).then(value=>{if(live)setOriginal(value);}).catch(()=>{if(live)setError('原件暂时无法读取，固定录文仍可查看。');});return()=>{live=false;};},[position?.asset_id,detail.edition_id]);
  return <div className="text-document-reader"><div className="reader-toolbar"><strong>工作输入快照</strong><label>段<input aria-label="固定输入段号" type="number" min={1} max={detail.page_count} value={page} onChange={e=>onPage(Math.max(1,Math.min(detail.page_count,Number(e.target.value)||1)))}/> / {detail.page_count}</label><span>{position?.page_index?`来源第 ${position.page_index} 页 / 段`:''}</span></div>
    <p className="text-reader-notice">工作使用创建时的固定录文。{detail.source_revision_changed?'文献库已有新修订，当前工作仍使用原快照。':''}{position?.missing?'这一页没有录文，请核验原件。':''}</p>
    <pre className="text-document-body">{detail.pages.find(p=>p.page_number===page)?.text??''}</pre>{error&&<p className="scope-note">{error}</p>}
    {original&&<details><summary>核验来源原件（第 {position?.page_index} 页 / 段）</summary><DocumentReader material={original} detail={original} page={position?.page_index||1} onPage={()=>{}} zoom={zoom} onZoom={onZoom}/></details>}
  </div>;
}
