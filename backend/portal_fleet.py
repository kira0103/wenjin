"""Private routing from an authenticated account to its isolated workspace host."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time

import httpx

from .knowledge.managed import DirectoryLease
from .portal_database import save_private_json
from .portal_shared_database import provision_private


@dataclass(frozen=True)
class WorkspaceTarget:
    url: str
    token: str


class WorkspaceFleet:
    def __init__(self, root, timeout=120):
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.lock = threading.Lock()
        self.locks = {}
        self.children = {}
        self.models = self.root / 'private' / 'models.json'
        identity = self.root / 'private' / 'library.json'
        identity.parent.mkdir(parents=True, exist_ok=True)
        identity_lock = DirectoryLease(identity.with_suffix('.lock'))
        identity_lock.acquire()
        try:
            if not identity.exists():
                save_private_json(identity, {'workspace_id': secrets.token_hex(16)})
            self.library = json.loads(identity.read_text(encoding='utf-8'))
        finally:
            identity_lock.release()

    def ensure_library(self):
        return self._ensure(self.library, library=True)

    def library_usage(self, identifier):
        import psycopg
        from psycopg import sql
        from .portal_database import native_url
        configuration = json.loads((self.runtime(self.library) / 'shared-database.json').read_text(encoding='utf-8'))
        anchors = projects = 0
        with psycopg.connect(native_url(configuration['owner_url'])) as db:
            schemas = ['public'] + [row[0] for row in db.execute("SELECT nspname FROM pg_namespace WHERE nspname ~ '^w_[0-9a-f]{32}$'")]
            for schema in schemas:
                anchors += db.execute(sql.SQL('SELECT count(*) FROM {}.kb_anchors WHERE edition_id=%s').format(sql.Identifier(schema)), (identifier,)).fetchone()[0]
                projects += db.execute(sql.SQL('SELECT count(*) FROM (SELECT project_id FROM {}.kb_project_sources WHERE edition_id=%s AND state IN (\'active\',\'archived\') UNION SELECT project_id FROM {}.kb_project_editions WHERE edition_id=%s) p').format(sql.Identifier(schema), sql.Identifier(schema)), (identifier,identifier)).fetchone()[0]
        return {'anchors':anchors, 'project_count':projects, 'projects':[]}

    def ensure(self, user):
        self.ensure_library()
        runtime = self.runtime(user)
        # Existing standalone account data is never silently exposed to other users.
        workspace = self.root / 'workspaces' / user['workspace_id']
        configuration = runtime / 'database.json'
        if workspace.joinpath('library.sqlite').exists() and not configuration.exists():
            raise RuntimeError('此账号保留旧版独立资料库，请先按迁移说明处理，原数据未修改')
        with self.lock:
            lock = self.locks.setdefault('schema-' + user['workspace_id'], threading.Lock())
        with lock:
            library = json.loads((self.runtime(self.library) / 'shared-database.json').read_text(encoding='utf-8'))
            try:
                provisioned=provision_private(library, user['workspace_id'], configuration)
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError('个人研究分区初始化失败，请联系管理员检查数据库权限') from None
            descriptor=runtime/'host.json'
            if descriptor.exists():
                host=json.loads(descriptor.read_text(encoding='utf-8'))
                fingerprint=hashlib.sha256(provisioned['runtime_url'].encode()).hexdigest()
                if host.get('state')=='ready' and host.get('database_key')!=fingerprint:
                    self.stop(user)
        return self._ensure(user)

    def runtime(self, user):
        identifier = user['workspace_id']
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise RuntimeError('账号工作区身份无效')
        return self.root / 'private' / 'runtimes' / identifier

    def state(self, user):
        runtime = self.runtime(user)
        try:
            value = json.loads((runtime / 'host.json').read_text(encoding='utf-8'))
            if (runtime / 'host.stop').exists():
                value['state'] = 'stopping'
            elif value.get('state') in {'ready', 'starting'} and time.time() - value.get('updated_at', 0) > 20:
                value['state'] = 'offline'
            return {key: value[key] for key in ('state', 'metrics', 'updated_at', 'message') if key in value}
        except (OSError, ValueError, TypeError):
            return {'state': 'not_started', 'metrics': {}}

    def _target(self, runtime):
        try:
            value = json.loads((runtime / 'host.json').read_text(encoding='utf-8'))
            access = json.loads((runtime / 'access.json').read_text(encoding='utf-8'))
            if value.get('state') != 'ready' or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', value.get('url', '')):
                return None
            return WorkspaceTarget(value['url'], access['token'])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _ensure(self, user, library=False):
        runtime = self.runtime(user)
        with self.lock:
            lock = self.locks.setdefault(user['workspace_id'], threading.Lock())
        with lock:
            if (runtime / 'host.stop').exists():
                probe = DirectoryLease(runtime / 'host.lock')
                try:
                    probe.acquire()
                except RuntimeError:
                    raise RuntimeError('工作区正在停止，请稍后重新连接') from None
                else:
                    probe.release()
                    (runtime / 'host.stop').unlink(missing_ok=True)
            target = self._target(runtime)
            if target:
                try:
                    with httpx.Client(timeout=3) as client:
                        response = client.get(target.url + '/api/session', headers={'X-App-Token': target.token})
                        if response.is_success and response.json().get('authenticated'):
                            return target
                except (httpx.HTTPError, ValueError):
                    pass
            runtime.mkdir(parents=True, exist_ok=True)
            if not (runtime / 'access.json').exists():
                save_private_json(runtime / 'access.json', {'token': secrets.token_hex(32)})
            host_lease = DirectoryLease(runtime / 'host.lock')
            try:
                host_lease.acquire()
            except RuntimeError:
                running = True
            else:
                host_lease.release()
                running = False
            if not running:
                # Account bootstrap passwords never enter workspace process environments.
                environment = {key: value for key, value in os.environ.items() if key not in {'SHIXUE_PORTAL_ADMIN_PASSWORD', 'SHIXUE_SERVER_TOKEN'}}
                workspace = self.root / 'shared-library' if library else self.root / 'workspaces' / user['workspace_id']
                log = (runtime / 'host.log').open('a', encoding='utf-8')
                try:
                    child = subprocess.Popen([sys.executable, '-m', 'backend.portal_workspace_host',
                                              '--root', str(workspace), '--runtime', str(runtime),
                                              '--workspace', user['workspace_id'], '--models', str(self.models),
                                              '--kind', 'library' if library else 'user'],
                                             cwd=Path(__file__).resolve().parents[1], env=environment,
                                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    self.children[user['workspace_id']] = child
                finally:
                    log.close()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                target = self._target(runtime)
                if target:
                    return target
                state = self.state(user)
                child = self.children.get(user['workspace_id'])
                if state['state'] == 'failed' or child and child.poll() is not None:
                    raise RuntimeError('独立工作区启动失败，请联系管理员检查数据库配置')
                time.sleep(.1)
            raise RuntimeError('工作区仍在准备，请稍后重试')

    def stop(self, user):
        runtime = self.runtime(user)
        if not (runtime / 'host.json').exists():
            return
        probe = DirectoryLease(runtime / 'host.lock')
        try:
            probe.acquire()
        except RuntimeError:
            (runtime / 'host.stop').touch()
        else:
            probe.release()
            (runtime / 'host.stop').unlink(missing_ok=True)
            save_private_json(runtime / 'host.json', {'state':'stopped','updated_at':time.time()})

    def request_stop_all(self, users):
        for user in users:
            self.stop(user)
        # The shared database must outlive users saving their in-flight work.
        deadline = time.monotonic() + 250
        while time.monotonic() < deadline:
            if all(self.state(user).get('state') not in {'stopping','ready','starting'} for user in users):
                break
            time.sleep(.1)
        if any(self.state(user).get('state') in {'stopping','ready','starting'} for user in users):
            raise RuntimeError('个人工作区尚未全部停止，公共数据库继续保留运行，请稍后重试')
        self.stop(self.library)
        deadline = time.monotonic() + 310
        while time.monotonic() < deadline and self.state(self.library).get('state') in {'stopping','ready','starting'}:
            time.sleep(.1)
        if self.state(self.library).get('state') in {'stopping','ready','starting'}:
            raise RuntimeError('公共资料库尚在停止，请稍后重试维护操作')

    def settings(self):
        if self.models.exists():
            return json.loads(self.models.read_text(encoding='utf-8'))
        return {'base_url': '', 'model': '', 'provider_name': '管理员配置的模型', 'context_tokens': None, 'max_output_tokens': None}

    def save_settings(self, value):
        save_private_json(self.models, value)
