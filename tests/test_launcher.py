"""Safe reuse, authentication bootstrap and cached builds for one-command startup."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from backend import launcher
from backend.portal_accounts import Accounts, AccountError, password_matches, DEFAULT_ADMIN_PASSWORD

PASSWORD = 'launcher-fixture-password-123'


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name).resolve()
        self.environment=patch.dict(os.environ,{},clear=False);self.environment.start()
        for key in ('SHIXUE_LAUNCHER_CONFIG','SHIXUE_PORTAL_ADMIN_PASSWORD','SHIXUE_PORTAL_PG_ADMIN_URL','SHIXUE_PG_RUNTIME'):
            os.environ.pop(key,None)
    def tearDown(self):
        self.environment.stop();self.folder.cleanup()
    def args(self,**values):
        return argparse.Namespace(data_dir=values.get('data_dir'),host=values.get('host'),port=values.get('port'))

    def test_reuses_existing_portal_and_remembers_explicit_choice(self):
        existing=self.root/'.local/portal-data'
        Accounts(existing/'private')
        self.assertEqual(launcher.settings(self.args(),self.root)[0],existing)
        chosen=self.root/'chosen';Accounts(chosen/'private')
        root,host,port,config=launcher.settings(self.args(data_dir=chosen,port=9876),self.root)
        launcher.save_private_json(config,{'data_dir':str(root),'host':host,'port':port})
        self.assertEqual(launcher.settings(self.args(),self.root)[:3],(chosen,'127.0.0.1',9876))

    def test_missing_saved_directory_never_becomes_a_new_empty_library(self):
        config=self.root/'.local/backend-launcher.json'
        launcher.save_private_json(config,{'data_dir':str(self.root/'missing')})
        with self.assertRaisesRegex(RuntimeError,'未建立空库'):launcher.settings(self.args(),self.root)
        self.assertFalse((self.root/'missing').exists())

    def test_multiple_portals_and_desktop_directory_require_explicit_selection(self):
        for name in ('portal-preview','portal-data'):Accounts(self.root/'.local'/name/'private')
        with self.assertRaisesRegex(RuntimeError,'多份'):launcher.settings(self.args(),self.root)
        desktop=self.root/'desktop';desktop.mkdir();(desktop/'library.sqlite').touch()
        with self.assertRaisesRegex(RuntimeError,'桌面资料'):launcher.settings(self.args(data_dir=desktop),self.root)
        with self.assertRaisesRegex(RuntimeError,'端口'):launcher.settings(self.args(data_dir=self.root/'new',port=0),self.root)

    def test_admin_created_once_without_storing_password_or_resetting_existing_account(self):
        accounts=Accounts(self.root/'private')
        with patch.dict(os.environ,{'SHIXUE_PORTAL_ADMIN_PASSWORD':PASSWORD}),patch('builtins.print') as output:
            self.assertTrue(launcher.ensure_admin(accounts))
        user=accounts.get(accounts.list()[0]['id'])
        self.assertTrue(password_matches(PASSWORD,user['password_hash']))
        self.assertNotIn(PASSWORD,str(output.call_args_list))
        with patch.dict(os.environ,{'SHIXUE_PORTAL_ADMIN_PASSWORD':'different-fixture-password'}),patch('getpass.getpass',side_effect=AssertionError('must not prompt')):
            self.assertFalse(launcher.ensure_admin(accounts))
        self.assertEqual(accounts.get(user['id'])['password_hash'],user['password_hash'])
        with accounts.connection() as db:db.execute('UPDATE portal_users SET enabled=0')
        with self.assertRaisesRegex(RuntimeError,'停用'):launcher.ensure_admin(accounts)

    def test_default_admin_starts_without_prompt_and_password_change_survives_restart(self):
        accounts=Accounts(self.root/'private')
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass',side_effect=AssertionError('must not prompt')),patch('builtins.print'):
            self.assertTrue(launcher.ensure_admin(accounts))
        token,user=accounts.authenticate('admin',DEFAULT_ADMIN_PASSWORD)
        self.assertFalse(user['password_change_required'])
        accounts.reset_password(user['id'],PASSWORD,user['id'],current_password=DEFAULT_ADMIN_PASSWORD)
        self.assertIsNone(accounts.session(token))
        self.assertFalse(launcher.ensure_admin(accounts))
        self.assertTrue(password_matches(PASSWORD,accounts.get(user['id'])['password_hash']))
        self.assertFalse(password_matches(DEFAULT_ADMIN_PASSWORD,accounts.get(user['id'])['password_hash']))
        for role in ('user','admin'):
            with self.assertRaises(AccountError):accounts.create('another','其他用户',DEFAULT_ADMIN_PASSWORD,role)
        with self.assertRaises(AccountError):accounts.reset_password(user['id'],DEFAULT_ADMIN_PASSWORD,user['id'],current_password=PASSWORD)

    def test_page_build_is_cached_and_rebuilds_for_changed_or_removed_source(self):
        for name in ('package.json','package-lock.json','index.html','vite.config.ts','tsconfig.json'):(self.root/name).write_text('{}')
        (self.root/'src').mkdir();source=self.root/'src/app.ts';source.write_text('first')
        vite=self.root/'node_modules/vite/bin/vite.js';vite.parent.mkdir(parents=True);vite.touch()
        calls=[]
        def build(args):
            calls.append(args);(self.root/'dist').mkdir(exist_ok=True);(self.root/'dist/index.html').write_text('built')
        with patch('backend.launcher.npm_command',return_value=['npm']),patch('builtins.print'):
            self.assertTrue(launcher.ensure_frontend(self.root,build))
            self.assertFalse(launcher.ensure_frontend(self.root,build))
            source.write_text('changed');self.assertTrue(launcher.ensure_frontend(self.root,build))
            source.unlink();self.assertTrue(launcher.ensure_frontend(self.root,build))
            (self.root/'dist/index.html').unlink();self.assertTrue(launcher.ensure_frontend(self.root,build))
        self.assertEqual(len(calls),4)

    def test_external_runtime_and_missing_override_are_not_silently_replaced(self):
        with patch.dict(os.environ,{'SHIXUE_PORTAL_PG_ADMIN_URL':'postgresql://fixture'}):
            launcher.ensure_runtime(self.root,lambda _:self.fail('external DB must not download runtime'))
        with patch.dict(os.environ,{'SHIXUE_PG_RUNTIME':str(self.root/'missing-runtime')}):
            with self.assertRaisesRegex(RuntimeError,'指定'):launcher.ensure_runtime(self.root)

    def test_build_helpers_do_not_inherit_service_secrets(self):
        with patch.dict(os.environ,{'SHIXUE_MODEL_API_KEY':'fixture-key','SHIXUE_PORTAL_ADMIN_PASSWORD':PASSWORD,'PGPASSWORD':'fixture-password'}),patch('subprocess.run') as run:
            launcher.command(['node','fixture.js'],self.root)
        environment=run.call_args.kwargs['env']
        self.assertNotIn('SHIXUE_MODEL_API_KEY',environment)
        self.assertNotIn('SHIXUE_PORTAL_ADMIN_PASSWORD',environment)
        self.assertNotIn('PGPASSWORD',environment)

    @unittest.skipUnless(os.name=='nt','Windows 内置运行时准备')
    def test_missing_native_runtime_runs_both_preparation_steps(self):
        commands=[]
        def prepare(args):
            commands.append(Path(args[-1]).name)
            if commands[-1]=='prepare_managed_runtime.py':
                target=self.root/'build/postgres/runtime-manifest.json';target.parent.mkdir(parents=True);target.write_text('{}')
        with patch('builtins.print'):launcher.ensure_runtime(self.root,prepare)
        self.assertEqual(commands,['fetch_managed_build_inputs.py','prepare_managed_runtime.py'])


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class LauncherNativeTests(unittest.TestCase):
    def test_one_command_boots_stops_and_reuses_accounts_and_projects(self):
        import httpx
        from backend.portal_fleet import WorkspaceFleet
        parent=launcher.PROJECT/'.local/launcher-native';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            directory=Path(folder);data=directory/'data';process=None
            environment={key:value for key,value in os.environ.items() if not key.startswith('SHIXUE_PORTAL_') and key not in {'SHIXUE_KB_DATABASE_URL','SHIXUE_KB_MIGRATION_URL','SHIXUE_KB_RESTORE_URL'}}
            environment.update(SHIXUE_LAUNCHER_CONFIG=str(directory/'settings.json'),SHIXUE_MODEL_API_KEY='',HF_HUB_OFFLINE='1')
            npm=launcher.npm_command(launcher.PROJECT)
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            def wait_ready():
                deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    self.assertIsNone(process.poll(),'launcher exited; see '+str(directory/'startup.log'))
                    try:
                        result=httpx.get(f'http://127.0.0.1:{port}/health',timeout=2)
                        if result.is_success:return
                    except httpx.HTTPError:pass
                    time.sleep(.15)
                self.fail('launcher startup timed out')
            def stop():
                result=subprocess.run(npm+['stop'],cwd=launcher.PROJECT,env=environment,capture_output=True,timeout=120)
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
                self.assertEqual(process.wait(timeout=20),0)
            with (directory/'startup.log').open('w',encoding='utf-8') as log:
                try:
                    process=subprocess.Popen(npm+['start','--','--data-dir',str(data),'--port',str(port),'--no-open'],cwd=launcher.PROJECT,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                    wait_ready()
                    with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=120) as client:
                        login=client.post('/api/session',json={'username':'admin','password':DEFAULT_ADMIN_PASSWORD})
                        self.assertEqual(login.status_code,200,login.text)
                        self.assertFalse(login.json()['user']['password_change_required'])
                        changed=client.post('/api/me/password',json={'current_password':DEFAULT_ADMIN_PASSWORD,'password':PASSWORD})
                        self.assertEqual(changed.status_code,200,changed.text)
                        self.assertEqual(client.get('/api/admin/users').status_code,401)
                        self.assertEqual(client.post('/api/session',json={'username':'admin','password':PASSWORD}).status_code,200)
                        project=client.post('/api/projects',json={'name':'一键启动保留的项目'})
                        self.assertEqual(project.status_code,200,project.text)
                    accounts=Accounts(data/'private');old_hash=accounts.get(accounts.list()[0]['id'])['password_hash']
                    duplicate=subprocess.run(npm+['start','--','--no-open'],cwd=launcher.PROJECT,env=environment,capture_output=True,timeout=20)
                    self.assertEqual(duplicate.returncode,0)
                    self.assertIn('已在运行',duplicate.stdout.decode('utf-8',errors='replace'))
                    stop()
                    self.assertFalse((data/'shared-library/knowledge-db/postmaster.pid').exists())
                    built=(launcher.PROJECT/'dist/.wenjin-build.json').stat().st_mtime_ns
                    environment['SHIXUE_PORTAL_ADMIN_PASSWORD']='unused-new-fixture-password'
                    process=subprocess.Popen(npm+['start','--','--no-open'],cwd=launcher.PROJECT,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
                    wait_ready()
                    with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=120) as client:
                        self.assertEqual(client.post('/api/session',json={'username':'admin','password':PASSWORD}).status_code,200)
                        listing=client.get('/api/projects');self.assertEqual(listing.status_code,200,listing.text)
                        self.assertIn('一键启动保留的项目',listing.text)
                    self.assertEqual(Accounts(data/'private').get(accounts.list()[0]['id'])['password_hash'],old_hash)
                    self.assertEqual((launcher.PROJECT/'dist/.wenjin-build.json').stat().st_mtime_ns,built)
                    stop()
                    self.assertNotIn(PASSWORD,(directory/'settings.json').read_text(encoding='utf-8'))
                finally:
                    if process and process.poll() is None:
                        subprocess.run([sys.executable,'-m','backend.launcher','--stop'],cwd=launcher.PROJECT,env=environment,capture_output=True,timeout=120)
                        if process.poll() is None:process.terminate();process.wait(timeout=20)
                    if (data/'private/accounts.sqlite').exists():
                        accounts=Accounts(data/'private');fleet=WorkspaceFleet(data)
                        fleet.request_stop_all([accounts.get(user['id']) for user in accounts.list()])
