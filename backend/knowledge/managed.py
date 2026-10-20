"""Owned, portable PostgreSQL lifecycle. Never manages an external database."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
from sqlalchemy import URL


USERNAME = 'shixue_local'
DATABASE = 'shixue_knowledge'


def runtime_directory() -> Path:
    override = os.environ.get('SHIXUE_PG_RUNTIME')
    if override:
        return Path(override).resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent.parent / 'postgres'
    return Path(__file__).resolve().parents[2] / 'build' / 'postgres'


def _run(command, *, env=None, timeout=45):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    # Daemons can inherit pipe handles from pg_ctl on Windows. Regular files let
    # the launcher finish without waiting for the database to close those handles.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(command, env=env, stdout=output, stderr=errors,
                                timeout=timeout, **options)
        output.seek(0)
        errors.seek(0)
        result.stdout = output.read()
        result.stderr = errors.read()
        return result


def _write_json(path: Path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _current_sid() -> str:
    result = _run(['whoami', '/user', '/fo', 'csv', '/nh'])
    if result.returncode:
        raise RuntimeError('无法识别当前用户，未创建数据库凭据')
    import csv
    return next(csv.reader([result.stdout.decode('utf-8', errors='replace').strip()]))[1]


def _protect(path: Path, sid: str):
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    suffix = ':(OI)(CI)F' if path.is_dir() else ':F'
    reset = _run(['icacls', str(path), '/reset'])
    if reset.returncode:
        raise RuntimeError('数据库凭据权限无法重设，请检查资料目录权限')
    result = _run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + sid + suffix,
                   '*S-1-5-18' + suffix])
    if result.returncode:
        raise RuntimeError('数据库凭据权限无法设置，请检查资料目录权限')


def check_local_storage(root: Path):
    if os.name != 'nt':
        return
    if str(root).startswith('\\\\'):
        raise RuntimeError('内置数据库暂不支持网络资料目录')
    kernel = ctypes.windll.kernel32
    volume = ctypes.create_unicode_buffer(1024)
    filesystem = ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumePathNameW(str(root), volume, len(volume)):
        raise RuntimeError('无法检查资料目录文件系统')
    if kernel.GetDriveTypeW(volume.value) != 3:
        raise RuntimeError('内置数据库暂仅支持本地固定磁盘')
    if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, None,
                                        filesystem, len(filesystem)) or filesystem.value != 'NTFS':
        raise RuntimeError('内置数据库需要本地 NTFS 资料目录')


class DirectoryLease:
    """Keep the lock inode/file persistent so concurrent launches cannot replace it."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeError('另一工作台实例正在使用此资料目录') from None
        self.stream = stream

    def release(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class ManagedPostgres:
    def __init__(self, root: Path, runtime: Path | None = None):
        self.root = root.resolve()
        self.runtime = (runtime or runtime_directory()).resolve()
        self.private = self.root / '.managed-postgres'
        self.data = self.root / 'knowledge-db'
        self.lease = DirectoryLease(self.private / 'owner.lock')
        self.port = None
        self._credential = None
        self.running = False
        self.recovered = False

    def tool(self, name: str) -> Path:
        if name not in {'initdb', 'pg_ctl', 'pg_dump', 'pg_restore', 'postgres', 'psql'}:
            raise ValueError('未知数据库工具')
        path = self.runtime / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
        if not path.is_file():
            raise RuntimeError('随程序提供的数据库工具缺失')
        return path

    def _environment(self):
        return {**os.environ, 'PATH': str(self.runtime / 'bin') + os.pathsep + os.environ.get('PATH', '')}

    def verify_runtime(self):
        manifest_path = self.runtime / 'runtime-manifest.json'
        if not manifest_path.is_file():
            raise RuntimeError('内置数据库运行时缺少校验清单')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if os.name == 'nt' and sys.getwindowsversion().build < manifest.get('minimum_windows_build', 18362):
            raise RuntimeError('内置数据库需要 Windows 10 1903 或更高版本')
        for relative, expected in manifest['files'].items():
            path = (self.runtime / relative).resolve()
            if self.runtime not in path.parents or not path.is_file():
                raise RuntimeError('数据库运行时文件缺失或路径不合法')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise RuntimeError('数据库运行时文件校验失败')
        for name in ('initdb', 'pg_ctl', 'pg_dump', 'pg_restore', 'postgres'):
            self.tool(name)

    def _read_credential(self):
        sid = _current_sid() if os.name == 'nt' else ''
        _protect(self.private, sid)
        target = self.private / 'credentials.json'
        if not target.exists():
            if self.data.exists():
                raise RuntimeError('已有数据库的凭据缺失，请从逻辑备份恢复，不能重新初始化')
            _write_json(target, {'username': USERNAME, 'password': secrets.token_urlsafe(48)})
        _protect(target, sid)
        value = json.loads(target.read_text(encoding='utf-8'))
        if value.get('username') != USERNAME or len(value.get('password', '')) < 32:
            raise RuntimeError('数据库凭据文件损坏')
        return value

    def _initialize(self):
        marker = self.private / 'cluster.json'
        if self.data.exists():
            if not marker.exists() or (self.data / 'PG_VERSION').read_text().strip() != '17':
                raise RuntimeError('数据库目录无法验证，请从备份恢复；不会覆盖已有目录')
            return
        pending_path = self.private / 'initialization.json'
        if pending_path.exists():
            pending = json.loads(pending_path.read_text(encoding='utf-8'))
            old = (self.root / pending['stage']).resolve()
            # Only erase an unactivated staging directory explicitly owned by this initializer.
            if old.parent != self.root or not old.name.startswith('.knowledge-db-initializing-'):
                raise RuntimeError('数据库初始化记录路径异常')
            if old.exists():
                shutil.rmtree(old)
        stage = self.root / ('.knowledge-db-initializing-' + uuid.uuid4().hex)
        _write_json(pending_path, {'stage': stage.name, 'format': 1})
        password_file = self.private / 'init-password'
        try:
            password_file.write_text(self._credential['password'] + '\n', encoding='utf-8')
            result = _run([str(self.tool('initdb')), '-D', str(stage), '-U', USERNAME,
                           '--pwfile=' + str(password_file), '--encoding=UTF8',
                           '--locale=C', '--locale-provider=builtin', '--builtin-locale=C.UTF-8',
                           '--auth-host=scram-sha-256', '--auth-local=scram-sha-256'],
                          env=self._environment())
            if result.returncode:
                # initdb diagnostics contain paths, but no password. Keep them outside API output.
                (self.private / 'initialization.log').write_bytes(result.stdout + result.stderr)
                raise RuntimeError('数据库初始化失败，请查看初始化日志')
            _write_json(marker, {'format': 1, 'cluster_id': uuid.uuid4().hex, 'pg_major': 17})
            stage.rename(self.data)
            pending_path.unlink(missing_ok=True)
        finally:
            password_file.unlink(missing_ok=True)

    def _owned_residual(self):
        pid_file = self.data / 'postmaster.pid'
        if not pid_file.exists():
            return
        lines = pid_file.read_text(encoding='utf-8').splitlines()
        if len(lines) < 2 or Path(lines[1]).resolve() != self.data:
            raise RuntimeError('残留数据库进程不属于此资料目录')
        self.recovered = True
        if os.name == 'nt':
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, int(lines[0]))
            if handle:
                try:
                    executable = ctypes.create_unicode_buffer(32768)
                    length = ctypes.c_ulong(len(executable))
                    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                                 ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
                    if not kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
                        raise RuntimeError('无法验证残留数据库进程')
                    if Path(executable.value).resolve() != self.tool('postgres').resolve():
                        raise RuntimeError('残留进程不是本程序的数据库，未操作该进程')
                finally:
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    kernel.CloseHandle(handle)
                result = _run([str(self.tool('pg_ctl')), '-D', str(self.data), '-m', 'fast',
                               '-w', '-t', '15', 'stop'], env=self._environment(), timeout=25)
                if result.returncode:
                    raise RuntimeError('无法正常停止残留数据库进程')
        # PostgreSQL itself checks/removes a stale pid file during startup; never unlink it here.

    def start(self):
        if self.running:
            return self
        self.root.mkdir(parents=True, exist_ok=True)
        check_local_storage(self.root)
        self.lease.acquire()
        try:
            self.verify_runtime()
            self._credential = self._read_credential()
            self._initialize()
            previous = self.private / 'runtime-state.json'
            if previous.exists():
                self.recovered = not json.loads(previous.read_text(encoding='utf-8')).get('clean_shutdown', True)
            self._owned_residual()
            log = self.private / 'postgres.log'
            for _ in range(3):
                self.port = self.free_port()
                result = _run([str(self.tool('pg_ctl')), '-D', str(self.data), '-l', str(log),
                               '-w', '-t', '15', '-o', f'-h 127.0.0.1 -p {self.port}', 'start'],
                              env=self._environment(), timeout=25)
                if result.returncode == 0:
                    break
            else:
                raise RuntimeError('内置数据库启动失败，请查看数据库日志')
            self.running = True
            _write_json(previous, {'clean_shutdown': False, 'port': self.port})
            with self.connect('postgres', autocommit=True) as connection:
                if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DATABASE,)).fetchone():
                    connection.execute('CREATE DATABASE shixue_knowledge')
            with self.connect() as connection:
                connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
                connection.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            return self
        except Exception:
            self.stop()
            raise

    @staticmethod
    def free_port():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            return probe.getsockname()[1]

    @property
    def url(self):
        if not self.running:
            raise RuntimeError('内置数据库未运行')
        return URL.create('postgresql+psycopg', username=USERNAME, password=self._credential['password'],
                          host='127.0.0.1', port=self.port, database=DATABASE)

    def connect(self, dbname=DATABASE, **kwargs):
        return psycopg.connect(host='127.0.0.1', port=self.port, dbname=dbname,
                               user=USERNAME, password=self._credential['password'],
                               connect_timeout=3, **kwargs)

    def stop(self):
        if self.running:
            result = _run([str(self.tool('pg_ctl')), '-D', str(self.data), '-m', 'fast',
                           '-w', '-t', '15', 'stop'], env=self._environment(), timeout=25)
            if result.returncode and (self.data / 'postmaster.pid').exists():
                # Do not release the directory lease while a managed service is still running.
                raise RuntimeError('数据库尚未正常停止，请检查日志后再搬迁资料目录')
            self.running = False
            _write_json(self.private / 'runtime-state.json', {'clean_shutdown': True})
        self._credential = None
        self.lease.release()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
