"""Product-owned database startup; external databases are never auto-upgraded."""
import os
from pathlib import Path

from .config import read_settings,register_managed,unregister_managed,register_managed_failure
from .database import database,SCHEMA_REVISION
from .managed import ManagedPostgres
from .migration import upgrade

class IncompatibleWorkspace(RuntimeError):
    pass

class ProductDatabase:
    def __init__(self,root,mode='auto'):
        self.root=root;self.mode=mode;self.manager=None;self.startup_error=''
        self.incompatible=False

    def start(self):
        if any((self.root/name).exists() for name in ('.workspace-copy-incomplete','.candidate-incomplete')):
            raise RuntimeError('此资料目录的迁移尚未完成，请使用已校验的原资料目录')
        settings=read_settings(self.root)
        external=bool(os.environ.get('SHIXUE_KB_DATABASE_URL')) or settings.mode=='external' or settings.enabled and settings.mode=='auto'
        if self.mode=='external' or self.mode=='auto' and external:return
        try:
            self.manager=ManagedPostgres(self.root)
            self.manager.start()
            register_managed(self.root,self.manager.url)
            with database.transaction(self.root) as connection:
                from sqlalchemy import text
                exists=connection.execute(text("SELECT to_regclass('public.kb_schema_version')")).scalar()
                revision=connection.execute(text('SELECT version_num FROM kb_schema_version')).scalar() if exists else None
            if revision!=SCHEMA_REVISION:
                if revision:
                    from alembic.script import ScriptDirectory
                    scripts=ScriptDirectory(str(Path(__file__).resolve().parents[1]/'migrations'))
                    supported={item.revision for item in scripts.walk_revisions(base='base',head=SCHEMA_REVISION)}
                    if revision not in supported:
                        raise IncompatibleWorkspace('资料结构比此程序更新或不兼容，请使用对应新版；未尝试降级')
                # User preference: schema upgrades do not generate full data copies.
                # Alembic applies transactional DDL; explicit exports remain available.
                upgrade(self.manager.url)
        except Exception as error:
            self.incompatible=isinstance(error,IncompatibleWorkspace)
            self.stop()
            if isinstance(error,RuntimeError) and '另一工作台实例' in str(error):raise
            self.startup_error=str(error) if isinstance(error,RuntimeError) else '内置数据库暂不可用，请检查日志或从备份恢复'
            register_managed_failure(self.root,self.startup_error)

    def stop(self):
        database.dispose()
        if self.manager:
            self.manager.stop();unregister_managed(self.root);self.manager=None
        else:unregister_managed(self.root)
