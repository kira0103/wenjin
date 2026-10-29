"""Authentication shared by the standalone HTTP service and the desktop host."""
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = 'wenjin_session'
SESSION_SECONDS = 12 * 60 * 60


def same_origin(request: Request):
    origin = request.headers.get('origin')
    if origin:
        expected = urlsplit(str(request.base_url))
        supplied = urlsplit(origin)
        return (supplied.scheme, supplied.netloc) == (expected.scheme, expected.netloc)
    return request.headers.get('sec-fetch-site') not in {'cross-site', 'same-site'}


def signature(state, value):
    return hmac.new(state.token.encode(), value.encode(), hashlib.sha256).hexdigest()


def authenticated(request, state):
    if not state.token:
        return False
    token = request.headers.get('X-App-Token', '')
    authorization = request.headers.get('authorization', '')
    if authorization.startswith('Bearer '):
        token = authorization[7:]
    if token and secrets.compare_digest(token.encode(), state.token.encode()):
        return True
    cookie = request.cookies.get(COOKIE, '')
    try:
        issued, nonce, digest = cookie.split('.')
        age = time.time() - int(issued)
        return 0 <= age <= SESSION_SECONDS and secrets.compare_digest(digest, signature(state, issued + '.' + nonce))
    except (ValueError, TypeError):
        return False


class ServiceAuthentication(BaseHTTPMiddleware):
    def __init__(self, app, state):
        super().__init__(app)
        self.state = state

    async def dispatch(self, request, call_next):
        state = self.state
        if state.mode == 'desktop' and request.client and request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
            return JSONResponse({'detail': 'local connections only'}, status_code=403)
        path = request.url.path
        if path != '/health' and (path.startswith('/api/') or path == '/api'):
            if request.method not in {'GET', 'HEAD'} and not same_origin(request):
                return JSONResponse({'detail': '请求来源与服务不一致'}, status_code=403)
            if path != '/api/session' and not authenticated(request, state):
                return JSONResponse({'detail': '请先连接资料服务'}, status_code=401 if state.mode == 'server' else 403)
            administrative = path in {'/api/service/exit', '/api/collation/shutdown',
                                      '/api/knowledge/document-imports/preview', '/api/knowledge/core/import'}
            administrative = administrative or path == '/api/knowledge/activation' and request.method != 'GET'
            if state.mode == 'server' and administrative:
                return JSONResponse({'detail': '此操作由服务端管理，请使用文件上传入口导入资料'}, status_code=403)
        response = await call_next(request)
        if path == '/api/session':
            response.headers['Cache-Control'] = 'no-store'
        return response


def register(app, state):
    app.add_middleware(ServiceAuthentication, state=state)

    @app.get('/api/session')
    def session(request: Request):
        return {'authenticated': authenticated(request, state), 'mode': state.mode}

    @app.post('/api/session')
    async def login(request: Request):
        # Parse explicitly so validation errors never echo a submitted credential.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                return JSONResponse({'detail': '连接凭据格式无效'}, status_code=400)
        try:
            import json
            token = json.loads(body).get('token', '')
        except (ValueError, AttributeError):
            token = ''
        if not isinstance(token, str) or not state.token or not secrets.compare_digest(token.encode(), state.token.encode()):
            return JSONResponse({'detail': '连接凭据不正确'}, status_code=401)
        value = f'{int(time.time())}.{secrets.token_hex(16)}'
        response = JSONResponse({'authenticated': True, 'mode': state.mode})
        response.set_cookie(COOKIE, value + '.' + signature(state, value), max_age=SESSION_SECONDS,
                            httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/api')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.delete('/api/session')
    def logout():
        response = JSONResponse({'authenticated': False})
        response.delete_cookie(COOKIE, path='/api')
        return response
