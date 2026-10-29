import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiPath, call, send, setLibraryAdminApi } from './transport';
import { setStorageAccount } from './storage';

afterEach(() => { setLibraryAdminApi(false);setStorageAccount('');vi.unstubAllGlobals(); });

describe('API transport', () => {
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
    for (const path of ['https://example.com/api', '//example.com/api', '/api/../file', '/api/\\file']) expect(() => apiPath(path)).toThrow();
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
