import { lazy, Suspense, useEffect, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';
import { Modal } from '../Pages';
import type { ReaderZoom } from '../viewState';
const PdfReader=lazy(()=>import('../PdfReader'));
type Block={id:string;asset_id:string;page_index:number;body:string;kind:string;section_path:string};
type Document={id:string;title:string;label:string;authors_json:string[];date_label:string;publisher:string;doi:string;
  files:{id:string;filename:string;position_kind:string;page_count:number}[];blocks:Block[]};

export default function LibraryDocumentReader({document,asset,page,onAsset,onPage,onBack,onEdit,onExcerpt,image}:{
  document:Document;asset:string;page:number;onAsset(id:string):void;onPage(n:number):void;onBack():void;onEdit():void;onExcerpt(block:Block):void;image:string;
}) {
  const [zoom,setZoom]=useState<ReaderZoom>('fit'),[pdfPages,setPdfPages]=useState(0),[textOpen,setTextOpen]=useState(false),[info,setInfo]=useState(false);
  const file=document.files.find(f=>f.id===asset),pdf=!!file?.filename.toLowerCase().endsWith('.pdf');
  const blocks=document.blocks.filter(b=>b.asset_id===asset&&b.page_index===page);
  const count=pdf?(pdfPages||file?.page_count||1):Math.max(1,file?.page_count||0,...document.blocks.filter(b=>b.asset_id===asset).map(b=>b.page_index));
  useEffect(()=>{setPdfPages(0);setTextOpen(false);},[asset,document.id]);
  return <section className="kb-document-reader" aria-label="文献阅读">
    <header className="kb-document-heading"><button className="button-quiet" onClick={onBack}><ArrowLeft size={16}/>返回目录</button><div className="kb-document-title"><h2>{file?.filename||document.title}</h2></div><div className="kb-document-actions"><button className="button-quiet" onClick={()=>setInfo(true)}>文献信息</button>{pdf&&<button className="button-secondary" aria-pressed={textOpen} onClick={()=>setTextOpen(v=>!v)}>提取文字</button>}</div></header>
    {document.files.length>1&&<div className="kb-document-file-select"><label>文件<select aria-label="选择分册" value={asset} onChange={e=>{onAsset(e.target.value);onPage(1);}}>{document.files.map(f=><option key={f.id} value={f.id}>{f.filename}</option>)}</select></label></div>}
    <div className={'kb-document-body'+(pdf&&textOpen?' has-text':'')}>
      <div className="kb-document-original">{pdf?<Suspense fallback={<p role="status">正在打开 PDF…</p>}><PdfReader key={asset} materialId={document.id} filePath={`/api/knowledge/editions/${document.id}/files/${asset}`} materialName={file?.filename||'文献.pdf'} pageCount={count} page={page} onPageChange={onPage} onDocumentLoad={setPdfPages} zoom={zoom} onZoomChange={setZoom}/></Suspense>:<>
        <div className="kb-text-pagination"><button className="icon-btn" aria-label="上一段" disabled={page<=1} onClick={()=>onPage(page-1)}><ChevronLeft size={18}/></button><label>{file?.position_kind==='section'?'段':'页'}<input aria-label="文献页段" type="number" min={1} max={count} value={page} onChange={e=>onPage(Math.max(1,Math.min(count,Number(e.target.value)||1)))}/> / {count}</label><button className="icon-btn" aria-label="下一段" disabled={page>=count} onClick={()=>onPage(page+1)}><ChevronRight size={18}/></button></div>
        {image?<img className="kb-document-scan" src={image} alt="原始扫描页"/>:<div className="kb-document-text">{blocks.length?blocks.map(b=><article key={b.id}><pre>{b.body}</pre><button className="button-quiet" onClick={()=>onExcerpt(b)}>保存摘录</button></article>):<p className="empty-block">此页暂无可显示的文字</p>}</div>}
      </>}</div>
      {pdf&&textOpen&&<aside className="kb-document-transcript"><div className="kb-transcript-heading"><h3>第 {page} 页提取文字</h3><button className="button-quiet" onClick={()=>setTextOpen(false)}>收起</button></div>{blocks.length?blocks.map(b=><article key={b.id}><pre>{b.body}</pre><button className="button-quiet" onClick={()=>onExcerpt(b)}>保存摘录</button></article>):<p className="scope-note">此页暂无提取文字，可继续阅读 PDF 原件。</p>}</aside>}
    </div>
    {info&&<Modal title="文献信息" onClose={()=>setInfo(false)}><dl className="kb-document-info"><dt>题名</dt><dd>{document.title}</dd><dt>作者</dt><dd>{document.authors_json.join('、')||'未填写'}</dd><dt>版本 / 卷期</dt><dd>{document.label||'未填写'}</dd><dt>年代</dt><dd>{document.date_label||'未填写'}</dd><dt>出版者</dt><dd>{document.publisher||'未填写'}</dd>{document.doi&&<><dt>DOI</dt><dd>{document.doi}</dd></>}</dl><div className="modal-actions"><button className="button-secondary" onClick={()=>setInfo(false)}>关闭</button><button className="button-primary" onClick={()=>{setInfo(false);onEdit();}}>编辑文献信息</button></div></Modal>}
  </section>;
}
