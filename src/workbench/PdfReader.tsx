import { send } from './transport';
import { platform } from './platform';
import { useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { ChevronLeft, ChevronRight, Download, LoaderCircle, ZoomIn, ZoomOut } from 'lucide-react';
import { clampPage, type ReaderZoom } from './viewState';
pdfjs.GlobalWorkerOptions.workerSrc=workerUrl;
type Props={filePath?:string;materialId:string;materialName:string;pageCount:number;page:number;onPageChange(n:number):void;onDocumentLoad?(n:number):void;zoom:ReaderZoom;onZoomChange(v:ReaderZoom):void};
type Size={width:number;height:number};

function PageCanvas({pdf,number,scale,onSize}:{pdf:pdfjs.PDFDocumentProxy;number:number;scale:number;onSize(n:number,s:Size):void}){
  const canvas=useRef<HTMLCanvasElement>(null),[ready,setReady]=useState(false),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  const sizeCallback=useRef(onSize);sizeCallback.current=onSize;
  useEffect(()=>{let cancelled=false;let render:pdfjs.RenderTask|undefined;const element=canvas.current;setReady(false);setError('');
    void(async()=>{try{const page=await pdf.getPage(number);if(cancelled||!element)return;const natural=page.getViewport({scale:1});sizeCallback.current(number,{width:natural.width,height:natural.height});
      const view=page.getViewport({scale}),ratio=Math.min(devicePixelRatio||1,2);element.width=Math.round(view.width*ratio);element.height=Math.round(view.height*ratio);element.style.width=view.width+'px';element.style.height=view.height+'px';
      const context=element.getContext('2d');if(!context)throw Error('页面显示不可用');render=page.render({canvasContext:context,viewport:view,transform:[ratio,0,0,ratio,0,0]});await render.promise;if(!cancelled)setReady(true);
    }catch(e){if(!cancelled)setError(e instanceof Error?e.message:'此页无法显示');}})();
    return()=>{cancelled=true;render?.cancel();if(element){element.width=0;element.height=0;}};
  },[pdf,number,scale,attempt]);
  return <>{!ready&&!error&&<span className="pdf-page-loading">正在显示第 {number} 页…</span>}{error&&<div className="pdf-page-error" role="alert">{error}<button className="button-secondary" onClick={()=>setAttempt(v=>v+1)}>重试此页</button></div>}<canvas ref={canvas} className="pdf-canvas" data-page={number} data-rendered={ready} aria-label={'PDF 第 '+number+' 页'}/></>;
}

export default function PdfReader({filePath,materialId,materialName,pageCount,page,onPageChange,onDocumentLoad,zoom,onZoomChange}:Props){
  const stage=useRef<HTMLDivElement>(null),shells=useRef<(HTMLDivElement|null)[]>([]),frame=useRef(0);
  const callbacks=useRef({onPageChange,onDocumentLoad});callbacks.current={onPageChange,onDocumentLoad};
  const wanted=useRef(page),reported=useRef(page);wanted.current=page;
  const [pdf,setPdf]=useState<pdfjs.PDFDocumentProxy|null>(null),[base,setBase]=useState<Size>({width:595,height:842}),[sizes,setSizes]=useState<Record<number,Size>>({});
  const [width,setWidth]=useState(0),[visible,setVisible]=useState<Set<number>>(new Set([page])),[current,setCurrent]=useState(page),[pageInput,setPageInput]=useState(String(page));
  const [error,setError]=useState(''),[attempt,setAttempt]=useState(0),[saving,setSaving]=useState(false),[saveMessage,setSaveMessage]=useState('');
  const pages=pdf?.numPages||Math.max(1,pageCount),scale=zoom==='fit'?Math.max(.1,Math.max(100,width-36)/base.width):zoom;
  const pageNumbers=useMemo(()=>Array.from({length:pdf?.numPages||0},(_,i)=>i+1),[pdf]);
  useEffect(()=>{if(!stage.current)return;const observer=new ResizeObserver(e=>setWidth(e[0].contentRect.width));observer.observe(stage.current);return()=>observer.disconnect();},[]);
  useEffect(()=>{let cancelled=false;let loading:pdfjs.PDFDocumentLoadingTask|undefined;setPdf(null);setSizes({});setError('');setSaveMessage('');reported.current=wanted.current;setCurrent(wanted.current);setVisible(new Set([wanted.current]));
    void(async()=>{try{const response=await send({path:filePath||`/api/materials/${materialId}/file`});if(cancelled)return;if(response.status!==200||!(response.body instanceof ArrayBuffer))throw Error('原始 PDF 无法打开，请检查资料文件');
      loading=pdfjs.getDocument({data:new Uint8Array(response.body),cMapUrl:new URL('./pdf-assets/cmaps/',location.href).href,cMapPacked:true,standardFontDataUrl:new URL('./pdf-assets/standard_fonts/',location.href).href,useWorkerFetch:false});
      const doc=await loading.promise;if(cancelled)return;const first=await doc.getPage(1);if(cancelled)return;const view=first.getViewport({scale:1});setBase({width:view.width,height:view.height});setPdf(doc);callbacks.current.onDocumentLoad?.(doc.numPages);
    }catch(e){if(!cancelled)setError(e instanceof Error?e.message:'PDF 读取失败');}})();
    return()=>{cancelled=true;void loading?.destroy();cancelAnimationFrame(frame.current);};
  },[filePath,materialId,attempt]);
  function scrollTo(n:number,notify=true){const next=clampPage(n,pages);reported.current=next;setCurrent(next);setPageInput(String(next));const node=shells.current[next-1],root=stage.current;if(node&&root)root.scrollTop+=node.getBoundingClientRect().top-root.getBoundingClientRect().top-16;if(notify)callbacks.current.onPageChange(next);}
  useEffect(()=>{if(!pdf)return;const initial=clampPage(wanted.current,pdf.numPages);const id=requestAnimationFrame(()=>scrollTo(initial,false));if(initial!==wanted.current)callbacks.current.onPageChange(initial);return()=>cancelAnimationFrame(id);},[pdf]);
  useEffect(()=>{if(!pdf||page===reported.current)return;scrollTo(page,false);},[page,pdf]);
  useEffect(()=>{if(!pdf)return;const id=requestAnimationFrame(()=>scrollTo(reported.current,false));return()=>cancelAnimationFrame(id);},[scale]);
  useEffect(()=>{if(!pdf||!stage.current)return;const observer=new IntersectionObserver(entries=>{setVisible(previous=>{const next=new Set(previous);for(const e of entries){const n=Number((e.target as HTMLElement).dataset.pdfPage);if(e.isIntersecting)next.add(n);else next.delete(n);}return next;});},{root:stage.current,rootMargin:'1000px 0px'});for(const node of shells.current)if(node)observer.observe(node);return()=>observer.disconnect();},[pdf]);
  function reportScroll(){cancelAnimationFrame(frame.current);frame.current=requestAnimationFrame(()=>{if(!stage.current)return;const root=stage.current.getBoundingClientRect(),anchor=root.top+Math.min(120,root.height*.25);let best=1,low=0,high=pageNumbers.length-1;
    // Visibility callbacks can lag a large jump. Locate the current placeholder
    // directly so page indicators never report an old, still-rendered neighbor.
    while(low<=high){const middle=(low+high)>>1,box=shells.current[middle]?.getBoundingClientRect();if(box&&box.top<=anchor){best=middle+1;low=middle+1;}else high=middle-1;}
    if(best!==reported.current){reported.current=best;setCurrent(best);setPageInput(String(best));callbacks.current.onPageChange(best);}
  });}
  function size(n:number,value:Size){setSizes(v=>v[n]?.width===value.width&&v[n]?.height===value.height?v:{...v,[n]:value});}
  async function saveCopy(){setSaving(true);setSaveMessage('');try{const result=await send({path:filePath||`/api/materials/${materialId}/file`});if(result.status!==200)throw Error('无法读取原始文件');if(await platform.saveFile({filename:materialName,data:result.body}))setSaveMessage('PDF 副本已保存');}catch(e){setSaveMessage(e instanceof Error?e.message:'保存失败');}finally{setSaving(false);}}
  return <section className="reader-panel continuous-reader" aria-label="PDF 连续阅读"><header className="reader-toolbar"><div className="reader-file"><span title={materialName}>{materialName}</span></div><div className="reader-controls">
    <button className="icon-btn" aria-label="上一页" disabled={!pdf||current<=1} onClick={()=>scrollTo(current-1)}><ChevronLeft size={18}/></button><label className="page-counter"><input aria-label="跳转到页码" type="number" min={1} max={pages} value={pageInput} onChange={e=>setPageInput(e.target.value)} onBlur={()=>scrollTo(Number(pageInput))} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}/><span>/ {pdf?pages:'…'}</span></label><button className="icon-btn" aria-label="下一页" disabled={!pdf||current>=pages} onClick={()=>scrollTo(current+1)}><ChevronRight size={18}/></button><span className="toolbar-divider"/>
    <button className="icon-btn" aria-label="缩小" disabled={scale<=.25} onClick={()=>onZoomChange(Math.max(.25,scale-.15))}><ZoomOut size={17}/></button><span className="zoom-label">{Math.round(scale*100)}%</span><button className="icon-btn" aria-label="放大" disabled={scale>=3} onClick={()=>onZoomChange(Math.min(3,scale+.15))}><ZoomIn size={17}/></button><button className="reader-fit button-quiet" aria-pressed={zoom==='fit'} onClick={()=>onZoomChange('fit')}>适合宽度</button><button className="icon-btn" aria-label="保存 PDF 副本" disabled={saving} onClick={()=>void saveCopy()}><Download size={17}/></button>
  </div></header><div ref={stage} className="reader-stage" tabIndex={0} onScroll={reportScroll}>
    {!pdf&&!error&&<div className="reader-loading" role="status"><LoaderCircle className="spin" size={20}/>正在打开 PDF</div>}{error&&<div className="reader-error-box" role="alert"><p>{error}</p><button className="button-secondary" onClick={()=>setAttempt(v=>v+1)}>重新打开 PDF</button></div>}
    <div className="pdf-pages">{pdf&&pageNumbers.map(n=>{const dimensions=sizes[n]||base;return <div ref={node=>{shells.current[n-1]=node;}} className="pdf-page-shell" data-pdf-page={n} key={n} style={{width:dimensions.width*scale,height:dimensions.height*scale+28}}><div className="pdf-page-paper" style={{height:dimensions.height*scale}}>{visible.has(n)&&<PageCanvas pdf={pdf} number={n} scale={scale} onSize={size}/>}</div><small className="pdf-page-number">{n}</small></div>;})}</div>
  </div>{saveMessage&&<p className="reader-save-message" role="status">{saveMessage}</p>}</section>;
}
