"""Portable workspace identity, retaining the original key of existing data."""
import hashlib
import json
import os
import secrets
from pathlib import Path


def legacy_key(root):
    return hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()


def workspace_key(root):
    path = Path(root) / 'workspace-identity.json'
    if not path.exists():
        return legacy_key(root)
    value = json.loads(path.read_text(encoding='utf-8'))
    key = value.get('workspace_key', '')
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('工作区身份文件无效，请从备份恢复')
    return key


def ensure_identity(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'workspace-identity.json'
    if path.exists():
        return workspace_key(root)
    key = legacy_key(root)
    # Concurrent first starts derive the same legacy key; publishing is atomic.
    temporary = root / ('.workspace-identity-' + secrets.token_hex(8))
    try:
        temporary.write_text(json.dumps({'format': 1, 'workspace_key': key}), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return workspace_key(root)
