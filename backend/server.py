"""Standalone personal workspace service: python -m backend.server."""
import argparse
import os
from pathlib import Path


def configure(root, token):
    from . import app as application
    from .knowledge.worker import worker
    if len(token) < 32:
        raise ValueError('请通过 SHIXUE_SERVER_TOKEN 配置至少 32 字符的服务连接凭据')
    application.service.mode = 'server'
    application.service.token = token
    application.service.embedded_workers = False
    worker.enabled = False
    application.initialize(Path(root).expanduser().resolve(), recover=False)
    return application.app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--ui-dir', type=Path, help='已构建的 React 页面目录，例如 dist')
    parser.add_argument('--prepare', action='store_true', help='离线初始化并激活已升级的文献库，然后退出')
    args = parser.parse_args()
    token = os.environ.get('SHIXUE_SERVER_TOKEN', '')
    if len(token) < 32:
        parser.error('请设置 SHIXUE_SERVER_TOKEN（至少 32 字符）')
    ui = args.ui_dir.resolve() if args.ui_dir else None
    if ui and not (ui / 'index.html').is_file():
        parser.error('--ui-dir 需要包含构建后的 index.html')
    from .knowledge.managed import DirectoryLease
    lease = DirectoryLease(args.data_dir.expanduser().resolve() / '.http-service.lock')
    lease.acquire()
    try:
        preparation_lease = None
        if args.prepare:
            preparation_lease = DirectoryLease(args.data_dir.expanduser().resolve() / '.execution-worker.lock')
            preparation_lease.acquire()
        try:
            application = configure(args.data_dir, token)
            from . import app as legacy
            legacy.uvicorn_port = args.port
            from .knowledge.activation import CatalogActivation
            activation = CatalogActivation(legacy.service.root, legacy.connection)
            activation.reconcile()
            if args.prepare:
                from .knowledge.database import database
                if not database.health(legacy.service.root)['ready']:
                    raise RuntimeError('数据库尚未就绪，请先配置外部 PostgreSQL 并执行结构升级')
                result = activation.migrate()
                if not result['active']:
                    raise RuntimeError('文献库尚未激活，请处理旧资料迁移中的冲突后重试')
                print('工作区已准备，可启动 API 与 Worker')
                return
        finally:
            if preparation_lease:
                preparation_lease.release()
        if ui:
            from fastapi.staticfiles import StaticFiles
            application.mount('/', StaticFiles(directory=ui, html=True), name='client')
        import uvicorn
        # One API process owns this workspace. Execution belongs to backend.worker.
        uvicorn.run(application, host=args.host, port=args.port, log_level='info')
    finally:
        lease.release()


if __name__ == '__main__':
    main()
