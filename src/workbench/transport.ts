import {ApiError,operationName,reportFailure,statusAction} from './errors';
import { currentStorageAccount } from './storage';

/** `silent`：页面已在本地展示该错误时，要求 transport 不再派发全局错误浮层。 */
export type ApiRequest = { path: string; method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; body?: unknown; contentType?: string; timeoutMs?: number; silent?: boolean };
export type ApiResponse = { status: number; contentType: string; body: any };
let libraryAdmin = false;
let systemLibrary = false;
export function setLibraryAdminApi(enabled: boolean) { libraryAdmin = enabled; }
export function setSystemLibraryApi(enabled: boolean) { systemLibrary = enabled; }

export function apiPath(path: string) {
  if (!path.startsWith('/api/') || path.includes('..') || path.includes('://') || path.includes('\\') || /[\r\n]/.test(path)) throw new Error('无效的服务接口');
  return path;
}

export function errorMessage(body: any, status: number) {
  const detail = body?.detail || body?.error || body?.message;
  return Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('；') : detail ? String(detail) : ({401:'登录状态已失效',403:'当前账号没有操作权限',404:'请求的资料不存在或已被删除',413:'上传文件超过允许大小',429:'请求过于频繁',500:'后台服务发生异常',502:'后台服务连接异常',503:'后台服务暂时不可用',504:'后台服务响应超时'} as Record<number,string>)[status]||`资料服务请求失败（${status}）`;
}

export async function send(request: ApiRequest, signal?:AbortSignal): Promise<ApiResponse> {
  apiPath(request.path);
  if (libraryAdmin && !/^\/api\/(admin|session|me)(\/|$)/.test(request.path)) {
    request = {...request, path: '/api/admin/library/' + request.path.slice(5)};
  } else if (systemLibrary && (!request.method || request.method === 'GET') && /^\/api\/knowledge\/(health|activation|catalog|editions|document-groups|document-tree|literature-categories|document-entries|core)(\/|\?|$)/.test(request.path)) {
    request = {...request, path: '/api/system-library/' + request.path.slice(5)};
  }
  if (window.desktop) {
    const {timeoutMs: _timeout,...bridgeRequest}=request;
    const result = await window.desktop.request(bridgeRequest);
    if (result.status === 401) window.dispatchEvent(new Event('wenjin:unauthorized'));
    return result;
  }
  const entry=window.location?.pathname;
  const headers: Record<string, string> = entry?{'X-Wenjin-Portal':/^\/admin(?:\/|$)/.test(entry)?'admin':'app'}:{};
  const account = currentStorageAccount();
  if (account) headers['X-Wenjin-Account'] = account;
  let body: BodyInit | undefined;
  if (request.body instanceof Blob || request.body instanceof ArrayBuffer || ArrayBuffer.isView(request.body)) {
    body = request.body as BodyInit;
    headers['Content-Type'] = request.contentType || 'application/octet-stream';
  } else if (request.body !== undefined) {
    body = JSON.stringify(request.body);
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(request.path, { method: request.method || 'GET', headers, body, credentials: 'same-origin', signal });
  const contentType = response.headers.get('content-type') || '';
  const value = contentType.includes('json') ? await response.json() : await response.arrayBuffer();
  if (response.status === 401 && request.path !== '/api/session') window.dispatchEvent(new Event('wenjin:unauthorized'));
  return { status: response.status, contentType, body: value };
}

export async function call<T>(request: ApiRequest): Promise<T> {
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined,timedOut=false;
  try{
    const result=await Promise.race([send(request,controller.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{timedOut=true;controller.abort();reject(new Error('timeout'));},request.timeoutMs??(request.method&&request.method!=='GET'?240000:30000));})]);
    if(result.status<200||result.status>=300){
      const body=result.body;
      throw new ApiError({message:errorMessage(body,result.status),action:statusAction(result.status,request.method),operation:operationName(request.path,request.method),status:result.status,code:typeof body?.code==='string'?body.code:undefined,requestId:typeof body?.request_id==='string'&&/^[a-zA-Z0-9_-]{1,64}$/.test(body.request_id)?body.request_id:undefined});
    }
    return result.body as T;
  }catch(error){
    const failure=error instanceof ApiError?error:new ApiError({message:timedOut?'请求超时，后台未能及时响应':error instanceof SyntaxError?'后台返回的数据格式异常':'无法连接后台服务，请检查资料服务是否启动及网络连接',action:statusAction(503,request.method),operation:operationName(request.path,request.method),code:timedOut?'REQUEST_TIMEOUT':'CONNECTION_ERROR'});
    // 页内错误优先：4xx 由页面就地展示；全局出口只接未捕获异常、网络/超时和 5xx。
    const status=failure.notice.status;
    if(!request.silent&&status!==401&&(status===undefined||status>=500))reportFailure(failure.notice);
    throw failure;
  }finally{clearTimeout(timer);}
}
