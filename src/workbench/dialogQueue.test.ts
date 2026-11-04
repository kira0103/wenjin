import {describe,it,expect,vi} from 'vitest';
import {DialogQueue,validDialogInput} from './dialogQueue';

describe('page dialog decisions',()=>{
  it('keeps decisions separate and resolves only the active request',async()=>{
    const queue=new DialogQueue(),listener=vi.fn();queue.subscribe(listener);
    const first=queue.request('confirm',{title:'入库'}),second=queue.request('confirm',{title:'清理'}),secondDone=vi.fn();void second.then(secondDone);
    expect(queue.snapshot()?.options.title).toBe('入库');queue.settle(true);
    expect(await first).toBe(true);expect(secondDone).not.toHaveBeenCalled();expect(queue.snapshot()?.options.title).toBe('清理');
    queue.cancel();expect(await second).toBe(false);expect(queue.snapshot()).toBeNull();expect(listener).toHaveBeenCalled();
  });
  it('distinguishes clearing a page label from cancelling input',async()=>{
    const queue=new DialogQueue();const clear=queue.request('input',{title:'页码',label:'页码',defaultValue:'卷一'});
    queue.settle('');expect(await clear).toBe('');
    const cancel=queue.request('input',{title:'页码',label:'页码'});queue.cancel();expect(await cancel).toBeNull();
  });
  it('cannot confirm removal before the complete title matches',async()=>{
    const queue=new DialogQueue(),options={title:'移出',label:'题名',match:'一乘佛性究竟論',required:true};
    const result=queue.request('input',options),done=vi.fn();void result.then(done);
    expect(validDialogInput('',options)).toBe(false);expect(validDialogInput('一乘佛性',options)).toBe(false);
    queue.settle('一乘佛性');await Promise.resolve();expect(done).not.toHaveBeenCalled();expect(queue.snapshot()).not.toBeNull();
    queue.settle('一乘佛性究竟論');expect(await result).toBe('一乘佛性究竟論');
  });
  it('cancels pending work when the dialog host is removed',async()=>{
    const queue=new DialogQueue(),confirm=queue.request('confirm',{title:'启动任务'}),input=queue.request('input',{title:'修改页码',label:'页码'});
    queue.cancelAll();expect(await confirm).toBe(false);expect(await input).toBeNull();expect(queue.snapshot()).toBeNull();
  });
  it('rejects excessive input and non-text confirmation values',async()=>{
    const queue=new DialogQueue(),result=queue.request('input',{title:'页码',label:'页码',maxLength:3});
    queue.settle(true);expect(queue.snapshot()).not.toBeNull();queue.settle('1234');expect(queue.snapshot()).not.toBeNull();
    queue.settle('123');expect(await result).toBe('123');
  });
});
