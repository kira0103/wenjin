"""Packaged lifecycle probe, restricted to explicitly marked test directories."""
import argparse
import json
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.knowledge.managed import ManagedPostgres


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    marker = root / '.managed-prototype'
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise RuntimeError('原型仅可使用独立测试目录，不操作已有资料目录')
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text('managed-prototype-v1', encoding='ascii')
    with ManagedPostgres(root, args.runtime) as manager:
        with manager.connect() as connection:
            version = connection.execute('SHOW server_version').fetchone()[0]
            extensions = dict(connection.execute("SELECT extname,extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm')"))
            connection.execute('CREATE TABLE IF NOT EXISTS probe(body text, embedding vector(3))')
            connection.execute('INSERT INTO probe VALUES(%s,%s)', ('打包中文𠀀原文', '[1,0,0]'))
            connection.execute('CREATE INDEX IF NOT EXISTS probe_hnsw ON probe USING hnsw(embedding vector_cosine_ops)')
            found = connection.execute("SELECT body FROM probe ORDER BY embedding <=> '[1,0,0]'::vector LIMIT 1").fetchone()[0]
            if found != '打包中文𠀀原文':
                raise RuntimeError('打包原型原文校验失败')
        report = {'postgres_version': version, 'extensions': extensions, 'chinese_path': True,
                  'hnsw': True, 'credentials_printed': False}
    report['clean_shutdown'] = not (root / 'knowledge-db/postmaster.pid').exists()
    if not report['clean_shutdown']:
        raise RuntimeError('打包原型未正常退出数据库')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('原型验证未完成，请查看独立测试目录中的数据库日志', file=sys.stderr)
        raise SystemExit(1)
