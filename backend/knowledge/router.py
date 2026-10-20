from pathlib import Path
from threading import RLock

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from .config import SettingsUpdate, save_settings, settings_view
from .database import database


class CredentialSafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError as error:
                # FastAPI defaults echo invalid input, including password fields.
                return JSONResponse(status_code=422, content={"detail": [
                    {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()]})
            except SQLAlchemyError:
                return JSONResponse(status_code=503,content={'detail':'文献库操作未完成，请检查数据库状态或资料关联'})
            except ValueError:
                return JSONResponse(status_code=503,content={'detail':'文献库处理未完成，请检查配置和资料状态'})
        return handler


def register(app, service, legacy_connection=None, completion=None, model_settings=None):
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], route_class=CredentialSafeRoute)
    config_lock = RLock()

    def root() -> Path:
        if service.root is None:
            raise HTTPException(503, "资料服务尚未初始化")
        return service.root

    @router.get("/settings")
    def get_settings():
        try:
            with config_lock:
                return settings_view(root())
        except Exception:
            raise HTTPException(503, "文献库设置无法读取，请检查配置文件") from None

    @router.put("/settings")
    def update_settings(payload: SettingsUpdate):
        try:
            with config_lock:
                result = save_settings(root(), payload)
                database.dispose()
                if legacy_connection:
                    from .activation import CatalogActivation
                    CatalogActivation(root(),legacy_connection).reconcile()
                return result
        except ValueError:
            raise HTTPException(409, "当前由环境变量配置连接，请先取消环境变量再编辑") from None
        except Exception:
            raise HTTPException(503, "设置未保存，请检查系统凭据库和资料目录") from None

    @router.get("/health")
    @router.post("/connection-test")
    def health():
        with config_lock:
            return database.health(root())

    @router.get('/activation')
    def activation_status():
        from .activation import CatalogActivation
        return CatalogActivation(root(),legacy_connection).status()

    @router.post('/activation')
    def activate():
        from .activation import CatalogActivation
        from .worker import worker
        with service.lock:
            if getattr(service,'catalog_maintenance',False):raise HTTPException(409,'文献库正在升级，请等待完成')
            service.catalog_maintenance=True
        try:
            worker.stop()
            if worker.thread:worker.thread.join(30)
            if worker.thread and worker.thread.is_alive():raise HTTPException(409,'资料处理仍在停止，请稍后迁移')
            result=CatalogActivation(root(),legacy_connection).migrate()
        finally:
            service.catalog_maintenance=False
        if result['active']:worker.start(root())
        return result

    if legacy_connection is not None:
        from .core_corpus import register as register_core
        register_core(router,root)
        from .api import register_features
        from .worker import worker
        def sync_project_events():
            from .execution import activated
            from .project_sources import ProjectSourceService
            with legacy_connection() as local:
                active=activated(local)
                marker=local.execute("SELECT 1 FROM app_settings WHERE setting_key='catalog-activation'").fetchone()
            if marker and not active:return False
            if active:ProjectSourceService(root(),legacy_connection).sync_events()
            return True
        worker.sync_callback=sync_project_events
        register_features(router,root,legacy_connection,completion,model_settings)
        from .unified_api import register as register_unified
        from datetime import datetime,timezone
        register_unified(router,root,legacy_connection,lambda:datetime.now(timezone.utc).isoformat(timespec='seconds'))
        app.router.add_event_handler('startup',lambda:worker.start(root()))
        app.router.add_event_handler('shutdown',worker.stop)
    app.include_router(router)
    app.router.add_event_handler("shutdown", database.dispose)
