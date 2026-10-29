"""Versioned schema migrations for the portal identity database."""
SCHEMA_VERSION = 1

FOUNDATION = r'''
                CREATE TABLE IF NOT EXISTS portal_schema (version INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS portal_users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
                    enabled INTEGER NOT NULL DEFAULT 1, password_change_required INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, last_login_at REAL,
                    workspace_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS portal_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES portal_users(id),
                    expires_at REAL NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS portal_sessions_user ON portal_sessions(user_id);
                CREATE TABLE IF NOT EXISTS portal_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT, action TEXT NOT NULL,
                    target_id TEXT, details TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portal_login_attempts (
                    attempt_key TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS portal_login_attempt_key ON portal_login_attempts(attempt_key,created_at);
                INSERT OR IGNORE INTO portal_schema(version) VALUES(1);
'''


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS portal_schema (version INTEGER PRIMARY KEY)')
    version = db.execute('SELECT max(version) FROM portal_schema').fetchone()[0]
    if version is not None and version > SCHEMA_VERSION:
        raise RuntimeError('账号数据库结构比当前程序更新，请使用对应版本')
    if version is None:
        db.executescript(FOUNDATION)
