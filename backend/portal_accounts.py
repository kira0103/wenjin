"""Portal identities, revocable sessions, and administrator audit records."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

SESSION_SECONDS = 12 * 60 * 60


class AccountError(ValueError):
    pass


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise AccountError('密码需要 12–256 个字符')
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1,
                            maxmem=64 * 1024 * 1024, dklen=32).hex()
    return f'scrypt${salt}${digest}'


def password_matches(password, encoded):
    try:
        method, salt, _ = encoded.split('$')
        return method == 'scrypt' and hmac.compare_digest(password_hash(password, salt), encoded)
    except (AccountError, ValueError, TypeError):
        return False


def public_user(row):
    return {key: row[key] for key in ('id', 'username', 'display_name', 'role', 'enabled',
                                      'password_change_required', 'created_at', 'last_login_at')}


class Accounts:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'accounts.sqlite'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            from .portal_migrations import migrate
            migrate(db)
        # Equal work for unknown usernames, without hashing a password per failed request.
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, actor, action, target=None, details=None):
        db.execute('INSERT INTO portal_audit(actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?)',
                   (actor, action, target, json.dumps(details or {}, ensure_ascii=False), time.time()))

    def create(self, username, display_name, password, role='user', actor=None, require_change=True, bootstrap=False):
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,47}', username):
            raise AccountError('用户名需要 3–48 个小写字母、数字、点、下划线或连字符')
        if role not in {'user', 'admin'}:
            raise AccountError('账号角色无效')
        name = display_name.strip()
        if not name or len(name) > 80:
            raise AccountError('显示名称需要 1–80 个字符')
        encoded = password_hash(password)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if bootstrap and db.execute("SELECT 1 FROM portal_users WHERE role='admin' LIMIT 1").fetchone():
                raise AccountError('管理员已存在，请登录后台管理账号')
            identifier, workspace = uuid.uuid4().hex, uuid.uuid4().hex
            try:
                db.execute('INSERT INTO portal_users(id,username,display_name,password_hash,role,password_change_required,created_at,workspace_id) VALUES(?,?,?,?,?,?,?,?)',
                           (identifier, username, name, encoded, role, int(require_change), time.time(), workspace))
            except sqlite3.IntegrityError:
                raise AccountError('用户名已存在') from None
            self.audit(db, actor, 'user.create', identifier, {'username': username, 'role': role})
            return public_user(db.execute('SELECT * FROM portal_users WHERE id=?', (identifier,)).fetchone())

    def authenticate(self, username, password, remote=''):
        username = username.strip().lower() if isinstance(username, str) else ''
        attempt = hashlib.sha256((remote + '\n' + username).encode()).hexdigest()
        now = time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM portal_login_attempts WHERE created_at<?', (now - 900,))
            count = db.execute('SELECT count(*) FROM portal_login_attempts WHERE attempt_key=?', (attempt,)).fetchone()[0]
            if count >= 10:
                raise AccountError('尝试次数过多，请在 15 分钟后重试')
            db.execute('INSERT INTO portal_login_attempts VALUES(?,?)', (attempt, now))
            row = db.execute('SELECT * FROM portal_users WHERE username=?', (username,)).fetchone()
        valid = password_matches(password, row['password_hash'] if row else self.dummy_hash)
        if not valid or not row or not row['enabled']:
            raise AccountError('用户名或密码不正确，或账号已停用')
        token = secrets.token_urlsafe(48)
        with self.connection() as db:
            # Recheck after the expensive hash: disabling or resetting an account wins a race with login.
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM portal_users WHERE id=?', (row['id'],)).fetchone()
            if not current['enabled'] or current['password_hash'] != row['password_hash']:
                raise AccountError('账号状态已更新，请重新登录')
            db.execute('DELETE FROM portal_sessions WHERE expires_at<?', (now,))
            db.execute('INSERT INTO portal_sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], now + SESSION_SECONDS, now))
            db.execute('UPDATE portal_users SET last_login_at=? WHERE id=?', (now, row['id']))
            db.execute('DELETE FROM portal_login_attempts WHERE attempt_key=?', (attempt,))
            return token, public_user(db.execute('SELECT * FROM portal_users WHERE id=?', (row['id'],)).fetchone())

    def session(self, token):
        if not token or not isinstance(token, str) or len(token) > 256:
            return None
        with self.connection() as db:
            row = db.execute('SELECT u.* FROM portal_users u JOIN portal_sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM portal_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def list(self):
        with self.connection() as db:
            return [public_user(row) for row in db.execute('SELECT * FROM portal_users ORDER BY created_at')]

    def get(self, identifier):
        with self.connection() as db:
            row = db.execute('SELECT * FROM portal_users WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AccountError('找不到此账号')
            return dict(row)

    def update(self, identifier, actor, *, display_name, role, enabled):
        if role not in {'user', 'admin'} or not display_name.strip() or len(display_name.strip()) > 80:
            raise AccountError('账号信息无效')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM portal_users WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AccountError('找不到此账号')
            if row['role'] == 'admin' and row['enabled'] and (role != 'admin' or not enabled):
                admins = db.execute("SELECT count(*) FROM portal_users WHERE role='admin' AND enabled=1").fetchone()[0]
                if admins <= 1:
                    raise AccountError('需要保留至少一个可用管理员')
            if identifier == actor and not enabled:
                raise AccountError('不能停用当前登录账号')
            db.execute('UPDATE portal_users SET display_name=?,role=?,enabled=? WHERE id=?', (display_name.strip(), role, int(enabled), identifier))
            if not enabled or role != row['role']:
                db.execute('DELETE FROM portal_sessions WHERE user_id=?', (identifier,))
            self.audit(db, actor, 'user.update', identifier, {'role': role, 'enabled': enabled})
            return public_user(db.execute('SELECT * FROM portal_users WHERE id=?', (identifier,)).fetchone())

    def reset_password(self, identifier, password, actor, current_password=None):
        row = self.get(identifier)
        if current_password is not None and not password_matches(current_password, row['password_hash']):
            raise AccountError('当前密码不正确')
        encoded = password_hash(password)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if current_password is not None:
                current = db.execute('SELECT password_hash FROM portal_users WHERE id=?', (identifier,)).fetchone()
                if not current or current['password_hash'] != row['password_hash']:
                    raise AccountError('密码已更新，请重新登录')
            db.execute('UPDATE portal_users SET password_hash=?,password_change_required=? WHERE id=?', (encoded, int(current_password is None), identifier))
            db.execute('DELETE FROM portal_sessions WHERE user_id=?', (identifier,))
            self.audit(db, actor, 'user.password.change' if current_password is not None else 'user.password.reset', identifier)

    def recent_audit(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT a.id,a.action,a.target_id,a.details,a.created_at,u.username actor,t.username target FROM portal_audit a LEFT JOIN portal_users u ON u.id=a.actor_id LEFT JOIN portal_users t ON t.id=a.target_id ORDER BY a.id DESC LIMIT 100')]
