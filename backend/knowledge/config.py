from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

import keyring
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import URL

CREDENTIAL_SERVICE = "shixue-workbench-knowledge"
_managed_connections = {}
_managed_failures = {}


def managed_failure(root):
    return _managed_failures.get(str(root.resolve()))


def register_managed_failure(root,message):
    _managed_failures[str(root.resolve())]=message


def register_managed(root, url):
    _managed_failures.pop(str(root.resolve()),None)
    _managed_connections[str(root.resolve())] = url


def unregister_managed(root):
    _managed_connections.pop(str(root.resolve()), None)
    _managed_failures.pop(str(root.resolve()),None)


def managed_url(root):
    return _managed_connections.get(str(root.resolve()))


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    mode: Literal['auto','internal','external'] = 'auto'
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=55432, ge=1, le=65535)
    database: str = Field(default="shixue_knowledge", min_length=1, max_length=63)
    username: str = Field(default="shixue_app", min_length=1, max_length=63)

    @field_validator("host", "database", "username")
    @classmethod
    def clean(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("连接字段不能为空或包含控制字符")
        return value.strip()


class SettingsUpdate(DatabaseSettings):
    password: str | None = Field(default=None, max_length=1024)


def credential_account(root: Path, settings: DatabaseSettings) -> str:
    identity = [os.path.normcase(str(root.resolve())), settings.host, settings.port,
                settings.database, settings.username]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def read_settings(root: Path) -> DatabaseSettings:
    path = root / "knowledge.json"
    return DatabaseSettings.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else DatabaseSettings()


def settings_view(root: Path) -> dict:
    failure=managed_failure(root)
    if failure:
        return {**DatabaseSettings().model_dump(),'mode':'internal','enabled':False,'has_password':False,
                'environment_override':True,'credential_available':True,'message':failure}
    internal=managed_url(root)
    if internal is not None:
        return {'enabled':True,'mode':'internal','host':internal.host,'port':internal.port,
                'database':internal.database,'username':internal.username,'has_password':True,
                'environment_override':True,'credential_available':True}
    settings = read_settings(root)
    environmental = bool(os.environ.get("SHIXUE_KB_DATABASE_URL"))
    # Never return a URL, password, or credential store exception.
    has_password = False
    credential_available = True
    if not environmental:
        try:
            has_password = bool(keyring.get_password(CREDENTIAL_SERVICE, credential_account(root, settings)))
        except Exception:
            credential_available = False
    return {**settings.model_dump(), "has_password": has_password,
            "environment_override": environmental, "credential_available": credential_available}


def save_settings(root: Path, update: SettingsUpdate) -> dict:
    if managed_url(root):
        raise ValueError('内置数据库由工作台管理')
    if os.environ.get("SHIXUE_KB_DATABASE_URL"):
        raise ValueError("当前由环境变量配置连接，请先取消环境变量再编辑")
    settings = DatabaseSettings.model_validate(update.model_dump(exclude={"password"}))
    # Empty input keeps the credential for this exact host/database/user.
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ("knowledge-" + uuid.uuid4().hex + ".tmp")
    account = credential_account(root, settings)
    previous_password = keyring.get_password(CREDENTIAL_SERVICE, account) if update.password else None
    credential_changed = False
    try:
        temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        if update.password:
            keyring.set_password(CREDENTIAL_SERVICE, account, update.password)
            credential_changed = True
        temporary.replace(root / "knowledge.json")
    except Exception:
        if credential_changed:
            if previous_password is None:
                keyring.delete_password(CREDENTIAL_SERVICE, account)
            else:
                keyring.set_password(CREDENTIAL_SERVICE, account, previous_password)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return settings_view(root)


def connection_url(root: Path) -> URL | str | None:
    if managed_failure(root):return None
    internal=managed_url(root)
    if internal is not None:return internal
    override = os.environ.get("SHIXUE_KB_DATABASE_URL")
    if override:
        return override
    settings = read_settings(root)
    if not settings.enabled:
        return None
    password = keyring.get_password(CREDENTIAL_SERVICE, credential_account(root, settings))
    return URL.create("postgresql+psycopg", username=settings.username, password=password,
                      host=settings.host, port=settings.port, database=settings.database)
