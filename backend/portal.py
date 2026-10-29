"""Multi-account Web portal with isolated user workspaces and an admin console."""
import argparse
from contextlib import asynccontextmanager
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, unquote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .auth import same_origin
from .portal_accounts import Accounts, AccountError, SESSION_SECONDS, public_user
from .portal_fleet import WorkspaceFleet

COOKIE = 'wenjin_portal_session'


class UserCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=48)
    display_name: str = Field(min_length=1, max_length=80)
    password: SecretStr
    role: str = 'user'


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=80)
    role: str
    enabled: bool


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: SecretStr


class PasswordChange(PasswordReset):
    current_password: SecretStr


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(default='', max_length=500)
    model: str = Field(default='', max_length=200)
    provider_name: str = Field(default='管理员配置的模型', max_length=100)
    context_tokens: int | None = Field(default=None, ge=4096, le=2000000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=262144)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip('/')
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('请填写不含凭据的 HTTP 或 HTTPS 模型服务地址')
        return value


def restricted_workspace_api(path, method):
    if path.startswith('/api/admin') or path.startswith('/api/me'):
        return True
    if path in {'/api/service/exit', '/api/collation/shutdown', '/api/knowledge/settings',
                '/api/knowledge/connection-test', '/api/knowledge/document-imports/preview', '/api/knowledge/core/import'}:
        return True
    if path == '/api/settings' and method != 'GET':
        return True
    if path == '/api/knowledge/activation' and method != 'GET':
        return True
    if path.startswith('/api/knowledge/exports'):
        return True
    if path.startswith('/api/knowledge/migrations') or path.startswith('/api/uploads'):
        return True
    if method not in {'GET', 'HEAD'}:
        if path == '/api/materials/import' or path == '/api/library' or path.startswith('/api/library/') or re.fullmatch(r'/api/references/[^/]+/(add-to-library|confirm-version)',path):
            return True
        suffix = path.removeprefix('/api/knowledge/')
        if suffix in {'editions','catalog','document-entries/move','literature-memberships'}:
            return True
        if suffix.startswith(('document-groups/','document-imports/','literature-categories','catalog/works/','catalog/editions/','reference-candidates/')):
            return True
        if re.fullmatch(r'editions/[^/]+(?:/(?:imports|revisions|reindex|pages/[^/]+/[^/]+/label))?', suffix):
            return True
    return False


def create_app(root, ui_dir=None, fleet=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    accounts = Accounts(root / 'private')
    fleet = fleet or WorkspaceFleet(root)
    upstream = httpx.AsyncClient(timeout=httpx.Timeout(240, connect=5), limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
    @asynccontextmanager
    async def lifespan(_app):
        # Workspace hosts outlive a portal restart; their workers keep executing.
        try:
            yield
        finally:
            await upstream.aclose()
    application = FastAPI(title='问津 Web 门户', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.state.accounts = accounts
    application.state.fleet = fleet

    @application.exception_handler(RequestValidationError)
    async def safe_validation(_request, error):
        return JSONResponse({'detail': [{'loc': list(item['loc']), 'msg': item['msg'], 'type': item['type']} for item in error.errors()]}, status_code=422)

    @application.exception_handler(AccountError)
    async def account_error(_request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @application.middleware('http')
    async def authorization(request, call_next):
        path = request.url.path
        if path.startswith('/api/'):
            if request.method not in {'GET', 'HEAD'} and not same_origin(request):
                return JSONResponse({'detail': '请求来源与服务不一致'}, status_code=403)
            user = await run_in_threadpool(accounts.session, request.cookies.get(COOKIE, ''))
            request.state.user = user
            bound_account = request.headers.get('X-Wenjin-Account')
            if user and bound_account and bound_account != user['id'] and (path != '/api/session' or request.method == 'DELETE'):
                return JSONResponse({'detail': '当前浏览器已切换账号，请重新登录此页面', 'code': 'account_changed'}, status_code=401)
            if path != '/api/session':
                if not user:
                    return JSONResponse({'detail': '请先登录'}, status_code=401)
                if user['password_change_required'] and path not in {'/api/me', '/api/me/password'}:
                    return JSONResponse({'detail': '请先修改临时密码', 'code': 'password_change_required'}, status_code=403)
                if path.startswith('/api/admin') and user['role'] != 'admin':
                    return JSONResponse({'detail': '此操作需要管理员权限'}, status_code=403)
        response = await call_next(request)
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @application.get('/health')
    def health():
        return {'ok': True, 'mode': 'portal'}

    @application.get('/api/session')
    def session(request: Request):
        user = request.state.user
        return {'authenticated': bool(user), 'mode': 'portal', 'user': public_user(user) if user else None}

    @application.post('/api/session')
    async def login(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                raise HTTPException(400, '登录信息格式无效')
        try:
            value = json.loads(body)
            username, password = value.get('username', ''), value.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError()
        except (ValueError, AttributeError):
            raise HTTPException(400, '登录信息格式无效') from None
        try:
            token, user = await run_in_threadpool(accounts.authenticate, username, password, request.client.host if request.client else '')
        except AccountError as error:
            raise HTTPException(401, str(error)) from None
        response = JSONResponse({'authenticated': True, 'mode': 'portal', 'user': user})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/api')
        return response

    @application.delete('/api/session')
    def logout(request: Request):
        accounts.logout(request.cookies.get(COOKIE, ''))
        response = JSONResponse({'authenticated': False, 'mode': 'portal'})
        response.delete_cookie(COOKIE, path='/api')
        return response

    @application.get('/api/me')
    def me(request: Request):
        return {'user': public_user(request.state.user), 'workspace': fleet.state(request.state.user)}

    @application.post('/api/me/password')
    def change_password(request: Request, payload: PasswordChange):
        user = request.state.user
        accounts.reset_password(user['id'], payload.password.get_secret_value(), user['id'], payload.current_password.get_secret_value())
        response = JSONResponse({'changed': True, 'reauthenticate': True})
        response.delete_cookie(COOKIE, path='/api')
        return response

    @application.get('/api/admin/overview')
    def overview():
        users = accounts.list()
        workspaces = [fleet.state(accounts.get(user['id'])) for user in users]
        return {'users': len(users), 'enabled_users': sum(bool(user['enabled']) for user in users),
                'administrators': sum(user['role'] == 'admin' and bool(user['enabled']) for user in users),
                'running_workspaces': sum(item['state'] == 'ready' for item in workspaces),
                'active_tasks': sum(item.get('metrics', {}).get('active_tasks', 0) for item in workspaces),
                'failed_workspaces': sum(item['state'] == 'failed' for item in workspaces)}

    @application.get('/api/admin/users')
    def users():
        return [{**user, 'workspace': fleet.state(accounts.get(user['id']))} for user in accounts.list()]

    @application.post('/api/admin/users')
    def create_user(request: Request, payload: UserCreate):
        return accounts.create(payload.username, payload.display_name, payload.password.get_secret_value(), payload.role, request.state.user['id'])

    @application.patch('/api/admin/users/{identifier}')
    def update_user(identifier: str, payload: UserUpdate, request: Request):
        return accounts.update(identifier, request.state.user['id'], **payload.model_dump())

    @application.post('/api/admin/users/{identifier}/password')
    def reset_password(identifier: str, payload: PasswordReset, request: Request):
        if identifier == request.state.user['id']:
            raise HTTPException(409, '请在个人设置中修改自己的密码')
        accounts.reset_password(identifier, payload.password.get_secret_value(), request.state.user['id'])
        return {'changed': True}

    @application.post('/api/admin/users/{identifier}/stop-workspace')
    def stop_workspace(identifier: str, request: Request):
        user = accounts.get(identifier)
        if user['enabled']:
            raise HTTPException(409, '请先停用账号，再停止工作区，避免活跃客户端立即重新启动')
        fleet.stop(user)
        with accounts.connection() as db:
            accounts.audit(db, request.state.user['id'], 'workspace.stop', identifier)
        return {'stopping': True}

    @application.get('/api/admin/settings')
    def settings():
        return {**fleet.settings(), 'has_api_key': bool(os.environ.get('SHIXUE_MODEL_API_KEY')),
                'api_key_managed': True, 'database_mode': 'shared-library-private-research'}

    @application.put('/api/admin/settings')
    def update_settings(payload: ModelConfiguration, request: Request):
        value = payload.model_dump()
        if bool(value['base_url']) != bool(value['model'].strip()):
            raise HTTPException(400, '模型地址和名称需要同时填写')
        fleet.save_settings(value)
        with accounts.connection() as db:
            accounts.audit(db, request.state.user['id'], 'model.configure', details={'model': value['model']})
        return settings()

    @application.get('/api/admin/audit')
    def audit():
        return accounts.recent_audit()

    def validate_path(path):
        decoded = path
        for _ in range(3):
            decoded = unquote(decoded)
        if any(char in decoded for char in '\\?#%\r\n\x00') or any(part in {'.', '..'} for part in decoded.split('/')):
            raise HTTPException(400, '接口路径无效')
        return decoded

    @application.api_route('/api/admin/library/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'])
    async def library_proxy(path: str, request: Request):
        path = validate_path(path)
        route = '/api/' + path
        if not (route.startswith(('/api/knowledge/', '/api/uploads')) or route == '/api/settings' and request.method == 'GET'):
            raise HTTPException(403, '此入口仅用于维护公共资料库')
        if route.startswith(('/api/knowledge/exports','/api/knowledge/migrations')) or route in {
            '/api/knowledge/settings','/api/knowledge/connection-test','/api/knowledge/core/import','/api/knowledge/document-imports/preview'}:
            raise HTTPException(403, '此操作需要服务器维护入口')
        if route == '/api/knowledge/activation' and request.method != 'GET':
            raise HTTPException(403, '公共资料库已由服务器初始化')
        try:
            target = await run_in_threadpool(fleet.ensure_library)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None
        if request.method == 'GET' and re.fullmatch(r'/api/knowledge/editions/[a-fA-F0-9-]{36}/delete-preview', route):
            current = await run_in_threadpool(accounts.session, request.cookies.get(COOKIE,''))
            if not current or current['id'] != request.state.user['id']:
                raise HTTPException(401,'登录状态已更新，请重新登录')
            if current['role'] != 'admin':raise HTTPException(403,'此操作需要管理员权限')
            return await run_in_threadpool(fleet.library_usage, route.split('/')[-2])
        if request.method not in {'GET','HEAD'}:
            with accounts.connection() as db:
                accounts.audit(db, request.state.user['id'], 'library.request', details={'method':request.method,'path':route})
        return await forward(request, target, route, library=True)

    @application.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'])
    async def workspace_proxy(path: str, request: Request):
        path = validate_path(path)
        route = '/api/' + path
        # Even administrators enter their own research workspace here. Administration has separate routes.
        if restricted_workspace_api(route, request.method):
            raise HTTPException(403, '此配置由系统管理员管理')
        try:
            target = await run_in_threadpool(fleet.ensure, request.state.user)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None
        return await forward(request, target, route)

    async def forward(request, target, route, library=False):
        current = await run_in_threadpool(accounts.session, request.cookies.get(COOKIE, ''))
        if not current or current['id'] != request.state.user['id']:
            raise HTTPException(401, '登录状态已更新，请重新登录')
        if library and current['role'] != 'admin':
            raise HTTPException(403, '此操作需要管理员权限')
        headers = {'X-App-Token': target.token}
        for name in ('content-type', 'accept', 'range', 'if-range'):
            if name in request.headers:
                headers[name] = request.headers[name]
        url = target.url + route + ('?' + request.url.query if request.url.query else '')
        try:
            incoming = request.stream() if request.method not in {'GET', 'HEAD'} else None
            forwarded = upstream.build_request(request.method, url, headers=headers, content=incoming)
            response = await upstream.send(forwarded, stream=True)
        except httpx.HTTPError:
            raise HTTPException(503, '个人工作区暂时无法连接，请稍后重试') from None
        allowed_headers = {name: value for name, value in response.headers.items() if name.lower() not in {
            'connection', 'transfer-encoding', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade', 'set-cookie'}}
        if allowed_headers.get('location', '').startswith(target.url):
            allowed_headers['location'] = allowed_headers['location'][len(target.url):]
        if library and allowed_headers.get('location','').startswith('/api/'):
            allowed_headers['location'] = '/api/admin/library/' + allowed_headers['location'][5:]
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers=allowed_headers, background=BackgroundTask(response.aclose))

    if ui_dir:
        directory = Path(ui_dir).resolve()

        @application.get('/{path:path}', include_in_schema=False)
        def frontend(path: str):
            target = (directory / path).resolve()
            if not target.is_relative_to(directory):
                raise HTTPException(404)
            if target.is_file():
                return FileResponse(target)
            if not path or path.rstrip('/') in {'app', 'admin', 'login'}:
                html = (directory / 'index.html').read_text(encoding='utf-8').replace('<head>', '<head><base href="/">', 1)
                return HTMLResponse(html, headers={'Cache-Control': 'no-store'})
            raise HTTPException(404)

    return application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--ui-dir', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--init-admin', metavar='USERNAME')
    parser.add_argument('--stop-workspaces', action='store_true')
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # The portal owns passwords, provisioner credentials and runtime access tokens.
    from .knowledge.managed import _protect, _current_sid
    _protect(root, _current_sid() if os.name == 'nt' else '')
    accounts = Accounts(root / 'private')
    if args.init_admin:
        password = os.environ.get('SHIXUE_PORTAL_ADMIN_PASSWORD', '')
        try:
            accounts.create(args.init_admin, '系统管理员', password, 'admin', require_change=False, bootstrap=True)
        except AccountError as error:
            parser.error(str(error))
        print('管理员已建立，请使用账号和密码登录 /admin')
        return
    if args.stop_workspaces:
        fleet = WorkspaceFleet(root)
        fleet.request_stop_all([accounts.get(user['id']) for user in accounts.list()])
        print('已向工作区发出正常停止请求；后台任务将在保存后退出')
        return
    if not any(user['role'] == 'admin' and user['enabled'] for user in accounts.list()):
        parser.error('请先设置 SHIXUE_PORTAL_ADMIN_PASSWORD 并使用 --init-admin 建立管理员')
    if args.ui_dir and not (args.ui_dir.resolve() / 'index.html').is_file():
        parser.error('--ui-dir 需要包含已构建的网页')
    from .knowledge.managed import DirectoryLease
    lease = DirectoryLease(root / 'private' / 'portal.lock')
    lease.acquire()
    try:
        import uvicorn
        uvicorn.run(create_app(root, args.ui_dir), host=args.host, port=args.port, log_level='info')
    finally:
        lease.release()


if __name__ == '__main__':
    main()
