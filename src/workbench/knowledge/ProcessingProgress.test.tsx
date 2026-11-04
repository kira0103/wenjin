import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import OverallProcessingProgress,{batchOutcome,batchElapsed,formatElapsed,type ProcessingState} from './ProcessingProgress';

function state():ProcessingState {
  const batch={batch_id:'current',submitted_at:'2026-11-03T06:59:46Z',updated_at:'2026-11-03T07:00:00Z',total:3882,succeeded:20,failed:2,cancelled:1,queued:3858,running:1,percent:.6,models:['intfloat/multilingual-e5-large']};
  return {batch,batches:[batch],overall:{...batch,total:15295,succeeded:11314,failed:0,cancelled:122,percent:74.8},queue:{queued:3858,running:2},ingestion_queue:{queued:3858,running:1},library:{ready:11437,total:15295},active_jobs:[],dispatches:[{id:'current',state:'succeeded',progress:100,error:'',result:{cursor:3882,scheduled:3882,skipped:0,total:3882}}]};
}
const render=(value:ProcessingState,refreshError='')=>renderToStaticMarkup(<OverallProcessingProgress value={value} batchId="current" onBatchChange={()=>{}} embedding={{model:'BAAI/bge-small-zh-v1.5',dimensions:512}} refreshError={refreshError}/>);

describe('ingestion monitoring scopes',()=>{
  it('uses the server snapshot for elapsed time, including queue and interruptions',()=>{
    const value=state();value.sampled_at='2026-11-03T09:02:51Z';
    expect(batchElapsed(value.batch!,value.sampled_at)).toEqual({seconds:7385,ended:false});
    const html=render(value);
    expect(html).toContain('已用时');expect(html).toContain('2 小时 3 分 5 秒');expect(html).toContain('含排队与中断');
    expect(render(value,'断连')).toContain('截至上次刷新');
  });
  it('freezes completed batches at their recorded endpoint and marks the estimate',()=>{
    const value=state();value.batch={...value.batch!,total:23,queued:0,running:0,updated_at:'2026-11-03T08:00:01Z'};value.sampled_at='2026-11-04T09:00:00Z';
    expect(batchElapsed(value.batch,value.sampled_at)).toEqual({seconds:3615,ended:true});
    expect(render(value)).toContain('约 1 小时 0 分 15 秒');
    expect(render(value)).toContain('按最后状态记录估算');
    expect(batchElapsed(value.batch,value.sampled_at,true).ended).toBe(false);
  });
  it('handles missing timestamps and durations across days without using the browser clock',()=>{
    expect(batchElapsed(state().batch!).seconds).toBeNull();
    expect(batchElapsed({...state().batch!,submitted_at:'invalid'},'2026-11-03T08:00:00Z').seconds).toBeNull();
    expect(batchElapsed(state().batch!,'2026-11-02T08:00:00Z').seconds).toBeNull();
    expect(formatElapsed(0)).toBe('0 秒');expect(formatElapsed(90061)).toBe('1 天 1 小时 1 分 1 秒');
  });
  it('uses document outcomes and never presents weighted progress as completed work',()=>{
    const batch=state().batch!;
    expect(batchOutcome({...batch,total:100,succeeded:40,failed:10,cancelled:5,skipped:3,dispatch_failed:2,percent:99})).toEqual({finished:60,remaining:40,percent:60});
    expect(batchOutcome({...batch,total:15000,succeeded:14999,failed:0,cancelled:0})).toEqual({finished:14999,remaining:1,percent:99.9});
  });
  it('keeps selected batch, current model coverage and task queue separate',()=>{
    const html=render(state());
    expect(html).toContain('aria-valuenow="23"');
    expect(html).toContain('aria-valuemax="3882"');
    expect(html).toContain('0.6%');
    expect(html).not.toContain('74.8%');
    expect(html).not.toContain('100%');
    expect(html).toContain('当前模型可检索');
    expect(html).toContain('11,437');
    expect(html).toContain('intfloat/multilingual-e5-large');
    expect(html).toContain('BAAI/bge-small-zh-v1.5');
    expect(html).toContain('结束包含失败、取消与跳过');
  });
  it('shows actual ingestion progress and excludes model-download tasks',()=>{
    const value=state();
    value.active_jobs=[{id:'model',kind:'embedding_model',state:'running',stage:'download_embedding',progress:80,error:'',title:'模型下载'},
      {id:'doc',kind:'index',state:'running',stage:'writing_index',progress:97,error:'',title:'新元史',detail:{completed:200,total:500,unit:'chunks'}}];
    const html=render(value);
    expect(html).toContain('新元史');expect(html).toContain('写入向量索引');expect(html).toContain('200 / 500 个分块');
    expect(html).toContain('40%');expect(html).not.toContain('97%');
    expect(html).not.toContain('模型下载');
  });
  it('does not report an idle executor as offline or completed, and marks stale data',()=>{
    const value=state();value.ingestion_queue={queued:3858,running:0};
    const html=render(value,'网络断开');
    expect(html).toContain('暂未发现正在执行的入库任务');
    expect(html).toContain('刷新失败，保留上次数据');
    expect(html).not.toContain('自动刷新中');
  });
  it('does not call empty history a completed batch',()=>{
    const value=state();value.batch=null;value.batches=[];value.dispatches=[];value.queue={queued:0,running:0};value.ingestion_queue=value.queue;
    expect(render(value)).toContain('尚未提交加工批次');
    expect(render(value)).not.toContain('role="progressbar"');
  });
});
