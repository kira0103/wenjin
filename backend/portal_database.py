"""Provision a distinct PostgreSQL database and restricted role per workspace."""
import json
import os
import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from .knowledge.database import SCHEMA_REVISION
from .knowledge.migration import upgrade


def save_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        if os.name != 'nt':
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def native_url(value):
    return make_url(value).set(drivername='postgresql').render_as_string(hide_password=False)


def provision(admin_url, workspace_id, configuration):
    path = Path(configuration)
    if path.is_file():
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('ready'):
            if value.get('schema') != SCHEMA_REVISION:
                raise RuntimeError('工作区数据库需要管理员离线升级')
            return value['runtime_url']
    else:
        base = make_url(admin_url)
        database = 'wj_u_' + workspace_id
        owner, reader = 'wj_o_' + workspace_id, 'wj_a_' + workspace_id
        owner_password, reader_password = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
        value = {'database': database, 'owner': owner, 'app': reader,
                 'owner_password': owner_password, 'app_password': reader_password,
                 'owner_url': base.set(drivername='postgresql+psycopg', database=database, username=owner, password=owner_password).render_as_string(hide_password=False),
                 'runtime_url': base.set(drivername='postgresql+psycopg', database=database, username=reader, password=reader_password).render_as_string(hide_password=False),
                 'ready': False}
        save_private_json(path, value)
    with psycopg.connect(native_url(admin_url), autocommit=True, connect_timeout=5) as connection:
        for name, password in [(value['owner'], value['owner_password']), (value['app'], value['app_password'])]:
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(name), sql.Literal(password)))
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (value['database'],)).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(value['database']), sql.Identifier(value['owner'])))
    admin_database = make_url(admin_url).set(database=value['database'])
    with psycopg.connect(native_url(admin_database.render_as_string(hide_password=False)), connect_timeout=5) as connection:
        connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
        connection.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        connection.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(value['database'])))
        connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {},{}').format(sql.Identifier(value['database']), sql.Identifier(value['owner']), sql.Identifier(value['app'])))
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        connection.execute(sql.SQL('GRANT USAGE,CREATE ON SCHEMA public TO {}').format(sql.Identifier(value['owner'])))
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(value['app'])))
        connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}').format(sql.Identifier(value['owner']), sql.Identifier(value['app'])))
        connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO {}').format(sql.Identifier(value['owner']), sql.Identifier(value['app'])))
    upgrade(value['owner_url'])
    with psycopg.connect(native_url(admin_database.render_as_string(hide_password=False)), connect_timeout=5) as connection:
        connection.execute(sql.SQL('REVOKE INSERT,UPDATE,DELETE ON kb_schema_version FROM {}').format(sql.Identifier(value['app'])))
    value.update(ready=True, schema=SCHEMA_REVISION)
    save_private_json(path, value)
    return value['runtime_url']
