from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .database import build_engine


def upgrade(url):
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            # Serialize schema upgrades; application health never runs this code.
            connection.execute(text("SELECT pg_advisory_xact_lock(824171,1)"))
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            if connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname='shixue_app'")).scalar():
                connection.execute(text("REVOKE INSERT, UPDATE, DELETE ON public.kb_schema_version FROM shixue_app"))
    finally:
        engine.dispose()
