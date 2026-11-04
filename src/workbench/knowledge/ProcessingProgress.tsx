import './processing-monitor.css';

type Detail={completed?:number;total?:number;unit?:'files'|'blocks'|'chunks'|'segments';filename?:string;files?:{filename:string;completed:number;total:number}[]};
export type ProcessingJob={id:string;kind:string;state:string;stage:string;progress:number;error:string;detail?:Detail|null;chunks?:number;queue_position?:number;updated_at?:string;lease_valid?:boolean|null;document_progress?:number};
type Totals={total:number;succeeded:number;failed:number;cancelled:number;queued:number;running:number;finished?:number;percent:number;skipped?:number;pending_dispatch?:number;dispatch_failed?:number};
type Batch=Totals&{batch_id:string;submitted_at:string;updated_at:string;models:(string|null)[]};
export type ProcessingState={sampled_at?:string;dispatches?:{id:string;state:string;progress:number;error:string;result:{cursor:number;scheduled:number;skipped:number;total:number}}[];overall?:Totals;batch:Batch|null;batches:Batch[];queue:{queued:number;running:number};ingestion_queue?:{queued:number;running:number};library:{total:number;ready:number};active_jobs:(ProcessingJob&{title:string|null})[]};
const stages:Record<string,string>={queued:'等待执行',starting:'准备录文与模型',reading_source:'读取原文',parse:'解析原件',publish:'保存录文修订',chunking:'清洗与切分',semantic_chunking:'计算语义切分边界',embedding:'计算文献向量',writing_index:'写入向量索引',complete:'加工完成',download_embedding:'下载与载入模型',verify_embedding:'校验向量模型'};
const kinds:Record<string,string>={index:'文献向量化',import:'解析文献',embedding_model:'准备向量模型',rerank_model:'准备重排模型',document_batch:'导入文献批次',export:'资料备份'};
function phase(job:ProcessingJob){return job.state==='queued'?'排队中':job.state==='succeeded'?'加工完成':job.state==='failed'?'加工失败':job.state==='cancelled'?'已取消':job.lease_valid===false?'等待执行器恢复':stages[job.stage]||'后台处理中';}
function counter(detail?:Detail|null){if(detail?.completed===undefined)return '';const unit={files:'个原件',blocks:'个原文块',chunks:'个分块',segments:'段语义样本'}[detail.unit||'chunks'];return detail.total===undefined?`已处理 ${detail.completed} ${unit}`:`${detail.completed} / ${detail.total} ${unit}`;}
function percent(job:ProcessingJob){return job.state==='succeeded'?100:Math.max(0,Math.min(100,job.document_progress??job.progress));}
function time(value?:string){return value?new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):'';}

export function DocumentProcessingProgress({job,stageOnly=false}:{job:ProcessingJob;stageOnly?:boolean}){
  const preparing=job.state==='running'&&job.stage==='starting',active=['queued','running'].includes(job.state);
  const detail=job.detail;
  const stagePercent=detail?.total&&detail.completed!==undefined?Math.max(0,Math.min(100,Math.round(detail.completed/detail.total*1000)/10)):null;
  const indeterminate=preparing||stageOnly&&active&&stagePercent===null;
  const shownPercent=stageOnly?stagePercent??percent(job):percent(job);
  return <div className="rag-document-job" aria-label="文献加工进度"><div className="rag-job-heading"><strong>{phase(job)}</strong><span>{job.state==='queued'?(job.queue_position?`队列第 ${job.queue_position} 位`:'等待前面的任务完成'):indeterminate?preparing?'准备中':'执行中':`${shownPercent}%`}</span></div>
    <progress aria-label={stageOnly?'当前阶段进度':'当前文献进度'} max={100} value={indeterminate?undefined:shownPercent}/>
    <div className="rag-job-detail"><span>{counter(job.detail)||(!active&&job.chunks!==undefined?`已建立 ${job.chunks} 个向量分块`:job.state==='queued'?'后台会按队列依次执行。':job.state==='failed'?'本次未完成，可按原配置重试。':job.state==='cancelled'?'本次处理已取消。':'正在处理完整文献。')}{job.detail?.filename?` · ${job.detail.filename}`:''}</span>{job.updated_at&&<small>更新于 {time(job.updated_at)}{job.state==='running'&&job.lease_valid?' · 执行器在线':''}</small>}</div>
    {job.detail?.files?.length===1&&<small className="rag-job-file">{job.detail.files[0].filename} · {job.detail.files[0].completed} / {job.detail.files[0].total} 块</small>}
    {!!job.detail?.files&&job.detail.files.length>1&&<details className="rag-job-files"><summary>查看 {job.detail.files.length} 个原件的进度</summary>{job.detail.files.map((file,i)=><div key={i}><span>{file.filename}</span><small>{file.completed} / {file.total} 块</small><progress max={Math.max(1,file.total)} value={file.completed}/></div>)}</details>}
    {!!job.error&&<small className="rag-job-error">{job.error}</small>}
  </div>;
}

const number=(n:number)=>n.toLocaleString('zh-CN');
export function batchOutcome(batch:Totals){
  const finished=batch.succeeded+batch.failed+batch.cancelled+(batch.skipped||0)+(batch.dispatch_failed||0);
  // Count documents that have reached an outcome, not weighted stage estimates.
  // A rounded 100% must never appear while documents remain unfinished.
  const remaining=Math.max(0,batch.total-finished);
  const percent=batch.total?Math.min(remaining?99.9:100,Math.round(finished/batch.total*1000)/10):0;
  return {finished,remaining,percent};
}

export function batchElapsed(batch:Batch,sampledAt?:string,dispatching=false){
  const ended=batchOutcome(batch).remaining===0&&!dispatching;
  // PostgreSQL's terminal updated_at may be the start of a long write
  // transaction. It is a recorded endpoint, not a precise execution timer.
  const endpoint=ended?batch.updated_at:sampledAt;
  const start=Date.parse(batch.submitted_at),end=endpoint?Date.parse(endpoint):NaN;
  return {seconds:Number.isFinite(start)&&Number.isFinite(end)&&end>=start?Math.floor((end-start)/1000):null,ended};
}
export function formatElapsed(seconds:number){
  const total=Math.max(0,Math.floor(seconds)),days=Math.floor(total/86400),hours=Math.floor(total/3600)%24,minutes=Math.floor(total/60)%60;
  return `${days?`${days} 天 `:''}${days||hours?`${hours} 小时 `:''}${days||hours||minutes?`${minutes} 分 `:''}${total%60} 秒`;
}

export default function OverallProcessingProgress({value,batchId,onBatchChange,busy=false,onBulkAction,onViewFailed,onCancelAll,embedding,refreshError=''}:{value:ProcessingState;batchId:string;onBatchChange(id:string):void;busy?:boolean;onCancelAll?():Promise<void>;onViewFailed?():void;onBulkAction?(id:string,action:'cancel'|'retry'):Promise<void>;embedding?:{model:string;dimensions:number};refreshError?:string}){
  const batch=value.batch;
  const ingestion=value.ingestion_queue||value.queue;
  const cancellable=ingestion.queued+ingestion.running>0||!!value.dispatches?.some(d=>d.state==='dispatching');
  const dispatch=value.dispatches?.find(d=>d.id===batch?.batch_id);
  const outcome=batch?batchOutcome(batch):null;
  const elapsed=batch?batchElapsed(batch,value.sampled_at,dispatch?.state==='dispatching'):null;
  const active=value.active_jobs.filter(j=>['index','import'].includes(j.kind));
  const failed=value.overall?.failed??batch?.failed??0;
  const statuses=batch?[
    {key:'succeeded',label:'成功',count:batch.succeeded},
    {key:'failed',label:'失败',count:batch.failed},
    {key:'cancelled',label:'取消',count:batch.cancelled},
    {key:'skipped',label:'跳过',count:batch.skipped||0},
    {key:'dispatch_failed',label:'入队失败',count:batch.dispatch_failed||0},
    {key:'running',label:'执行中',count:batch.running},
    {key:'queued',label:'排队',count:batch.queued},
    {key:'pending_dispatch',label:'待入队',count:batch.pending_dispatch||0},
  ].filter(s=>s.count>0||['succeeded','failed','cancelled','queued','running'].includes(s.key)):[];
  return <section className="rag-overall-progress processing-monitor" aria-label="入库监控">
    <header className="processing-monitor-heading">
      <div className="processing-monitor-scope"><strong>入库监控</strong>{value.batches.length>0&&<label className="processing-batch-picker">批次<select aria-label="选择加工批次" value={batchId||batch?.batch_id||''} onChange={e=>onBatchChange(e.target.value)}>{value.batches.map(b=><option key={b.batch_id} value={b.batch_id}>{new Date(b.submitted_at).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})} · {number(b.total)} 份</option>)}</select></label>}</div>
      <div className="processing-monitor-actions">
        <span className="processing-monitor-queue">全部入库任务：执行 <b>{number(ingestion.running)}</b> · 排队 <b>{number(ingestion.queued)}</b></span>
        {onCancelAll&&<button type="button" className="button-secondary rag-cancel-all" disabled={busy||!cancellable} onClick={()=>void onCancelAll()}>取消全部入库任务</button>}
      </div>
    </header>
    <div className="processing-monitor-body">
      <div className="processing-batch-outcome">
        {batch&&outcome?<>
          <div className="processing-outcome-heading"><span>已结束 <b>{number(outcome.finished)}</b> / {number(batch.total)} 份 <b>{outcome.percent}%</b></span><span>未结束 {number(outcome.remaining)} 份</span></div>
          <div className="processing-outcome-track" role="progressbar" aria-label="当前批次进度" aria-valuemin={0} aria-valuemax={batch.total||1} aria-valuenow={outcome.finished} aria-valuetext={`已结束 ${number(outcome.finished)} / ${number(batch.total)} 份，成功 ${number(batch.succeeded)} 份，失败 ${number(batch.failed)} 份，取消 ${number(batch.cancelled)} 份`}>
            {statuses.filter(s=>['succeeded','failed','cancelled','skipped','dispatch_failed'].includes(s.key)&&s.count>0).map(s=><span key={s.key} className={`is-${s.key}`} style={{width:`${s.count/Math.max(1,batch.total)*100}%`}}/>)}</div>
          <dl className="processing-outcome-counts">{statuses.map(s=><div key={s.key} className={`is-${s.key}`}><dt>{s.label}</dt><dd>{number(s.count)}</dd></div>)}</dl>
          <p className="processing-outcome-note">结束包含失败、取消与跳过，不代表全部成功。</p>
          {elapsed&&<p className="processing-batch-elapsed" title={`本批提交于 ${new Date(batch.submitted_at).toLocaleString('zh-CN',{hour12:false})}。批次历时包含排队、服务停止及恢复的时间，不等同于累计执行耗时。`}><span>{elapsed.ended?'记录用时':'已用时'}</span><strong>{elapsed.seconds===null?'时间未记录':`${elapsed.ended?'约 ':''}${formatElapsed(elapsed.seconds)}`}</strong><small>{elapsed.ended?'按最后状态记录估算':'含排队与中断'}{refreshError?' · 截至上次刷新':''}</small></p>}
          {!!batch.models.filter(Boolean).length&&<p className="processing-batch-model">本批模型 <span>{batch.models.filter(Boolean).join('、')}</span></p>}
        </>:<p className="processing-idle-message">尚未提交加工批次。在下方选择文献后，可开始加工。</p>}
      </div>
      <div className="processing-live" aria-label="当前入库执行情况">
        <strong className="processing-live-label">当前执行 · 全部入库任务</strong>
        {active.length>0?active.map(job=><div className="processing-live-job" key={job.id}><strong className="processing-live-title" title={job.title||undefined}>{job.title||kinds[job.kind]||'入库任务'}</strong><DocumentProcessingProgress job={job} stageOnly/></div>):<p className="processing-idle-message">{ingestion.running>0?'任务执行中，正在读取阶段详情。':ingestion.queued>0?`${number(ingestion.queued)} 项等待领取，暂未发现正在执行的入库任务。`:cancellable?'正在检查文献并分批入队。':'当前没有正在执行或排队的入库任务。'}</p>}
      </div>
    </div>
    {!!dispatch?.error&&<div className="processing-dispatch-error" role="alert"><span>本批入队异常：{dispatch.error}。已入队的文献仍可继续加工。</span>{dispatch.state==='failed'&&onBulkAction&&<button type="button" className="button-secondary" disabled={busy} onClick={()=>void onBulkAction(dispatch.id,'retry')}>继续本批入队</button>}</div>}
    <footer className="processing-monitor-footer">
      <span className="processing-library-readiness"><strong>当前模型可检索</strong> <b>{number(value.library.ready)}</b> / {number(value.library.total)} 份{embedding&&<span className="processing-library-model" title={embedding.model}>{embedding.model} · {embedding.dimensions} 维</span>}</span>
      {failed>0&&onViewFailed&&<button type="button" className="processing-failure-link" onClick={onViewFailed}>查看全库失败文献（{number(failed)}）</button>}
      <span className={`processing-refresh${refreshError?' is-stale':''}`} title={refreshError||undefined}>{refreshError?'刷新失败，保留上次数据':`${value.sampled_at?`更新于 ${time(value.sampled_at)} · `:''}${cancellable?'自动刷新中':'暂无待执行任务'}`}</span>
    </footer>
    <details className="processing-monitor-details"><summary>入队详情与统计说明</summary>
      {dispatch&&<div className="processing-dispatch-detail"><strong>{dispatch.state==='dispatching'?'正在分批入队':dispatch.state==='failed'?'入队中断':dispatch.state==='cancelled'?'后续入队已取消':'文献检查与入队已结束'}</strong><span>已检查 {number(dispatch.result.cursor??0)} / {number(dispatch.result.total??0)} 份 · 新增任务 {number(dispatch.result.scheduled??0)} · 跳过 {number(dispatch.result.skipped??0)}</span>{dispatch.state==='dispatching'&&<progress aria-label="分批入队进度" max={100} value={dispatch.progress??0}/ >}{onBulkAction&&dispatch.state==='cancelled'&&<button type="button" className="button-secondary" disabled={busy} onClick={()=>void onBulkAction(dispatch.id,'retry')}>继续本批入队</button>}</div>}
      <p>本批按文献计数，解析后接续的向量任务只计一份；“未结束”包含执行中、排队和待入队。跳过表示本批未新增任务，可能是已有索引、已有任务或原文变化，不计作取消。入队失败表示尚未创建任务，需继续入队。</p>
      <p>上方队列与当前执行覆盖全部入库批次，按任务计数。“当前模型可检索”按当前模型配置及录文版本核验；历史批次成功、失败或取消，不直接决定当前是否可检索。入队结束不等于加工完成。</p>
      <p>“已用时”从本批最初提交时计算，随后台快照刷新，包含排队与服务中断；刷新失败时保留上次读数。历史批次的“记录用时”按最后状态更新时间估算，结束后不再增加，不代表累计实际执行时间。</p>
    </details>
  </section>;
}
