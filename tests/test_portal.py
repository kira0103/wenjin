"""Account permissions and routing isolation without real research data."""
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from backend.portal import COOKIE, create_app
from backend.portal_accounts import AccountError, Accounts
from backend.portal_fleet import WorkspaceTarget

PASSWORD = 'isolated-password-12345'


class FakeFleet:
    def __init__(self):
        self.servers = {}
        self.threads = []
        self.requests = []
        self.models = {'base_url': '', 'model': '', 'provider_name': '测试模型', 'context_tokens': None, 'max_output_tokens': None}

    def state(self, user):
        return {'state': 'ready' if user['id'] in self.servers else 'not_started', 'metrics': {}}

    def settings(self):
        return self.models

    def save_settings(self, value):
        self.models = value

    def stop(self, user):
        self.requests.append(('stop', user['id']))

    def ensure_library(self):
        return self.ensure({'id':'shared-library','username':'公共资料库'})

    def ensure(self, user):
        self.requests.append(('ensure', user['id']))
        if user['id'] not in self.servers:
            projects = [{'id': 'project-main', 'name': user['username'] + ' 的研究'}]
            expected = 'private-' + user['id']
            class Workspace(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.headers.get('X-App-Token') != expected:
                        self.send_response(403); self.end_headers(); return
                    value = projects if self.path == '/api/projects' else {'content': user['username'] + ' 的私有文献'}
                    body = json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                def log_message(self, *_):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Workspace)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start(); self.servers[user['id']] = server; self.threads.append(thread)
        return WorkspaceTarget(f"http://127.0.0.1:{self.servers[user['id']].server_port}", 'private-' + user['id'])

    def close(self):
        for server in self.servers.values():
            server.shutdown(); server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.fleet = FakeFleet()
        self.app = create_app(self.root, fleet=self.fleet)
        self.accounts = self.app.state.accounts
        self.admin = self.accounts.create('admin', '管理员', PASSWORD, 'admin', require_change=False)
        self.alice = self.accounts.create('alice', '研究者甲', PASSWORD, require_change=False)
        self.bob = self.accounts.create('bobby', '研究者乙', PASSWORD, require_change=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.fleet.close()
        self.folder.cleanup()

    def login(self, username='admin', password=PASSWORD):
        response = self.client.post('/api/session', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_passwords_are_hashed_sessions_revocable_and_role_checked_on_server(self):
        self.assertEqual(self.client.get('/api/admin/users').status_code, 401)
        self.login('alice')
        self.assertEqual(self.client.get('/api/admin/users').status_code, 403)
        self.assertEqual(self.client.post('/api/admin/users', json={'username':'attacker','display_name':'越权','password':PASSWORD}).status_code, 403)
        token = self.client.cookies.get(COOKIE)
        with self.accounts.connection() as db:
            row = db.execute("SELECT password_hash FROM portal_users WHERE username='alice'").fetchone()
            self.assertNotIn(PASSWORD, row[0])
            self.assertTrue(row[0].startswith('scrypt$'))
            self.assertEqual(db.execute('SELECT count(*) FROM portal_sessions WHERE token_hash=?', (token,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM portal_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0], 1)
        self.client.delete('/api/session')
        self.assertEqual(self.client.get('/api/projects', headers={'Cookie':COOKIE+'='+token}).status_code, 401)

    def test_workspace_routing_ignores_client_identity_and_replaces_authentication(self):
        self.login('alice')
        alice = self.client.get('/api/projects', headers={'X-App-Token':'attacker','Authorization':'Bearer attacker','X-Workspace-ID':self.bob['id']})
        self.assertEqual(alice.status_code, 200, alice.text)
        self.assertEqual(alice.json()[0]['name'], 'alice 的研究')
        self.client.delete('/api/session'); self.login('bobby')
        bob = self.client.get('/api/projects')
        self.assertEqual(bob.status_code, 200, bob.text)
        self.assertEqual(bob.json()[0]['name'], 'bobby 的研究')
        self.assertNotEqual(self.accounts.get(self.alice['id'])['workspace_id'], self.accounts.get(self.bob['id'])['workspace_id'])
        self.assertEqual(self.client.get('/api/materials/alice-id').json()['content'], 'bobby 的私有文献')

    def test_users_cannot_change_model_or_database_settings_or_bypass_path_checks(self):
        self.login('alice')
        for path in ['/api/settings','/api/knowledge/settings','/api/knowledge/connection-test','/api/knowledge/exports','/api/service/exit']:
            self.assertEqual(self.client.post(path, json={}).status_code, 403, path)
        response = self.client.put('/api/anything/%2e%2e/settings', json={'base_url':'https://untrusted.invalid'})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.fleet.requests)

    def test_switching_another_tab_account_cannot_submit_the_old_accounts_draft(self):
        self.login('alice')
        self.login('bobby')
        result = self.client.get('/api/projects', headers={'X-Wenjin-Account': self.alice['id']})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.json()['code'], 'account_changed')
        self.assertEqual(self.client.post('/api/tasks', headers={'X-Wenjin-Account':self.alice['id']},json={'source_text':'旧账号私有草稿'}).status_code,401)
        self.assertFalse(self.fleet.requests)

    def test_public_library_admin_entry_and_private_mutation_guard(self):
        self.login('alice')
        for path,method in [('/api/admin/library/knowledge/catalog','get'),('/api/knowledge/catalog','post'),('/api/knowledge/%2563atalog','post'),('/api/knowledge/editions/abc/reindex','post'),('/api/knowledge/document-entries/move','post'),('/api/uploads','post')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,403,path)
        self.assertFalse(self.fleet.requests)
        self.client.delete('/api/session');self.login()
        result=self.client.get('/api/admin/library/knowledge/catalog')
        self.assertEqual(result.status_code,200,result.text)
        self.assertIn(('ensure','shared-library'),self.fleet.requests)
        self.assertNotIn(('ensure',self.admin['id']),self.fleet.requests)
        self.assertEqual(self.client.get('/api/admin/library/projects').status_code,403)

    def test_admin_create_disable_reset_and_last_administrator_protection(self):
        self.login()
        created = self.client.post('/api/admin/users', json={'username':'reader','display_name':'新用户','password':PASSWORD})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertNotIn('password_hash', created.text)
        self.assertTrue(created.json()['password_change_required'])
        last = self.client.patch('/api/admin/users/' + self.admin['id'], json={'display_name':'管理员','role':'user','enabled':True})
        self.assertEqual(last.status_code, 409)
        alice_token, _ = self.accounts.authenticate('alice', PASSWORD)
        disabled = self.client.patch('/api/admin/users/' + self.alice['id'], json={'display_name':'甲','role':'user','enabled':False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertIsNone(self.accounts.session(alice_token))
        stop = self.client.post('/api/admin/users/' + self.alice['id'] + '/stop-workspace')
        self.assertEqual(stop.status_code, 200)
        self.assertEqual(self.client.post('/api/admin/users/' + self.bob['id'] + '/stop-workspace').status_code, 409)
        reset = self.client.post('/api/admin/users/' + self.bob['id'] + '/password', json={'password':'new-isolated-password-123'})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.client.post('/api/admin/users/' + self.admin['id'] + '/password', json={'password':PASSWORD}).status_code, 409)
        events = self.client.get('/api/admin/audit').json()
        self.assertTrue(any(event['action']=='user.password.reset' for event in events))
        self.assertNotIn('new-isolated-password-123', json.dumps(events))

    def test_temporary_password_must_be_changed_and_old_sessions_are_invalidated(self):
        user = self.accounts.create('reader', '临时账号', PASSWORD)
        self.login('reader')
        token = self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.get('/api/projects').status_code, 403)
        changed = self.client.post('/api/me/password', json={'current_password':PASSWORD,'password':'replacement-password-123'})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertIsNone(self.accounts.session(token))
        self.assertFalse(self.accounts.get(user['id'])['password_change_required'])
        self.login('reader', 'replacement-password-123')
        self.assertEqual(self.client.get('/api/projects').status_code, 200)

    def test_cross_origin_login_and_credential_echo_are_rejected(self):
        self.assertEqual(self.client.post('/api/session', json={'username':'admin','password':PASSWORD}, headers={'Origin':'https://untrusted.invalid'}).status_code, 403)
        self.login()
        invalid = self.client.post('/api/admin/users', json={'username':'bad','display_name':{'password':PASSWORD},'password':PASSWORD})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn(PASSWORD, invalid.text)

    def test_login_failures_are_rate_limited(self):
        for _ in range(10):
            with self.assertRaises(AccountError):self.accounts.authenticate('unknown', PASSWORD, '203.0.113.1')
        with self.assertRaisesRegex(AccountError, '尝试次数过多'):
            self.accounts.authenticate('unknown', PASSWORD, '203.0.113.1')

    def test_newer_account_schema_is_rejected_without_modifying_existing_data(self):
        with self.accounts.connection() as db:
            db.execute('INSERT INTO portal_schema(version) VALUES(2)')
        with self.assertRaisesRegex(RuntimeError, '数据库结构比当前程序更新'):
            Accounts(self.root/'private')
        with self.accounts.connection() as db:
            self.assertEqual(db.execute('SELECT max(version) FROM portal_schema').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM portal_users').fetchone()[0],3)
