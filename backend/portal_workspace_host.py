"""Supervise one account's database, HTTP API, and independent execution worker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx

from .portal_database import provision, save_private_json
from .knowledge.managed import DirectoryLease


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def model_settings(application, configuration):
    value = json.loads(Path(configuration).read_text(encoding='utf-8')) if Path(configuration).exists() else {'base_url':'','model':'','provider_name':'管理员配置的模型'}
    application.save_settings(application.SettingsUpdate(**value))


def metrics(application):
    with application.connection() as db:
        result = {'projects': db.execute('SELECT count(*) FROM projects WHERE deleted=0').fetchone()[0],
                  'tasks': db.execute('SELECT count(*) FROM tasks').fetchone()[0],
                  'active_tasks': db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]}
        result['active_tasks'] += db.execute("SELECT count(*) FROM collation_batches WHERE status IN ('queued','running','pausing','stopping')").fetchone()[0]
        result['active_tasks'] += db.execute("SELECT count(*) FROM preprocess_jobs WHERE status IN ('queued','running')").fetchone()[0]
        result['active_tasks'] += db.execute("SELECT count(*) FROM collation_debug_runs WHERE status IN ('queued','running')").fetchone()[0]
    try:
        from .knowledge import repository as r
        with r.database.transaction(application.service.root) as db:
            result['documents'] = r.execute(db, 'SELECT count(*) FROM kb_editions WHERE NOT deleted').scalar_one()
            result['active_tasks'] += r.execute(db, "SELECT count(*) FROM kb_jobs WHERE state IN ('queued','running')").scalar_one()
    except Exception:
        result['documents'] = None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--kind', choices=('library','user'), default='user')
    args = parser.parse_args()
    root, runtime = args.root.resolve(), args.runtime.resolve()
    lease = DirectoryLease(runtime / 'host.lock')
    lease.acquire()
    children = {}
    owned = None
    stop = threading.Event()
    for kind in (signal.SIGINT, signal.SIGTERM):
        signal.signal(kind, lambda *_: stop.set())
    descriptor = runtime / 'host.json'
    status = {'state': 'starting', 'pid': os.getpid(), 'updated_at': time.time()}
    save_private_json(descriptor, status)
    log = (runtime / 'process.log').open('a', encoding='utf-8')
    try:
        config = json.loads((runtime / 'access.json').read_text(encoding='utf-8'))
        admin_url = os.environ.get('SHIXUE_PORTAL_PG_ADMIN_URL')
        # Workspace children never inherit the portal's account/bootstrap/provisioning credentials.
        environment = {key: value for key, value in os.environ.items() if not key.startswith('SHIXUE_PORTAL_')}
        environment.pop('SHIXUE_KB_DATABASE_URL', None)
        environment.pop('SHIXUE_KB_MIGRATION_URL', None)
        environment.pop('SHIXUE_KB_RESTORE_URL', None)
        environment['SHIXUE_SERVER_TOKEN'] = config['token']
        environment['SHIXUE_MODEL_API_KEY'] = os.environ.get('SHIXUE_MODEL_API_KEY', '')
        environment['PYTHON_KEYRING_BACKEND'] = 'keyring.backends.null.Keyring'
        for key in ('SHIXUE_PRIVATE_SCHEMA','SHIXUE_LIBRARY_ROOT','SHIXUE_LIBRARY_KEY'):
            environment.pop(key,None)
            os.environ.pop(key,None)
        if args.kind == 'user':
            shared = json.loads((runtime / 'database.json').read_text(encoding='utf-8'))
            if shared.get('mode') != 'shared' or not shared.get('ready'):
                raise RuntimeError('个人数据分区尚未准备')
            url = shared['runtime_url']
            for key, value in {'SHIXUE_KB_DATABASE_URL':url, 'SHIXUE_PRIVATE_SCHEMA':shared['schema'],
                               'SHIXUE_LIBRARY_ROOT':shared['library_root'], 'SHIXUE_LIBRARY_KEY':shared['library_key']}.items():
                os.environ[key] = value
                environment[key] = value
        elif admin_url:
            url = provision(admin_url, args.workspace, runtime / 'database.json')
            os.environ['SHIXUE_KB_DATABASE_URL'] = url
            environment['SHIXUE_KB_DATABASE_URL'] = url
        else:
            os.environ.pop('SHIXUE_KB_DATABASE_URL', None)
            from .knowledge.product_runtime import ProductDatabase
            owned = ProductDatabase(root, 'internal')
            owned.start()
            if owned.startup_error:
                raise RuntimeError('独立数据库启动失败，请由管理员检查数据库运行时')
            from .portal_shared_database import provision_library_runtime
            from .knowledge.config import register_managed
            owner_url = owned.manager.url.render_as_string(hide_password=False)
            url = provision_library_runtime(owner_url,args.workspace,runtime/'library-access.json')
            environment['SHIXUE_KB_DATABASE_URL'] = url
            register_managed(root,url)
            environment['SHIXUE_PORTAL_OWNED_DATABASE'] = '1'
        if args.kind == 'library':
            from .identity import workspace_key
            if admin_url:
                provisioned = json.loads((runtime / 'database.json').read_text(encoding='utf-8'))
                owner_url = provisioned['owner_url']
            save_private_json(runtime / 'shared-database.json', {
                'runtime_url':environment['SHIXUE_KB_DATABASE_URL'], 'owner_url':owner_url,
                'admin_url':admin_url or owner_url, 'root':str(root), 'key':workspace_key(root)})
        model_root = Path(shared['library_root']) if args.kind == 'user' else root
        for key, directory in {'SHIXUE_KB_MODEL_CACHE':'models','SHIXUE_KB_RERANK_CACHE':'rerank-models'}.items():
            value=os.environ.get(key) or str(model_root/directory)
            os.environ[key]=environment[key]=value
        from . import app as application
        from .knowledge.activation import CatalogActivation
        application.service.mode = 'workspace-host'
        application.service.embedded_workers = False
        application.initialize(root, recover=False)
        model_settings(application, args.models)
        activation = CatalogActivation(root, application.connection)
        activation.reconcile()
        if not activation.status()['active']:
            result = activation.migrate()
            if not result['active']:
                raise RuntimeError('工作区文献库尚未完成初始化')
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        worker_stop = runtime / 'worker.stop'
        worker_stop.unlink(missing_ok=True)
        def spawn(role):
            command = [sys.executable, '-m', 'backend.portal_runtime', role, '--data-dir', str(root)]
            command += ['--port', str(port)] if role == 'api' else ['--stop-file', str(worker_stop)]
            return subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], env=environment,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        children['api'] = spawn('api')
        children['worker'] = spawn('worker')
        restart_times = {'api': [], 'worker': []}
        last_models = args.models.stat().st_mtime_ns if args.models.exists() else 0
        with httpx.Client(timeout=3) as client:
            while not stop.is_set() and not (runtime / 'host.stop').exists():
                for role, child in list(children.items()):
                    if child.poll() is not None:
                        recent = [when for when in restart_times[role] if time.monotonic()-when < 60]
                        if len(recent) >= 3:
                            raise RuntimeError('工作区进程反复退出，请检查运行环境')
                        restart_times[role] = recent + [time.monotonic()]
                        children[role] = spawn(role)
                try:
                    response = client.get(base + '/health')
                    ready = response.is_success
                except httpx.HTTPError:
                    ready = False
                changed = args.models.stat().st_mtime_ns if args.models.exists() else 0
                if changed != last_models:
                    model_settings(application, args.models)
                    last_models = changed
                if args.kind == 'library':
                    # Readers never need write privileges merely to list a catalog.
                    from .knowledge.document_library import DocumentLibrary
                    from .knowledge.literature_categories import LiteratureCategories
                    DocumentLibrary(root).groups()
                    LiteratureCategories(root).tree()
                status = {'state': 'ready' if ready else 'starting', 'pid': os.getpid(), 'url': base,
                          'updated_at': time.time(), 'metrics': metrics(application),
                          'database_key':hashlib.sha256(environment['SHIXUE_KB_DATABASE_URL'].encode()).hexdigest()}
                save_private_json(descriptor, status)
                stop.wait(2)
    except Exception:
        # Driver errors can contain credentials; the public status is deliberately bounded.
        save_private_json(descriptor, {'state': 'failed', 'pid': os.getpid(), 'updated_at': time.time(),
                                       'message': '工作区启动失败，请检查数据库、运行时与服务配置'})
        raise SystemExit(1) from None
    finally:
        if 'api' in children and children['api'].poll() is None:
            children['api'].terminate()
            children['api'].wait(timeout=30)
        if 'worker' in children and children['worker'].poll() is None:
            (runtime / 'worker.stop').touch()
            try:
                children['worker'].wait(timeout=240)
            except subprocess.TimeoutExpired:
                children['worker'].kill()
                children['worker'].wait(timeout=15)
        if owned:
            owned.stop()
        log.close()
        (runtime / 'host.stop').unlink(missing_ok=True)
        if descriptor.exists():
            value = json.loads(descriptor.read_text(encoding='utf-8'))
            if value.get('state') != 'failed':
                save_private_json(descriptor, {'state': 'stopped', 'updated_at': time.time()})
        lease.release()


if __name__ == '__main__':
    main()
