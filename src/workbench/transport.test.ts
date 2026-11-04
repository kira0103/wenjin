import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiPath, call, send, setLibraryAdminApi, setSystemLibraryApi } from './transport';
import { setStorageAccount } from './storage';

afterEach(() => { vi.useRealTimers(); setLibraryAdminApi(false);setSystemLibraryApi(false);setStorageAccount('');vi.unstubAllGlobals(); });

describe('API transport', () => {
  it('binds login and business requests to their page entry',async()=>{
    const fetch=vi.fn().mockImplementation(()=>Promise.resolve(Response.json({ok:true})));vi.stubGlobal('fetch',fetch);
    vi.stubGlobal('window',{location:{pathname:'/admin/'},dispatchEvent:vi.fn()});
    await send({path:'/api/session'});
    await send({path:'/api/admin/users'});
    vi.stubGlobal('window',{location:{pathname:'/app/'},dispatchEvent:vi.fn()});
    await send({path:'/api/session',method:'DELETE'});
    await send({path:'/api/projects'});
    expect(fetch.mock.calls.map(c=>c[1].headers['X-Wenjin-Portal'])).toEqual(['admin','admin','app','app']);
  });
  it('reads system originals separately while keeping research and uploads private',async()=>{
    vi.stubGlobal('window',{dispatchEvent:vi.fn()});
    const fetch=vi.fn().mockImplementation(()=>Promise.resolve(Response.json({ok:true})));vi.stubGlobal('fetch',fetch);
    setSystemLibraryApi(true);
    await call({path:'/api/knowledge/document-tree?group_id=system'});
    await call({path:'/api/knowledge/editions/source/files/original'});
    await call({path:'/api/knowledge/anchors',method:'POST',body:{}});
    await call({path:'/api/knowledge/projects/private/sources'});
    await call({path:'/api/uploads',method:'POST',body:{}});
    setSystemLibraryApi(false);
    await call({path:'/api/knowledge/document-tree'});
    expect(fetch.mock.calls.map(item=>item[0])).toEqual(['/api/system-library/knowledge/document-tree?group_id=system','/api/system-library/knowledge/editions/source/files/original','/api/knowledge/anchors','/api/knowledge/projects/private/sources','/api/uploads','/api/knowledge/document-tree']);
  });
  it('routes public library maintenance separately and restores private requests on leave',async()=>{
    vi.stubGlobal('window',{dispatchEvent:vi.fn()});
    const fetch=vi.fn().mockImplementation(()=>Promise.resolve(Response.json({ok:true})));vi.stubGlobal('fetch',fetch);
    setLibraryAdminApi(true);
    await call({path:'/api/knowledge/catalog'});
    await call({path:'/api/uploads',method:'POST',body:{files:[]}});
    await call({path:'/api/admin/users'});
    await call({path:'/api/me'});
    setLibraryAdminApi(false);
    await call({path:'/api/knowledge/notes'});
    expect(fetch.mock.calls.map(item=>item[0])).toEqual(['/api/admin/library/knowledge/catalog','/api/admin/library/uploads','/api/admin/users','/api/me','/api/knowledge/notes']);
  });
  it('rejects paths outside the API boundary', () => {
    for (const path of ['https://outside.invalid/api', '//outside.invalid/api', '/api/../file', '/api/\\file']) expect(() => apiPath(path)).toThrow();
    expect(apiPath('/api/projects?query=%E5%8E%9F%E6%96%87')).toContain('/api/projects');
  });
  it('sends browser file bytes with session credentials and returns image bytes', async () => {
    vi.stubGlobal('window', {dispatchEvent:vi.fn()});
    const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array([1,2,3]), {headers:{'Content-Type':'image/png'}}));
    vi.stubGlobal('fetch', fetch);
    const file = new Blob(['original']);
    const result = await send({path:'/api/uploads/a/files/0?offset=0',method:'PUT',body:file});
    expect(fetch.mock.calls[0][1]).toMatchObject({body:file,credentials:'same-origin',headers:{'Content-Type':'application/octet-stream'}});
    expect(result.body).toBeInstanceOf(ArrayBuffer);
  });
  it('keeps the Electron bridge as the desktop transport and requests reauthentication on 401', async () => {
    const desktop = { request:vi.fn().mockResolvedValue({status:401,contentType:'application/json',body:{detail:'请重新连接'}}) };
    const dispatchEvent = vi.fn();
    vi.stubGlobal('window', {desktop,dispatchEvent});
    await expect(call({path:'/api/projects'})).rejects.toThrow('请重新连接');
    expect(desktop.request).toHaveBeenCalledOnce();
    expect(dispatchEvent.mock.calls[0][0].type).toBe('wenjin:unauthorized');
  });
  it('binds a Web request to the account whose draft is open',async()=>{
    vi.stubGlobal('window',{dispatchEvent:vi.fn()});setStorageAccount('alice');
    const fetch=vi.fn().mockResolvedValue(Response.json({ok:true}));vi.stubGlobal('fetch',fetch);
    await call({path:'/api/tasks',method:'POST',body:{source_text:'private-draft'}});
    expect(fetch.mock.calls[0][1].headers['X-Wenjin-Account']).toBe('alice');
  });
});


describe('visible request failures',()=>{
  it('reports a network failure without exposing the request body or query',async()=>{
    const dispatchEvent=vi.fn();vi.stubGlobal('window',{dispatchEvent});
    vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(call({path:'/api/knowledge/catalog?query=private-title'})).rejects.toThrow('无法连接后台服务');
    const event=dispatchEvent.mock.calls[0][0];expect(event.type).toBe('wenjin:request-error');
    expect(event.detail.operation).toBe('文献读取失败');expect(JSON.stringify(event.detail)).not.toContain('private-title');
  });
  it('keeps the backend diagnostic identifier and handles non-JSON server errors',async()=>{
    const dispatchEvent=vi.fn();vi.stubGlobal('window',{dispatchEvent});
    vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(Response.json({detail:'文献库查询超时',code:'DATABASE_TIMEOUT',request_id:'abc123def456'},{status:503})).mockResolvedValueOnce(new Response('<html>proxy error</html>',{status:502})));
    await expect(call({path:'/api/knowledge/rag-documents'})).rejects.toThrow('文献库查询超时');
    expect(dispatchEvent.mock.calls[0][0].detail).toMatchObject({status:503,code:'DATABASE_TIMEOUT',requestId:'abc123def456'});
    await expect(call({path:'/api/projects'})).rejects.toThrow('后台服务连接异常');
    expect(JSON.stringify(dispatchEvent.mock.calls)).not.toContain('<html>');
  });
  it('bounds a hung request and aborts the browser connection',async()=>{
    vi.useFakeTimers();const dispatchEvent=vi.fn();vi.stubGlobal('window',{dispatchEvent});
    const fetch=vi.fn().mockImplementation(()=>new Promise(()=>{}));vi.stubGlobal('fetch',fetch);
    const pending=expect(call({path:'/api/projects',timeoutMs:100})).rejects.toThrow('请求超时');
    await vi.advanceTimersByTimeAsync(101);await pending;
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);expect(fetch).toHaveBeenCalledOnce();
    expect(dispatchEvent.mock.calls[0][0].detail.code).toBe('REQUEST_TIMEOUT');
  });
  it('does not automatically repeat a failed write',async()=>{
    const dispatchEvent=vi.fn();vi.stubGlobal('window',{dispatchEvent});
    const fetch=vi.fn().mockRejectedValue(new TypeError('disconnected'));vi.stubGlobal('fetch',fetch);
    await expect(call({path:'/api/knowledge/rag-process-all',method:'POST',body:{scope:'library'}})).rejects.toThrow();
    expect(fetch).toHaveBeenCalledOnce();expect(dispatchEvent.mock.calls[0][0].detail.action).toContain('先检查操作结果');
  });
});
