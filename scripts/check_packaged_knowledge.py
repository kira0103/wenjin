"""Smoke-test the built service against development PostgreSQL using isolated data."""
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy import URL


def main():
    project = Path(__file__).resolve().parents[1]
    binary = project / 'build' / 'backend' / 'ShixueWorkbenchService.exe'
    password = (project / '.local' / 'knowledge' / 'app_password').read_text().strip()
    url = URL.create('postgresql+psycopg', username='shixue_app', password=password,
                     host='127.0.0.1', port=55432, database='shixue_knowledge')
    environment = os.environ.copy()
    environment['SHIXUE_KB_DATABASE_URL'] = url.render_as_string(hide_password=False)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix='shixue-packaged-check-') as directory:
        process = subprocess.Popen([str(binary), '--port', str(port), '--data-dir', directory, '--token', token],
                                   env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        address = f'http://127.0.0.1:{port}'
        try:
            with httpx.Client(base_url=address, headers={'X-App-Token': token}, timeout=8) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('打包服务启动失败')
                    try:
                        if client.get('/health').is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.25)
                else:
                    raise RuntimeError('打包服务启动超时')
                health = client.get('/api/knowledge/health').json()
                assert health.get('ready'), '打包服务知识库未就绪'
                assert client.get('/api/bootstrap').is_success, '旧 SQLite 接口不可用'
                assert client.get('/api/knowledge/health', headers={'X-App-Token': 'wrong'}).status_code == 403
                response = client.get('/api/knowledge/settings')
                assert password not in response.text
                assert client.post('/api/service/exit').is_success
                process.wait(timeout=10)
                print('Packaged service: PostgreSQL connection, schema, legacy API and access checks passed')
        finally:
            if process.poll() is None:
                try:
                    httpx.post(address + '/api/service/exit', headers={'X-App-Token': token}, timeout=3)
                    process.wait(timeout=10)
                except Exception:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
