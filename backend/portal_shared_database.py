"""Versioned private research schemas with role-owned personal literature.

Only the portal provisioner reads owner credentials. Runtime roles have SELECT on
system sources, row-protected writes on their own sources, and one private schema. FKs retain source IDs.
"""
import json
import re
import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from .portal_database import native_url, save_private_json
from .knowledge.database import SCHEMA_REVISION
from .knowledge.shared import SHARED_TABLES

LAYOUT_VERSION = 1
CONFIG_POLICY_VERSION = 1


def provision_library_runtime(admin_url, identifier, configuration):
    """Keep native library API/Worker credentials out of schema management."""
    path=Path(configuration)
    value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'role':'wj_l_'+identifier,'password':secrets.token_urlsafe(36)}
    save_private_json(path,value)
    with psycopg.connect(native_url(admin_url)) as db:
        if not db.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(value['role'],)).fetchone():
            db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT').format(sql.Identifier(value['role']),sql.Literal(value['password'])))
        db.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(make_url(admin_url).database),sql.Identifier(value['role'])))
        db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(value['role'])))
        for table, in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND starts_with(tablename,'kb_')").fetchall():
            rights=sql.SQL('SELECT') if table=='kb_schema_version' else sql.SQL('SELECT,INSERT,UPDATE,DELETE')
            db.execute(sql.SQL('GRANT {} ON public.{} TO {}').format(rights,sql.Identifier(table),sql.Identifier(value['role'])))
    return make_url(admin_url).set(username=value['role'],password=value['password']).render_as_string(hide_password=False)


def provision_private(library, workspace_id, configuration):
    if not re.fullmatch(r'[a-f0-9]{32}', workspace_id):
        raise RuntimeError('账号工作区身份无效')
    path = Path(configuration)
    schema = 'w_' + workspace_id
    role = 'wj_r_' + workspace_id
    if path.exists():
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('mode') != 'shared':
            raise RuntimeError('此账号保留旧版独立资料库，请先按迁移说明处理，原数据未修改')
        if value.get('revision') not in {'kb0012','kb0013','kb0014', SCHEMA_REVISION} or value.get('layout') != LAYOUT_VERSION:
            raise RuntimeError('个人数据分区需要管理员离线升级')
        if value.get('revision') != SCHEMA_REVISION:
            value['ready'] = False
    else:
        password = secrets.token_urlsafe(36)
        value = {'mode': 'shared', 'schema': schema, 'role': role, 'password': password,
                 'runtime_url': make_url(library['runtime_url']).set(username=role, password=password).render_as_string(hide_password=False),
                 'library_root': library['root'], 'library_key': library['key'],
                 'revision': SCHEMA_REVISION, 'layout': LAYOUT_VERSION, 'ready': False}
        save_private_json(path, value)
    value.update(runtime_url=make_url(library['runtime_url']).set(username=role, password=value['password']).render_as_string(hide_password=False),
                 library_root=library['root'], library_key=library['key'])
    if value.get('ready'):
        if value.get('config_policy') == CONFIG_POLICY_VERSION:
            save_private_json(path, value)
            return value
    # Roles are cluster-wide; schema DDL and grants below are transactional.
    with psycopg.connect(native_url(library['admin_url']), autocommit=True) as admin:
        if not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT').format(sql.Identifier(role), sql.Literal(value['password'])))
        admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(make_url(library['runtime_url']).database), sql.Identifier(role)))
    # Schema creation needs a database-level privilege retained only by management.
    management_url = make_url(library['admin_url']).set(database=make_url(library['runtime_url']).database).render_as_string(hide_password=False)
    owner = make_url(library['owner_url']).username
    with psycopg.connect(native_url(management_url)) as admin:
        admin.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        admin.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}').format(sql.Identifier(schema), sql.Identifier(owner)))
    with psycopg.connect(native_url(library['owner_url'])) as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (schema,))
        db.execute('SET LOCAL search_path=public')
        marker = db.execute('SELECT to_regclass(%s)', (schema+'.wj_private_schema_version',)).fetchone()[0]
        if marker:
            version = db.execute(sql.SQL('SELECT layout,revision FROM {}.wj_private_schema_version').format(sql.Identifier(schema))).fetchone()
            if version not in {(LAYOUT_VERSION, SCHEMA_REVISION), (LAYOUT_VERSION, 'kb0012'),(LAYOUT_VERSION,'kb0013'),(LAYOUT_VERSION,'kb0014')}:
                raise RuntimeError('个人数据分区结构不兼容')
            # kb0013 changes public source ownership only; private research rows and IDs stay intact.
            db.execute(sql.SQL('UPDATE {}.wj_private_schema_version SET revision=%s').format(sql.Identifier(schema)), (SCHEMA_REVISION,))
            for table in sorted(SHARED_TABLES - {'kb_schema_version'}):
                db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON public.{} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
            value.update(ready=True, revision=SCHEMA_REVISION)
        else:
            _create_schema(db, schema, role)
        # Configuration is read from public explicitly. Existing private copies remain
        # untouched for historical runs and are never published into the shared registry.
        for table in ('kb_retrieval_settings','kb_assistant_definitions','kb_assistant_versions'):
            db.execute(sql.SQL('GRANT SELECT ON public.{} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
        # FOR SHARE fences revocation while a result is committed. The immutable
        # release trigger rejects changes to id; no mutable column is granted.
        db.execute(sql.SQL('GRANT UPDATE(id) ON public.kb_assistant_versions TO {}').format(sql.Identifier(role)))
        value['config_policy'] = CONFIG_POLICY_VERSION
        value.update(ready=True, revision=SCHEMA_REVISION)
    save_private_json(path, value)
    return value


def _create_schema(db, schema, role):
    tables = [row[0] for row in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND starts_with(tablename,'kb_') ORDER BY tablename")]
    private = set(tables) - SHARED_TABLES
    # LIKE preserves defaults, indexes, checks and generated columns, but not FKs.
    for table in sorted(private):
        db.execute(sql.SQL('CREATE TABLE {}.{} (LIKE public.{} INCLUDING ALL)').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)))
    for table in sorted(private):
        constraints = db.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype='f'", ('public.' + table,)).fetchall()
        for name, definition in constraints:
            def target(match):
                referred = match.group(1)
                return 'REFERENCES ' + (schema if referred in private else 'public') + '.' + referred
            definition = re.sub(r'REFERENCES (?:public\.)?(kb_[a-z_]+)', target, definition)
            db.execute(sql.SQL('ALTER TABLE {}.{} ADD CONSTRAINT {} ').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(name)) + sql.SQL(definition))
    db.execute(sql.SQL('CREATE TRIGGER kb_assistant_version_immutable BEFORE UPDATE OR DELETE ON {}.kb_assistant_versions FOR EACH ROW EXECUTE FUNCTION public.kb_assistant_version_immutable()').format(sql.Identifier(schema)))
    db.execute(sql.SQL('CREATE TABLE {}.wj_private_schema_version (layout integer NOT NULL, revision text NOT NULL)').format(sql.Identifier(schema)))
    db.execute(sql.SQL('INSERT INTO {}.wj_private_schema_version VALUES (%s,%s)').format(sql.Identifier(schema)), (LAYOUT_VERSION, SCHEMA_REVISION))
    db.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(schema)))
    db.execute(sql.SQL('GRANT USAGE ON SCHEMA public,{} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
    for table in sorted(SHARED_TABLES & set(tables)):
        rights = sql.SQL('SELECT') if table == 'kb_schema_version' else sql.SQL('SELECT,INSERT,UPDATE,DELETE')
        db.execute(sql.SQL('GRANT {} ON public.{} TO {}').format(rights, sql.Identifier(table), sql.Identifier(role)))
    for table in sorted(private):
        db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON {}.{} TO {}').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    db.execute(sql.SQL('GRANT SELECT ON {}.wj_private_schema_version TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
