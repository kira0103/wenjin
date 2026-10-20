from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from sqlalchemy import URL

from .audit import audit_legacy
from .config import DatabaseSettings, SettingsUpdate, save_settings
from .database import database
from .migration import upgrade


def main():
    parser = argparse.ArgumentParser(description="文献库审计、显式结构升级和开发环境准备")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("--data-dir", type=Path, required=True)
    audit.add_argument("--output-dir", type=Path, required=True)
    commands.add_parser("upgrade", help="使用 SHIXUE_KB_MIGRATION_URL 显式升级")
    prepare = commands.add_parser("dev-prepare")
    prepare.add_argument("--secrets-dir", type=Path, default=Path(".local/knowledge"))
    settings = commands.add_parser("dev-configure")
    settings.add_argument("--data-dir", type=Path, required=True)
    settings.add_argument("--secrets-dir", type=Path, default=Path(".local/knowledge"))
    dev_upgrade = commands.add_parser("dev-upgrade", help="使用本项目开发环境迁移账号升级")
    dev_upgrade.add_argument("--secrets-dir", type=Path, default=Path(".local/knowledge"))
    restore=commands.add_parser('restore',help='恢复到不存在的资料目录及空数据库')
    restore.add_argument('--bundle',type=Path,required=True)
    restore.add_argument('--destination',type=Path,required=True)
    args = parser.parse_args()
    try:
        if args.command == "audit":
            report = audit_legacy(args.data_dir, args.output_dir)
            print(json.dumps({"restore_verified": report["restore_verified"],
                              "table_count": len(report["tables"]), "asset_files": report["asset_files"]}))
            return 0 if report["restore_verified"] else 1
        if args.command == "dev-prepare":
            args.secrets_dir.mkdir(parents=True, exist_ok=True)
            for name in ("admin_password", "owner_password", "app_password"):
                path = args.secrets_dir / name
                if not path.exists():
                    with path.open("x", encoding="utf-8") as file:
                        file.write(secrets.token_urlsafe(36))
            print("开发环境密码文件已准备，已有密码未覆盖")
        elif args.command == "dev-configure":
            password = (args.secrets_dir / "app_password").read_text(encoding="utf-8").strip()
            save_settings(args.data_dir, SettingsUpdate(**DatabaseSettings(enabled=True).model_dump(), password=password))
            print(json.dumps(database.health(args.data_dir), ensure_ascii=False))
        elif args.command == "dev-upgrade":
            password = (args.secrets_dir / "owner_password").read_text(encoding="utf-8").strip()
            url = URL.create("postgresql+psycopg", username="shixue_owner", password=password,
                             host="127.0.0.1", port=55432, database="shixue_knowledge")
            upgrade(url)
            print("开发文献库结构已升级")
        elif args.command=='restore':
            from .backup import restore_bundle
            url=os.environ.get('SHIXUE_KB_RESTORE_URL')
            if not url:raise ValueError('请设置目标数据库恢复账号连接')
            print(json.dumps(restore_bundle(args.bundle,args.destination,url),ensure_ascii=False))
        else:
            url = os.environ.get("SHIXUE_KB_MIGRATION_URL")
            if not url:
                raise ValueError("请通过 SHIXUE_KB_MIGRATION_URL 指定迁移账号连接")
            upgrade(url)
            print("文献库结构已升级")
        return 0
    except Exception:
        # Driver exceptions can contain connection details: never echo them.
        print("操作未完成，请检查参数、数据库服务、账号权限和目录；未输出连接密钥。", file=sys.stderr)
        return 1
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
