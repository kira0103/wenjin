"""One-command local portal: npm start / npm stop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from .portal_accounts import Accounts, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD
from .portal_database import save_private_json

PROJECT = Path(__file__).resolve().parents[1]


def settings(args, project=PROJECT):
    configuration = Path(os.environ.get('SHIXUE_LAUNCHER_CONFIG') or project / '.local' / 'backend-launcher.json').resolve()
    try:
        saved = json.loads(configuration.read_text(encoding='utf-8')) if configuration.exists() else {}
        if not isinstance(saved, dict):raise ValueError()
    except (ValueError, OSError):
        if not args.data_dir:raise RuntimeError('启动配置无法读取，请用 --data-dir 指定原有门户资料目录') from None
        saved = {}
    if args.data_dir:
        root = Path(args.data_dir).expanduser()
        root = root if root.is_absolute() else project / root
    elif saved.get('data_dir'):
        if not isinstance(saved['data_dir'], str):raise RuntimeError('启动配置中的资料路径无效')
        root = Path(saved['data_dir'])
        if not root.is_absolute() or not (root / 'private' / 'accounts.sqlite').is_file():
            raise RuntimeError('记住的门户资料目录不可用，未建立空库；请恢复原目录或用 --data-dir 明确指定')
    else:
        existing = [project / '.local' / name for name in ('portal-preview', 'portal-data')
                    if (project / '.local' / name / 'private' / 'accounts.sqlite').is_file()]
        if len(existing) > 1:
            raise RuntimeError('发现多份门户资料，首次请用 --data-dir 选择一份，之后会自动记住')
        root = existing[0] if existing else project / '.local' / 'portal-preview'
    if (root / 'library.sqlite').is_file():raise RuntimeError('这是桌面资料目录，请选择门户资料目录；原数据未修改')
    if not (root / 'private' / 'accounts.sqlite').is_file() and any((root / name).exists() for name in ('workspaces','shared-library')):
        raise RuntimeError('门户账号库缺失，请先恢复原账号库；未重新初始化资料')
    host = args.host if args.host is not None else saved.get('host', '127.0.0.1')
    port = args.port if args.port is not None else saved.get('port', 8765)
    if not isinstance(host, str) or not host or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RuntimeError('启动地址或端口无效')
    return root.resolve(), host, port, configuration


def command(arguments, project=PROJECT):
    environment={key:value for key,value in os.environ.items() if not key.startswith('SHIXUE_') and key!='PGPASSWORD'}
    subprocess.run(arguments, cwd=project, check=True, env=environment,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def npm_command(project):
    cli = os.environ.get('npm_execpath')
    node = os.environ.get('npm_node_execpath') or shutil.which('node')
    if cli and node and Path(cli).is_file():return [node, cli]
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:raise RuntimeError('未找到 npm，请先安装 Node.js')
    return [npm]


def frontend_signature(project):
    files = [project / name for name in ('package.json', 'package-lock.json', 'index.html', 'vite.config.ts', 'tsconfig.json')]
    files += sorted(path for path in (project / 'src').rglob('*') if path.is_file())
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_to(project).as_posix().encode())
        digest.update(b'\0');digest.update(file.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def ensure_frontend(project=PROJECT, run=None):
    run = run or (lambda args: command(args, project))
    index, stamp = project / 'dist' / 'index.html', project / 'dist' / '.wenjin-build.json'
    signature = frontend_signature(project)
    try:
        previous = json.loads(stamp.read_text(encoding='utf-8'))
        if index.is_file() and previous == {'signature':signature, 'index':hashlib.sha256(index.read_bytes()).hexdigest()}:
            return False
    except (OSError, ValueError):pass
    npm = npm_command(project)
    if not (project / 'node_modules' / 'vite' / 'bin' / 'vite.js').is_file():
        print('首次准备页面依赖…', flush=True);run(npm + ['ci'])
    print('正在构建最新 Web 页面…', flush=True)
    run(npm + ['run', 'build:ui'])
    if not index.is_file():raise RuntimeError('页面构建未生成 dist/index.html')
    save_private_json(stamp, {'signature':signature, 'index':hashlib.sha256(index.read_bytes()).hexdigest()})
    return True


def ensure_runtime(project=PROJECT, run=None):
    if os.environ.get('SHIXUE_PORTAL_PG_ADMIN_URL'):return
    runtime = Path(os.environ.get('SHIXUE_PG_RUNTIME') or project / 'build' / 'postgres')
    if (runtime / 'runtime-manifest.json').is_file():return
    if os.environ.get('SHIXUE_PG_RUNTIME'):
        raise RuntimeError('SHIXUE_PG_RUNTIME 指定的数据库运行时缺失，请检查原路径')
    if os.name != 'nt':raise RuntimeError('此系统请先配置 SHIXUE_PORTAL_PG_ADMIN_URL 外部数据库连接')
    print('首次准备本机数据库运行时，需要下载和构建，请稍候…', flush=True)
    run = run or (lambda args: command(args, project))
    for script in ('fetch_managed_build_inputs.py', 'prepare_managed_runtime.py'):
        run([sys.executable, str(project / 'scripts' / script)])
    if not (runtime / 'runtime-manifest.json').is_file():raise RuntimeError('数据库运行时准备未完成')


def ensure_admin(accounts):
    administrators = [user for user in accounts.list() if user['role'] == 'admin']
    if administrators:
        if not any(user['enabled'] for user in administrators):raise RuntimeError('现有管理员均已停用，请先恢复管理员权限')
        return False
    password = os.environ.get('SHIXUE_PORTAL_ADMIN_PASSWORD', DEFAULT_ADMIN_PASSWORD)
    accounts.create(DEFAULT_ADMIN_USERNAME, '系统管理员', password, 'admin', require_change=False, bootstrap=True)
    if password == DEFAULT_ADMIN_PASSWORD:
        print('初始管理员：admin / admin123。登录管理后台后，可在“账号与安全”修改密码。', flush=True)
    else:
        print('管理员已建立，用户名：admin。使用环境中配置的初始密码。', flush=True)
    return True


def address(host, port):
    host = {'0.0.0.0':'127.0.0.1', '::':'::1'}.get(host, host)
    return f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'


def stop_portal(root):
    from .knowledge.managed import DirectoryLease
    lock, marker = root / 'private' / 'portal.lock', root / 'private' / 'launcher.stop'
    if not lock.exists():print('后台未运行。');return
    lease = DirectoryLease(lock)
    try:lease.acquire()
    except RuntimeError:
        if not (root / 'private' / 'launcher.json').is_file():
            raise RuntimeError('该门户由手动命令启动，请先在原终端停止它，再运行 npm stop') from None
        marker.touch();print('已请求停止后台，正在等待任务保存…', flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            time.sleep(.2)
            try:lease.acquire()
            except RuntimeError:continue
            else:lease.release();break
        else:raise RuntimeError('后台仍在保存任务，请稍后重试；未强制终止数据库')
    else:lease.release()
    # A closed console may leave workspace hosts alive; stop them normally too.
    if (root / 'private' / 'accounts.sqlite').is_file():
        from .portal_fleet import WorkspaceFleet
        accounts = Accounts(root / 'private')
        WorkspaceFleet(root).request_stop_all([accounts.get(user['id']) for user in accounts.list()])
    print('后台已停止。')


def start_portal(root, host, port, configuration, *, open_browser=True, project=PROJECT):
    from .knowledge.managed import DirectoryLease, _protect, _current_sid
    root.mkdir(parents=True, exist_ok=True)
    _protect(root, _current_sid() if os.name == 'nt' else '')
    lease = DirectoryLease(root / 'private' / 'portal.lock')
    try:lease.acquire()
    except RuntimeError:
        running = root / 'private' / 'launcher.json'
        if running.is_file():
            value = json.loads(running.read_text(encoding='utf-8'))
            url = address(value['host'], value['port'])
            print(f'后台已在运行，无需重复启动。管理页面：{url}/admin/')
            if open_browser:webbrowser.open(url + '/admin/')
            return
        raise RuntimeError('该资料目录已有门户运行，请使用已有服务') from None
    fleet = None
    warmer = None
    finished = threading.Event()
    stop_file, running = root / 'private' / 'launcher.stop', root / 'private' / 'launcher.json'
    try:
        with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET) as probe:
            try:probe.bind((host, port))
            except OSError:raise RuntimeError(f'端口 {port} 已被占用；已有服务请直接使用，或用 --port 指定其他端口') from None
        stop_file.unlink(missing_ok=True)
        save_private_json(running, {'host':host, 'port':port, 'pid':os.getpid()})
        ensure_frontend(project)
        ensure_runtime(project)
        accounts = Accounts(root / 'private')
        ensure_admin(accounts)
        save_private_json(configuration, {'data_dir':str(root), 'host':host, 'port':port})
        from .portal import create_app
        from .portal_fleet import WorkspaceFleet
        import uvicorn
        fleet = WorkspaceFleet(root)
        print('页面即将开放，资料服务在后台准备…', flush=True)
        def warm_workspaces():
            try:
                fleet.ensure_library()
                # Prepare the most recently active researcher while the login
                # page is loading. Never create a workspace for every account.
                recent = sorted((user for user in accounts.list() if user['enabled'] and user['role']=='user' and user['last_login_at']),key=lambda user:user['last_login_at'],reverse=True)
                if recent and not finished.is_set():
                    fleet.ensure(accounts.get(recent[0]['id']))
                if not finished.is_set():print('资料服务已就绪。',flush=True)
            except Exception:
                if not finished.is_set():print('资料服务准备未完成，登录后可重试或查看管理员工作区状态。',flush=True)
        warmer = threading.Thread(target=warm_workspaces,daemon=True)
        warmer.start()
        server = uvicorn.Server(uvicorn.Config(create_app(root, project / 'dist', fleet=fleet), host=host, port=port, log_level='warning'))
        def monitor():
            opened = False
            while not finished.wait(.25):
                if stop_file.exists():server.should_exit=True;return
                if server.started and not opened:
                    opened = True
                    url = address(host, port)
                    print(f'\n后台已启动\n管理页面：{url}/admin/\n用户页面：{url}/app/\n资料目录：{root}\n停止后台：Ctrl+C 或 npm stop\n', flush=True)
                    if open_browser:webbrowser.open(url + '/admin/')
        threading.Thread(target=monitor, daemon=True).start()
        if not stop_file.exists():server.run()
    finally:
        finished.set()
        try:
            if fleet:
                print('正在保存任务并停止后台…', flush=True)
                if warmer:warmer.join()
                accounts = Accounts(root / 'private')
                fleet.request_stop_all([accounts.get(user['id']) for user in accounts.list()])
                for child in list(fleet.children.values()):child.wait(timeout=30)
        finally:
            stop_file.unlink(missing_ok=True);running.unlink(missing_ok=True);lease.release()
        print('后台已停止。', flush=True)


def main():
    if os.name == 'nt':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONUTF8', '1')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='首次指定门户资料目录，之后自动记住')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--stop', action='store_true', help='正常停止一键启动的后台')
    args = parser.parse_args()
    try:
        root, host, port, configuration = settings(args)
        if args.stop:stop_portal(root)
        else:start_portal(root, host, port, configuration, open_browser=not args.no_open)
    except KeyboardInterrupt:pass
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'操作未完成：{error}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':main()
