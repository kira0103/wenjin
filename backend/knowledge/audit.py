"""Read-only legacy inventory and independent SQLite snapshot restore check."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_only(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def fingerprint(connection) -> dict:
    tables = {}
    for name, sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = list(connection.execute("PRAGMA table_info(" + quoted(name) + ")"))
        digest = hashlib.sha256()
        ordered = ",".join(quoted(column[1]) for column in columns)
        count = 0
        for row in connection.execute("SELECT * FROM " + quoted(name) + " ORDER BY " + ordered):
            values = [value.hex() if isinstance(value, bytes) else value for value in row]
            digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        tables[name] = {"rows": count, "columns": [column[1] for column in columns],
                        "schema_sha256": hashlib.sha256((sql or "").encode()).hexdigest(),
                        "data_sha256": digest.hexdigest()}
    return tables


def audit_legacy(root: Path, output: Path) -> dict:
    source = root.resolve() / "library.sqlite"
    output = output.resolve()
    if not source.is_file():
        raise ValueError("资料目录中没有 library.sqlite")
    if output == root.resolve() or root.resolve() in output.parents:
        raise ValueError("审计输出必须位于原资料目录以外")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "legacy-snapshot.sqlite"
    with closing(read_only(source)) as original, closing(sqlite3.connect(snapshot)) as target:
        original.backup(target)
    restored = output / "restore-check.sqlite"
    shutil.copyfile(snapshot, restored)
    with closing(read_only(snapshot)) as before, closing(read_only(restored)) as after:
        inventory = fingerprint(before)
        recovered = fingerprint(after)
        integrity = [row[0] for row in after.execute("PRAGMA integrity_check")]
        report = {"kind": "legacy-database-only-audit", "tables": inventory,
                  "restore_verified": inventory == recovered and integrity == ["ok"],
                  "integrity": integrity, "asset_files": {"total": 0, "missing": 0, "digest_mismatch": 0,
                                                          "external_paths": 0, "invalid_digest": 0}}
        report["orphan_counts"] = {}
        relationships = [
            ("pages", "material_id", "materials", "id"),
            ("library_materials", "material_id", "materials", "id"),
            ("library_materials", "entry_id", "bibliography", "id"),
            ("project_materials", "project_id", "projects", "id"),
            ("project_materials", "material_id", "materials", "id"),
            ("project_bibliography", "project_id", "projects", "id"),
            ("project_bibliography", "entry_id", "bibliography", "id"),
            ("unit_versions", "unit_id", "research_units", "id"),
        ]
        for child, child_key, parent, parent_key in relationships:
            if child in inventory and parent in inventory:
                if child_key not in inventory[child]["columns"] or parent_key not in inventory[parent]["columns"]:
                    continue
                query = ("SELECT count(*) FROM " + quoted(child) + " c LEFT JOIN " + quoted(parent)
                         + " p ON c." + quoted(child_key) + "=p." + quoted(parent_key)
                         + " WHERE p." + quoted(parent_key) + " IS NULL")
                report["orphan_counts"][child + "." + child_key] = before.execute(query).fetchone()[0]
        if "materials" in inventory:
            for asset_path, expected in before.execute("SELECT asset_path,sha256 FROM materials"):
                counts = report["asset_files"]
                counts["total"] += 1
                path = Path(asset_path)
                if not path.is_absolute():
                    path = root / path
                resolved = path.resolve()
                if root.resolve() not in resolved.parents:
                    counts["external_paths"] += 1
                    continue
                if not resolved.is_file():
                    counts["missing"] += 1
                    continue
                if len(expected or "") != 64 or any(c not in "0123456789abcdef" for c in (expected or "")):
                    counts["invalid_digest"] += 1
                    continue
                with resolved.open("rb") as file:
                    actual = hashlib.file_digest(file, "sha256").hexdigest()
                if actual != expected:
                    counts["digest_mismatch"] += 1
        report["note"] = "已核验数据库快照恢复；原件仅核查哈希与缺失，不在本次导出中，不构成完整文献库备份。未输出正文、文件名或密钥。"
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
