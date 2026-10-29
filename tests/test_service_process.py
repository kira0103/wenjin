"""Real HTTP/API/worker processes with an isolated deterministic model server."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


class ServiceProcessTests(unittest.TestCase):
    def test_worker_finishes_once_when_client_disconnects_and_api_restarts(self):
        received, release = threading.Event(), threading.Event()
        calls = []

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                received.set()
                release.wait(35)
                response = json.dumps({'choices': [{'message': {'content': '跨进程持久化结果'}, 'finish_reason': 'stop'}],
                                       'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_):
                pass

        model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        processes = []
        with tempfile.TemporaryDirectory(prefix='wenjin-process-') as folder:
            root = Path(folder) / 'data'
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            token = secrets.token_hex(32)
            environment = {**os.environ, 'SHIXUE_SERVER_TOKEN': token, 'PYTHON_KEYRING_BACKEND': 'keyring.backends.null.Keyring', 'HF_HUB_OFFLINE': '1'}
            for name in ('SHIXUE_KB_DATABASE_URL', 'SHIXUE_MODEL_API_KEY'):
                environment.pop(name, None)
            with (Path(folder) / 'process.log').open('w', encoding='utf-8') as log:
                def spawn(module, *args):
                    process = subprocess.Popen([sys.executable, '-m', module, '--data-dir', str(root), *args],
                                               cwd=Path(__file__).resolve().parents[1], env=environment,
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    processes.append(process)
                    return process

                def until(check, label, timeout=25):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        try:
                            value = check()
                            if value:
                                return value
                        except httpx.HTTPError:
                            pass
                        time.sleep(.05)
                    self.fail(label)

                address = f'http://127.0.0.1:{port}'
                try:
                    api = spawn('backend.server', '--port', str(port))
                    with httpx.Client(base_url=address, headers={'X-App-Token': token}, timeout=3) as client:
                        until(lambda: client.get('/health').is_success, 'API startup')
                        response = client.put('/api/settings', json={'base_url': f'http://127.0.0.1:{model.server_port}/v1', 'model': 'process-fixture'})
                        self.assertEqual(response.status_code, 200, response.text)
                        task = client.post('/api/tasks', json={'task_type': 'summary', 'source_text': '只应执行一次的原文'}).json()
                        self.assertEqual(task['status'], 'queued')
                        spawn('backend.worker')
                        self.assertTrue(received.wait(25), 'Worker did not claim the durable task')
                        self.assertEqual(client.get('/api/tasks/' + task['id']).json()['status'], 'running')
                    # Closing the HTTP client does not signal task cancellation.
                    api.terminate()
                    api.wait(timeout=10)
                    spawn('backend.server', '--port', str(port))
                    with httpx.Client(base_url=address, headers={'X-App-Token': token}, timeout=3) as reconnected:
                        until(lambda: reconnected.get('/health').is_success, 'API restart')
                        self.assertEqual(reconnected.get('/api/tasks/' + task['id']).json()['status'], 'running')
                        self.assertTrue(reconnected.get('/api/service/status').json()['worker_ready'])
                        release.set()
                        result = until(lambda: (value if (value := reconnected.get('/api/tasks/' + task['id']).json())['status'] == 'completed' else None), 'Task completion')
                        self.assertEqual(result['artifact']['content'], '跨进程持久化结果')
                        self.assertEqual(len(calls), 1)
                finally:
                    release.set()
                    for process in reversed(processes):
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=15)
                    model.shutdown()
                    model.server_close()
                    thread.join(timeout=5)
