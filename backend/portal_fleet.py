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
from urllib.parse import urlsplit

import httpx

from .knowledge.managed import DirectoryLease
from .portal_database import save_private_json
from .portal_shared_database import provision_private


MODEL_POOL_VERSION = 2
# The public shape synced into a user workspace. Service URLs stay on the portal;
# every workspace only receives the models it is allowed to choose from.
MODEL_POOL_FIELDS = ('id', 'name', 'provider_name', 'base_url', 'model', 'context_tokens',
                     'max_output_tokens', 'price_input_per_million', 'price_output_per_million',
                     'price_cached_per_million', 'currency', 'price_date', 'enabled', 'selectable')
PUBLIC_MODEL_FIELDS = tuple(name for name in MODEL_POOL_FIELDS if name != 'base_url')
MODEL_MIRROR_FIELDS = ('base_url', 'model', 'provider_name', 'context_tokens', 'max_output_tokens')
EMPTY_MODEL_MIRROR = {'base_url': '', 'model': '', 'provider_name': '管理员配置的模型',
                      'context_tokens': None, 'max_output_tokens': None}


def _flag(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        return default if not text else text not in {'0', 'false', 'no', 'off'}
    return default


def _number(value, label, *, integer=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label}需要是数字')
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}需要是数字') from None
    if number < 0:
        raise ValueError(f'{label}不能为负数')
    return number


def _clean_url(value):
    text = str(value or '').strip().rstrip('/')
    if not text:
        return ''
    parsed = urlsplit(text)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('请填写不含凭据的 HTTP 或 HTTPS 模型服务地址')
    return text


def _valid_model_id(value):
    # `default` is the fixed identifier of a legacy single-model configuration.
    return value == 'default' or bool(re.fullmatch(r'm_[0-9a-f]{8}', value))


def _new_model_id(used):
    while True:
        candidate = 'm_' + secrets.token_hex(4)
        if candidate not in used:
            return candidate


def _normalize_model(value, used, *, strict, legacy=False):
    source = value if isinstance(value, dict) else {}
    url = str(source.get('base_url') or '').strip().rstrip('/')
    if url:
        try:
            url = _clean_url(url)
        except ValueError:
            if strict:
                raise
            url = ''
    model_name = str(source.get('model') or '').strip()[:200]
    if strict and not url:
        raise ValueError('请填写模型服务地址')
    if strict and not model_name:
        raise ValueError('请填写模型名称')
    identifier = str(source.get('id') or '').strip()
    if not _valid_model_id(identifier) or identifier in used:
        identifier = _new_model_id(used)
    used.add(identifier)
    provider = str(source.get('provider_name') or '').strip()[:100]
    name = str(source.get('name') or '').strip()[:100]
    if not name:
        name = provider or model_name or '管理员配置的模型'
    if not provider and legacy:
        provider = name
    try:
        context_tokens = _number(source.get('context_tokens'), '上下文长度', integer=True)
        max_output_tokens = _number(source.get('max_output_tokens'), '输出长度', integer=True)
        price_input = _number(source.get('price_input_per_million'), '输入单价')
        price_output = _number(source.get('price_output_per_million'), '输出单价')
        price_cached = _number(source.get('price_cached_per_million'), '缓存单价')
    except ValueError:
        if strict:
            raise
        context_tokens = max_output_tokens = None
        price_input = price_output = price_cached = None
    if legacy:
        enabled = selectable = True
    else:
        enabled = _flag(source.get('enabled'), True)
        selectable = _flag(source.get('selectable'), True)
    return {
        'id': identifier, 'name': name, 'provider_name': provider, 'base_url': url,
        'model': model_name, 'context_tokens': context_tokens,
        'max_output_tokens': max_output_tokens,
        'price_input_per_million': price_input,
        'price_output_per_million': price_output,
        'price_cached_per_million': price_cached,
        'currency': str(source.get('currency') or '').strip()[:12],
        'price_date': str(source.get('price_date') or '').strip()[:10],
        'enabled': enabled, 'selectable': selectable,
    }


def normalize_model_pool(value, *, strict=False):
    """Return the canonical v2 model pool.

    Old single-model files (only top-level base_url/model/...) become one model with
    the fixed identifier ``default``. Saving validates the invariant that the default
    model exists and is enabled; reading repairs legacy/hand-edited files instead of
    failing so a broken private file cannot take the whole portal down.
    """
    source = value if isinstance(value, dict) else {}
    used, models = set(), []
    if 'models' in source or 'default_model_id' in source:
        candidates = source.get('models')
        if not isinstance(candidates, list):
            candidates = []
        if strict and not candidates:
            raise ValueError('请至少配置一个模型')
        for item in candidates:
            if not isinstance(item, dict):
                if strict:
                    raise ValueError('模型列表格式无效')
                continue
            models.append(_normalize_model(item, used, strict=strict))
    else:
        legacy_value = {
            'id': 'default', 'name': '', 'provider_name': source.get('provider_name', ''),
            'base_url': source.get('base_url', ''), 'model': source.get('model', ''),
            'context_tokens': source.get('context_tokens'),
            'max_output_tokens': source.get('max_output_tokens'),
        }
        if strict:
            models.append(_normalize_model(legacy_value, used, strict=True, legacy=True))
        elif legacy_value['base_url'] or legacy_value['model']:
            models.append(_normalize_model(legacy_value, used, strict=False, legacy=True))
    default_id = str(source.get('default_model_id') or '').strip()
    default = next((item for item in models if item['id'] == default_id), None)
    if strict:
        if not models:
            raise ValueError('请至少配置一个模型')
        if default_id and default is None:
            raise ValueError('默认模型不在模型列表中')
        if default is not None and not default['enabled']:
            raise ValueError('默认模型必须处于启用状态')
        if default is None:
            default = next((item for item in models if item['enabled']), None)
            if default is None:
                raise ValueError('请至少启用一个模型作为默认模型')
    elif default is None or not default['enabled']:
        default = next((item for item in models if item['enabled']), None)
        if default is None:
            default = models[0] if models else None
    result = {'version': MODEL_POOL_VERSION,
              'default_model_id': default['id'] if default else '',
              'models': models}
    for field in MODEL_MIRROR_FIELDS:
        result[field] = default[field] if default else EMPTY_MODEL_MIRROR[field]
    return result


def public_model_pool(value):
    pool = normalize_model_pool(value)
    return {'default_model_id': pool['default_model_id'],
            'models': [{field: item[field] for field in PUBLIC_MODEL_FIELDS} for item in pool['models']]}


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
        self.ready_checks = {}
        self.provisioned = {}
        self.probes = httpx.Client(timeout=3, trust_env=False)
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
            if not db.execute('SELECT 1 FROM public.kb_editions WHERE id=%s AND owner_role IS NULL',(identifier,)).fetchone():
                from fastapi import HTTPException
                raise HTTPException(404,'找不到系统文献')
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
            shared_path = self.runtime(self.library) / 'shared-database.json'
            library = json.loads(shared_path.read_text(encoding='utf-8'))
            signature = (library['runtime_url'], library['key'])
            # Provision once per database identity, rather than rewriting credentials on every HTTP request.
            if self.provisioned.get(user['workspace_id']) == signature:
                return self._ensure(user)
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
            self.provisioned[user['workspace_id']] = signature
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
                if time.monotonic() - self.ready_checks.get(user['workspace_id'], 0) < 2:
                    return target
                try:
                    response = self.probes.get(target.url + '/api/session', headers={'X-App-Token': target.token})
                    if response.is_success and response.json().get('authenticated'):
                        self.ready_checks[user['workspace_id']] = time.monotonic()
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
                running = False
            if not running:
                try:
                    # Reset the previous attempt while holding its lease. The new
                    # child cannot publish readiness until the lease is released.
                    save_private_json(runtime/'host.json',{'state':'starting','updated_at':time.time()})
                    # Account bootstrap passwords never enter workspace process environments.
                    environment = {key: value for key, value in os.environ.items() if key not in {'SHIXUE_PORTAL_ADMIN_PASSWORD', 'SHIXUE_SERVER_TOKEN'}}
                    workspace = self.root / 'shared-library' if library else self.root / 'workspaces' / user['workspace_id']
                    with (runtime / 'host.log').open('a', encoding='utf-8') as log:
                        child = subprocess.Popen([sys.executable, '-m', 'backend.portal_workspace_host',
                                                  '--root', str(workspace), '--runtime', str(runtime),
                                                  '--workspace', user['workspace_id'], '--models', str(self.models),
                                                  '--kind', 'library' if library else 'user'],
                                                 cwd=Path(__file__).resolve().parents[1], env=environment,
                                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        self.children[user['workspace_id']] = child
                finally:
                    host_lease.release()
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
        self.ready_checks.pop(user['workspace_id'], None)
        self.provisioned.pop(user['workspace_id'], None)
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
        if not self.models.exists():
            return normalize_model_pool({})
        try:
            raw = json.loads(self.models.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return normalize_model_pool({})
        value = normalize_model_pool(raw)
        if value != raw:
            # Reading a legacy/hand-edited file repairs it in place (contract: v2 write-back).
            try:
                save_private_json(self.models, value)
            except OSError:
                pass
        return value

    def close(self):
        self.probes.close()

    def save_settings(self, value):
        normalized = normalize_model_pool(value, strict=True)
        save_private_json(self.models, normalized)
        return normalized
