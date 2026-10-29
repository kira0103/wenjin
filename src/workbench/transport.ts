import { currentStorageAccount } from './storage';

export type ApiRequest = { path: string; method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; body?: unknown; contentType?: string };
export type ApiResponse = { status: number; contentType: string; body: any };
let libraryAdmin = false;
export function setLibraryAdminApi(enabled: boolean) { libraryAdmin = enabled; }

export function apiPath(path: string) {
  if (!path.startsWith('/api/') || path.includes('..') || path.includes('://') || path.includes('\\') || /[\r\n]/.test(path)) throw new Error('无效的服务接口');
  return path;
}

export function errorMessage(body: any, status: number) {
  const detail = body?.detail || body?.error || body?.message;
  return Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('；') : detail ? String(detail) : `资料服务请求失败（${status}）`;
}

export async function send(request: ApiRequest): Promise<ApiResponse> {
  apiPath(request.path);
  if (libraryAdmin && !/^\/api\/(admin|session|me)(\/|$)/.test(request.path)) {
    request = {...request, path: '/api/admin/library/' + request.path.slice(5)};
  }
  if (window.desktop) {
    const result = await window.desktop.request(request);
    if (result.status === 401) window.dispatchEvent(new Event('wenjin:unauthorized'));
    return result;
  }
  const headers: Record<string, string> = {};
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
  const response = await fetch(request.path, { method: request.method || 'GET', headers, body, credentials: 'same-origin' });
  const contentType = response.headers.get('content-type') || '';
  const value = contentType.includes('json') ? await response.json() : await response.arrayBuffer();
  if (response.status === 401 && request.path !== '/api/session') window.dispatchEvent(new Event('wenjin:unauthorized'));
  return { status: response.status, contentType, body: value };
}

export async function call<T>(request: ApiRequest): Promise<T> {
  const result = await send(request);
  if (result.status < 200 || result.status >= 300) throw new Error(errorMessage(result.body, result.status));
  return result.body as T;
}
