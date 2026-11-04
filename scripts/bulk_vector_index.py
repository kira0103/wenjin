"""Defer and rebuild pgvector HNSW indexes around bulk ingestion.

Incremental HNSW maintenance dominates large imports (each INSERT updates a
multi-GB graph). For a bulk pass it is far faster to:

  1. defer --dimensions N   (drop only that HNSW index; GIN keyword search stays)
  2. process documents in the admin UI
  3. rebuild --dimensions N (one parallel CREATE INDEX + ANALYZE)
  4. verify --dimensions N

Semantic search for the affected model is unavailable while the index is
missing; keyword and exact search keep working. Run during a maintenance
window, with background jobs stopped, and close other memory-heavy programs.

Usage (from the project root):
  .venv\\Scripts\\python.exe scripts/bulk_vector_index.py status
  .venv\\Scripts\\python.exe scripts/bulk_vector_index.py defer --dimensions 1024 --yes
  .venv\\Scripts\\python.exe scripts/bulk_vector_index.py rebuild --dimensions 1024 --yes
  .venv\\Scripts\\python.exe scripts/bulk_vector_index.py verify --dimensions 1024

The same commands are available through npm:
  npm run vector-index -- status
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

DIMENSIONS = (384, 512, 768, 1024, 1536, 2048, 3072)
ROOT = Path(__file__).resolve().parents[1]
INDEX = 'kb_chunks_vectors_{}'
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, OSError):
    pass


def portal_directory(value=None):
    if value:
        return Path(value)
    launcher = ROOT / '.local' / 'backend-launcher.json'
    if launcher.is_file():
        try:
            data = json.loads(launcher.read_text(encoding='utf-8')).get('data_dir')
            if data:
                return Path(data)
        except (OSError, ValueError):
            pass
    return ROOT / '.local' / 'portal-preview'


def connection_settings(args):
    url = args.database_url or os.environ.get('SHIXUE_KB_MAINTENANCE_URL') or os.environ.get('SHIXUE_KB_DATABASE_URL')
    if not url:
        portal = portal_directory(args.data_dir)
        for candidate in sorted((portal / 'private' / 'runtimes').glob('*/shared-database.json')):
            try:
                value = json.loads(candidate.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            url = value.get('owner_url') or value.get('runtime_url')
            if url:
                break
    if not url:
        raise SystemExit('找不到数据库连接：请传 --database-url，或设置 SHIXUE_KB_MAINTENANCE_URL。')
    parsed = make_url(url)
    return {'host': parsed.host or '127.0.0.1', 'port': parsed.port or 5432,
            'dbname': parsed.database, 'user': parsed.username, 'password': parsed.password}


def connect(args):
    return psycopg.connect(**connection_settings(args), connect_timeout=5, autocommit=True,
                           options='-c statement_timeout=15000')


def index_sql(dimensions, concurrent=False):
    kind = 'vector' if dimensions <= 2000 else 'halfvec'
    unique = ' CONCURRENTLY' if concurrent else ''
    return (f'CREATE INDEX{unique} {INDEX.format(dimensions)} ON public.kb_chunks '
            f'USING hnsw (((embedding)::{kind}({dimensions})) {kind}_cosine_ops) '
            f'WHERE vector_dims(embedding) = {dimensions}')


def index_state(connection, dimensions):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT i.indisvalid, c.relpages, c.reltuples,
                   pg_size_pretty(pg_relation_size(c.oid)) AS size
            FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.oid = to_regclass(%s)""", (f'public.{INDEX.format(dimensions)}',))
        row = cur.fetchone()
    if not row:
        return None
    return {'valid': row[0], 'pages': row[1], 'tuples': row[2], 'size': row[3]}


def pending_jobs(connection):
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM kb_jobs WHERE state IN ('queued','running')")
        return cur.fetchone()[0]


def command_status(args):
    with connect(args) as connection:
        with connection.cursor() as cur:
            cur.execute("""SELECT name FROM pg_settings WHERE name IN
                ('shared_buffers','effective_cache_size','maintenance_work_mem','work_mem',
                 'max_wal_size','checkpoint_timeout','wal_compression','max_parallel_maintenance_workers')""")
            names = [row[0] for row in cur.fetchall()]
            settings = {}
            for name in names:
                cur.execute(f'SHOW {name}')
                settings[name] = cur.fetchone()[0]
            cur.execute("""SELECT c.reltuples, c.relpages,
                pg_size_pretty(pg_total_relation_size('public.kb_chunks'))
                FROM pg_class c WHERE c.oid = 'public.kb_chunks'::regclass""")
            tuples, _pages, table_size = cur.fetchone()
        print(f'Database : {connection.info.dbname}')
        print(f'Chunks   : ~{int(max(tuples, 0))} rows (estimated), {table_size}')
        print(f'Jobs     : {pending_jobs(connection)} queued/running')
        for dimensions in DIMENSIONS:
            state = index_state(connection, dimensions)
            if state:
                print(f'Index {dimensions:>4}d: {state["size"]}, valid={state["valid"]}, tuples~{int(max(state["tuples"], 0))}')
            else:
                print(f'Index {dimensions:>4}d: absent')
        print('Settings : ' + ', '.join(f'{key}={value}' for key, value in settings.items()))


def require_confirmation(args, action):
    if args.yes:
        return
    raise SystemExit(f'{action} 会修改生产数据库。确认后请加 --yes 再执行。')


def command_defer(args):
    require_confirmation(args, 'defer')
    with connect(args) as connection:
        state = index_state(connection, args.dimensions)
        if not state:
            print(f'{args.dimensions} 维 HNSW 索引不存在，无需 defer。')
            return
        jobs = pending_jobs(connection)
        if jobs and not args.force:
            raise SystemExit(f'仍有 {jobs} 个排队/运行中的任务。请先停止或等完成；确认要强行继续请加 --force。')
        print(f'将删除 {state["size"]} 的 {args.dimensions} 维 HNSW 索引；语义检索在重建前不可用，关键词/原句检索仍可用。')
        with connection.cursor() as cur:
            cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX.format(args.dimensions)}')
    print('已 defer。现在可以在管理端继续加工/全库入库；完成后运行 rebuild。')


def command_rebuild(args):
    require_confirmation(args, 'rebuild')
    with connect(args) as connection:
        state = index_state(connection, args.dimensions)
        if state and not args.replace:
            raise SystemExit(f'{args.dimensions} 维 HNSW 索引已存在（valid={state["valid"]}）。如需重建请加 --replace。')
        if state:
            with connection.cursor() as cur:
                cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX.format(args.dimensions)}')
        sql = index_sql(args.dimensions, concurrent=args.concurrent)
        print(f'开始重建 {args.dimensions} 维 HNSW：{sql}')
        with connection.cursor() as cur:
            cur.execute('SET statement_timeout = 0')
            cur.execute(f"SET maintenance_work_mem = '{args.maintenance_memory}'")
            cur.execute(f'SET max_parallel_maintenance_workers = {args.workers}')
            cur.execute(sql)
            if not args.no_analyze:
                cur.execute('ANALYZE public.kb_chunks')
        state = index_state(connection, args.dimensions)
        if not state or not state['valid']:
            raise SystemExit('重建失败：索引不存在或无效。请检查日志后重试 rebuild。')
        print(f'重建完成：{state["size"]}，tuples~{state["tuples"]}。建议随后运行 verify。')


def ingestion_pending(connection):
    with connection.cursor() as cur:
        cur.execute("""SELECT count(*) FROM kb_jobs
                       WHERE state IN ('queued','running') AND kind IN ('import','index')""")
        jobs = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM kb_jobs
                       WHERE kind='rag_batch' AND state IN ('queued','dispatching')""")
        batches = cur.fetchone()[0]
    return jobs + batches


def command_watch(args):
    """Wait for the bulk ingestion queue to drain, then rebuild the index."""
    require_confirmation(args, 'watch')
    with connect(args) as connection:
        if index_state(connection, args.dimensions):
            raise SystemExit(f'{args.dimensions} 维 HNSW 索引仍然存在；请先 defer，再开始批量入库。')
        print('等待入库队列清空；完成后自动重建索引。按 Ctrl+C 可中止等待（索引不会自动重建）。')
        started = time.monotonic()
        idle = 0
        seen_work = False
        while True:
            pending = ingestion_pending(connection)
            if pending:
                idle = 0
                seen_work = True
                print(f'仍有 {pending} 个任务/批次待处理……')
            else:
                # Do not rebuild before the first queued job appears: the user
                # may start watch just before submitting the batch.
                idle += 1 if seen_work else 0
                if idle >= args.idle_checks:
                    break
            if time.monotonic() - started > args.max_hours * 3600:
                raise SystemExit('等待超时，未重建索引；请稍后手动运行 rebuild。')
            time.sleep(args.interval)
        print('队列已清空，开始重建索引。')
    command_rebuild(args)


def command_verify(args):
    with connect(args) as connection:
        state = index_state(connection, args.dimensions)
        if not state:
            print(f'{args.dimensions} 维 HNSW 索引不存在。')
            raise SystemExit(1)
        print(f'索引：{INDEX.format(args.dimensions)}，大小 {state["size"]}，valid={state["valid"]}，tuples~{state["tuples"]}')
        with connection.cursor() as cur:
            cur.execute("""SELECT c.embedding::text FROM kb_chunks c
                WHERE c.index_id = (SELECT id FROM kb_indexes
                                    WHERE config->>'dimensions' = %s AND state='ready' LIMIT 1)
                  AND vector_dims(c.embedding) = %s LIMIT 1""", (str(args.dimensions), args.dimensions))
            row = cur.fetchone()
            if not row:
                print('没有可用于计划验证的样本向量（该维度没有已发布索引）。')
                return
            cur.execute(f"""EXPLAIN (COSTS OFF)
                SELECT c.id FROM kb_chunks c
                WHERE c.embedding IS NOT NULL AND vector_dims(c.embedding) = {args.dimensions}
                ORDER BY c.embedding::{('vector' if args.dimensions <= 2000 else 'halfvec')}({args.dimensions}) <=> CAST(%s AS {('vector' if args.dimensions <= 2000 else 'halfvec')}({args.dimensions}))
                LIMIT 50""", (row[0],))
            plan = '\n'.join(item[0] for item in cur.fetchall())
        name = INDEX.format(args.dimensions)
        for line in plan.splitlines():
            print(line[:160] + ('...' if len(line) > 160 else ''))
        print('计划命中索引：' + ('是' if name in plan else '否（请检查检索 SQL 或统计信息）'))


def main():
    parser = argparse.ArgumentParser(description='Defer/rebuild pgvector HNSW indexes for bulk ingestion.')
    parser.add_argument('command', choices=('status', 'defer', 'watch', 'rebuild', 'verify'))
    parser.add_argument('--dimensions', type=int, choices=DIMENSIONS, default=512)
    parser.add_argument('--data-dir', help='portal data directory (defaults to remembered launcher directory)')
    parser.add_argument('--database-url', help='maintenance connection URL (defaults to the portal shared library owner)')
    parser.add_argument('--maintenance-memory', default='1GB', help='maintenance_work_mem for rebuild (default 1GB)')
    parser.add_argument('--workers', type=int, default=4, help='parallel maintenance workers (default 4)')
    parser.add_argument('--concurrent', action='store_true', help='build with CREATE INDEX CONCURRENTLY (no parallel workers)')
    parser.add_argument('--replace', action='store_true', help='drop an existing index before rebuilding')
    parser.add_argument('--no-analyze', action='store_true', help='skip ANALYZE after rebuilding (for quick checks)')
    parser.add_argument('--interval', type=int, default=30, help='seconds between queue checks for watch (default 30)')
    parser.add_argument('--idle-checks', type=int, default=3, help='consecutive empty checks before rebuilding (default 3)')
    parser.add_argument('--max-hours', type=float, default=18.0, help='give up waiting after this many hours (default 18)')
    parser.add_argument('--force', action='store_true', help='defer even when jobs are queued/running')
    parser.add_argument('--yes', action='store_true', help='confirm a mutating action')
    args = parser.parse_args()
    {'status': command_status, 'defer': command_defer, 'watch': command_watch,
     'rebuild': command_rebuild, 'verify': command_verify}[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except psycopg.Error as error:
        print(f'数据库操作失败：{error}', file=sys.stderr)
        raise SystemExit(1)
