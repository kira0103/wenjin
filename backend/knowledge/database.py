from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
try:
    from backend.identity import workspace_key
except ModuleNotFoundError:
    from identity import workspace_key
from threading import RLock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from .config import connection_url,managed_url,managed_failure
from .shared import private_schema, library_key, SHARED_TABLES

SCHEMA_REVISION = "kb0012"
REQUIRED_TABLES = ("kb_libraries", "kb_works", "kb_editions", "kb_assets",
                   "kb_edition_assets", "kb_pages", "kb_page_labels", "kb_revisions",
                   "kb_blocks", "kb_indexes", "kb_chunks", "kb_notes", "kb_jobs", "kb_anchors",
                   "kb_retrieval_runs", "kb_eval_suites", "kb_eval_cases", "kb_eval_runs", "kb_retrieval_settings",
                   "kb_research_runs", "kb_research_steps", "kb_assistant_definitions", "kb_assistant_versions",
                   "kb_skill_tests", "kb_collaboration_runs", "kb_collaboration_nodes",
                   "kb_corpora", "kb_core_books", "kb_core_files", "kb_core_passages",
                   "kb_literature_category_state", "kb_literature_categories", "kb_literature_memberships",
                   "kb_document_library_state", "kb_document_groups", "kb_document_entries", "kb_document_imports")


def build_engine(url) -> Engine:
    parsed = make_url(url) if isinstance(url, str) else url
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("文献库需要 PostgreSQL 连接")
    parsed = parsed.set(drivername="postgresql+psycopg")
    # URL options cannot override the connection / query timeout.
    schema = private_schema()
    options = "-c statement_timeout=5000 -c lock_timeout=3000"
    if schema:
        options += ' -c search_path=' + schema + ',public'
    return create_engine(parsed, pool_pre_ping=True, pool_size=4, max_overflow=4,
                         pool_timeout=5, hide_parameters=True,
                         connect_args={"connect_timeout": 3,
                                       "options": options})


class KnowledgeDatabase:
    def __init__(self):
        self._lock = RLock()
        self._engine: Engine | None = None
        self._identity = None

    def dispose(self):
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
            self._engine = None
            self._identity = None

    def engine(self, root: Path) -> Engine | None:
        url = connection_url(root)
        identity = (str(root.resolve()), url, private_schema())
        with self._lock:
            if identity != self._identity:
                self.dispose()
                if url is not None:
                    self._engine = build_engine(url)
                self._identity = identity
            return self._engine

    @contextmanager
    def transaction(self, root: Path, isolation_level=None):
        engine = self.engine(root)
        if engine is None:
            raise ValueError("文献库尚未启用")
        with engine.connect() as connection:
            if isolation_level:
                connection=connection.execution_options(isolation_level=isolation_level)
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock_shared(824171,2)"))
                workspace=library_key(workspace_key(root))
                connection.execute(text("SELECT set_config('shixue.workspace',:key,true)"),{'key':workspace})
                yield connection

    def health(self, root: Path) -> dict:
        result = {"status": "disabled", "connected": False, "ready": False,
                  "expected_revision": SCHEMA_REVISION, "schema_revision": None,
                  "extensions": {}, "message": "文献库尚未启用"}
        if managed_failure(root):
            result.update(status='unavailable',mode='internal',message=managed_failure(root))
            return result
        try:
            engine = self.engine(root)
            if engine is None:
                return result
            with engine.connect() as connection:
                result["connected"] = True
                result["server_version"] = connection.execute(text("SHOW server_version")).scalar_one()
                result["extensions"] = dict(connection.execute(text(
                    "SELECT extname,extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm')")).all())
                version_table = connection.execute(text("SELECT to_regclass('public.kb_schema_version')")).scalar_one()
                revisions = list(connection.execute(text("SELECT version_num FROM public.kb_schema_version")).scalars()) if version_table else []
                result["schema_revision"] = revisions[0] if len(revisions) == 1 else None
                result["missing_tables"] = [table for table in REQUIRED_TABLES if not connection.execute(
                    text("SELECT to_regclass(:name)"), {"name": table}).scalar_one()]
                role = connection.execute(text(
                    "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")).one()
                schema_create = connection.execute(text(
                    "SELECT has_schema_privilege(current_user,'public','CREATE')")).scalar_one()
                owns_tables = connection.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' "
                    "AND tablename LIKE 'kb_%' AND tableowner=current_user)" )).scalar_one()
                result["restricted_role"] = not (any(role) or schema_create or owns_tables)
                internal=managed_url(root) is not None
                result['mode']='internal' if internal else 'external'
                privileges_ok = not result["missing_tables"] and all(connection.execute(text(
                    "SELECT has_table_privilege(current_user,:name,'SELECT') AND "
                    "(:readonly OR (has_table_privilege(current_user,:name,'INSERT') AND "
                    "has_table_privilege(current_user,:name,'UPDATE') AND has_table_privilege(current_user,:name,'DELETE')))"),
                    {"name": name, "readonly": bool(private_schema() and name in SHARED_TABLES)}).scalar_one() for name in REQUIRED_TABLES)
                if private_schema():
                    result['mode'] = 'shared-library'
                    layout = connection.execute(text('SELECT layout,revision FROM wj_private_schema_version')).one()
                    privileges_ok = privileges_ok and layout == (1, SCHEMA_REVISION)
                result["data_access"] = privileges_ok
                result["ready"] = (set(result["extensions"]) == {"vector", "pg_trgm"}
                                   and revisions == [SCHEMA_REVISION] and privileges_ok
                                   and (result["restricted_role"] or internal))
                result["status"] = "ready" if result["ready"] else "setup_required"
                result["message"] = "文献库连接正常，基础结构已就绪" if result["ready"] else "连接成功，请完成结构升级、扩展或运行账号权限配置"
        except Exception:
            result.update(status="unavailable", ready=False,
                          message="文献库不可用，请检查数据库服务、连接设置和账号权限")
        return result


database = KnowledgeDatabase()
