"""Cross-account usage ledger aggregation for the portal admin console.

Phase 1 contract: every research workspace keeps its own SQLite ledger table
(``usage_events``).  The portal process only ever reads those databases through
SQLite's read-only URI mode; it never writes to a user workspace.

All dates in the public API are ``YYYY-MM-DD`` strings in Asia/Shanghai.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

TIMEZONE = 'Asia/Shanghai'
GRANULARITIES = ('day', 'week', 'month')
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
CURRENCY = 'CNY'

CSV_HEADER = ['账号', '用户名', '项目', '日期', '功能', '模型', '请求数', '未计量',
              '输入token', '输出token', '缓存token', '总token', '估算金额(CNY)', '来源']

_WORKSPACE_ID = re.compile(r'[A-Za-z0-9_-]{1,80}')
_INT_FIELDS = ('requests', 'measured_requests', 'unknown_requests', 'input_tokens',
               'output_tokens', 'cached_tokens', 'total_tokens', 'cost_unpriced_requests')

_AGGREGATE_SQL = """
SELECT local_date, project_id, feature, source_kind, model_id, model_name, provider_name, currency,
       COUNT(*) AS requests,
       SUM(CASE WHEN usage_status='measured' THEN 1 ELSE 0 END) AS measured_requests,
       SUM(CASE WHEN usage_status='unknown' THEN 1 ELSE 0 END) AS unknown_requests,
       SUM(COALESCE(input_tokens,0)) AS input_tokens,
       SUM(COALESCE(output_tokens,0)) AS output_tokens,
       SUM(COALESCE(cached_tokens,0)) AS cached_tokens,
       SUM(COALESCE(total_tokens, COALESCE(input_tokens,0)+COALESCE(output_tokens,0))) AS total_tokens,
       SUM(CASE WHEN cost_status='estimated' AND COALESCE(currency,'') IN ('','CNY')
                THEN COALESCE(estimated_cost,0) ELSE 0 END) AS estimated_cost,
       SUM(CASE WHEN cost_status!='estimated' THEN 1 ELSE 0 END) AS cost_unpriced_requests,
       COALESCE(MAX(created_at),'') AS last_at
FROM usage_events
WHERE local_date BETWEEN ? AND ?{extra}
GROUP BY local_date, project_id, feature, source_kind, model_id, model_name, provider_name, currency
"""


def users(accounts) -> list[dict]:
    """Portal accounts with the workspace id needed to locate each ledger."""
    with accounts.connection() as db:
        rows = db.execute('SELECT id,username,display_name,enabled,workspace_id '
                          'FROM portal_users ORDER BY created_at,id').fetchall()
    return [dict(row) for row in rows]


def _today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _parse_date(value, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f'{label}日期格式无效，应为 YYYY-MM-DD') from None


def resolve_range(from_date=None, to_date=None) -> tuple[date, date]:
    """Resolve optional strings/dates into an inclusive Shanghai-local range."""
    start = _parse_date(from_date, '开始') if from_date not in (None, '') else _today() - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    end = _parse_date(to_date, '结束') if to_date not in (None, '') else _today()
    if start > end:
        raise ValueError('开始日期不能晚于结束日期')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f'日期范围不能超过 {MAX_RANGE_DAYS} 天')
    return start, end


def validate_granularity(granularity: str) -> str:
    value = (granularity or 'day').strip()
    if value not in GRANULARITIES:
        raise ValueError('统计粒度无效，请使用 day、week 或 month')
    return value


def _bucket_start(day: date, granularity: str) -> date:
    if granularity == 'week':
        return day - timedelta(days=day.weekday())
    if granularity == 'month':
        return day.replace(day=1)
    return day


def _bucket_starts(start: date, end: date, granularity: str) -> list[date]:
    values: list[date] = []
    current = _bucket_start(start, granularity)
    while current <= end:
        values.append(current)
        if granularity == 'week':
            current += timedelta(days=7)
        elif granularity == 'month':
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            current += timedelta(days=1)
    return values


def _open_ledger(path: Path) -> sqlite3.Connection:
    uri = 'file:' + quote(path.resolve().as_posix(), safe='/:') + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=1')
        db.execute('PRAGMA busy_timeout=5000')
    except sqlite3.Error:
        db.close()
        raise
    return db


def _now() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec='seconds')


def _empty_aggregate() -> dict:
    value = {key: 0 for key in _INT_FIELDS}
    value['estimated_cost'] = 0.0
    value['last_at'] = ''
    return value


def _collect(root, accounts, *, start: date, end: date, account_id: str = '',
             project_id: str = '', feature: str = '', model_id: str = '') -> tuple[list[dict], dict]:
    """Read every selected workspace ledger once and return per-key aggregate rows."""
    root = Path(root).resolve()
    workspace_users = [value for value in users(accounts) if not account_id or value['id'] == account_id]
    clauses = ['local_date BETWEEN ? AND ?']
    parameters: list = [start.isoformat(), end.isoformat()]
    if project_id:
        clauses.append('project_id=?')
        parameters.append(project_id)
    if feature:
        clauses.append('feature=?')
        parameters.append(feature)
    if model_id:
        clauses.append('model_id=?')
        parameters.append(model_id)
    statement = _AGGREGATE_SQL.format(extra=' AND ' + ' AND '.join(clauses[1:]) if clauses[1:] else '')
    sources = {'workspaces_scanned': 0, 'workspaces_with_ledger': 0, 'generated_at': _now()}
    collected: dict[tuple, dict] = {}
    for user in workspace_users:
        workspace = str(user.get('workspace_id') or '')
        if not _WORKSPACE_ID.fullmatch(workspace):
            continue
        path = root / 'workspaces' / workspace / 'library.sqlite'
        if not path.is_file():
            continue
        try:
            db = _open_ledger(path)
        except sqlite3.Error:
            continue
        try:
            sources['workspaces_scanned'] += 1
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'").fetchone()
            if not exists:
                continue
            sources['workspaces_with_ledger'] += 1
            project_names: dict[str, str] = {}
            try:
                project_names = {str(row['id']): str(row['name']) for row in
                                 db.execute('SELECT id,name FROM projects').fetchall()}
            except sqlite3.Error:
                project_names = {}
            try:
                rows = db.execute(statement, parameters).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                key = (user['id'], row['local_date'], row['project_id'], row['feature'],
                       row['source_kind'], row['model_id'], row['model_name'],
                       row['provider_name'], row['currency'])
                value = collected.get(key)
                if value is None:
                    value = _empty_aggregate()
                    value.update(account_id=key[0], local_date=key[1], project_id=key[2],
                                 feature=key[3], source_kind=key[4], model_id=key[5],
                                 model_name=key[6], provider_name=key[7], currency=key[8],
                                 username=user['username'], display_name=user['display_name'],
                                 project_name=project_names.get(str(row['project_id']), ''))
                    collected[key] = value
                for name in _INT_FIELDS:
                    value[name] += int(row[name] or 0)
                value['estimated_cost'] += float(row['estimated_cost'] or 0)
                value['last_at'] = max(value['last_at'], row['last_at'] or '')
                value['project_name'] = project_names.get(str(row['project_id']), '')
        except sqlite3.Error:
            # A single corrupt or locked ledger must never fail the whole admin view.
            continue
        finally:
            db.close()
    return list(collected.values()), sources


def _round_cost(value) -> float:
    return round(float(value or 0), 6)


def summary(root, accounts, *, from_date, to_date, granularity='day', account_id='',
            project_id='', feature='', model_id='') -> dict:
    """Cross-account usage summary in the exact admin-console response shape."""
    start, end = resolve_range(from_date, to_date)
    granularity = validate_granularity(granularity)
    rows, sources = _collect(root, accounts, start=start, end=end, account_id=account_id or '',
                             project_id=project_id or '', feature=feature or '', model_id=model_id or '')
    totals = {key: 0 for key in _INT_FIELDS}
    totals['estimated_cost'] = 0.0
    totals['currency'] = CURRENCY
    bucket_values = {value: _empty_aggregate() for value in _bucket_starts(start, end, granularity)}
    by_feature: dict[str, dict] = {}
    by_model: dict[tuple, dict] = {}
    accounts_map: dict[str, dict] = {}
    projects_map: dict[tuple, dict] = {}
    for row in rows:
        requests = int(row['requests'])
        tokens = int(row['total_tokens'])
        cost = float(row['estimated_cost'])
        for name in _INT_FIELDS:
            totals[name] += int(row[name])
        totals['estimated_cost'] += float(row['estimated_cost'])
        bucket = bucket_values.get(_bucket_start(date.fromisoformat(row['local_date']), granularity))
        if bucket is not None:
            for name in _INT_FIELDS:
                bucket[name] += int(row[name])
            bucket['estimated_cost'] += float(row['estimated_cost'])
            bucket['last_at'] = max(bucket['last_at'], row['last_at'])
        feature_row = by_feature.setdefault(row['feature'], {'feature': row['feature'], 'requests': 0, 'total_tokens': 0, 'estimated_cost': 0.0})
        feature_row['requests'] += requests
        feature_row['total_tokens'] += tokens
        feature_row['estimated_cost'] += cost
        model_key = (row['model_id'], row['model_name'], row['provider_name'])
        model_row = by_model.setdefault(model_key, {'model_id': row['model_id'], 'model_name': row['model_name'],
                                                    'provider_name': row['provider_name'], 'requests': 0,
                                                    'total_tokens': 0, 'estimated_cost': 0.0})
        model_row['requests'] += requests
        model_row['total_tokens'] += tokens
        model_row['estimated_cost'] += cost
        account = accounts_map.setdefault(row['account_id'], {
            'account_id': row['account_id'], 'username': row['username'], 'display_name': row['display_name'],
            'requests': 0, 'total_tokens': 0, 'estimated_cost': 0.0, 'projects': {}})
        account['requests'] += requests
        account['total_tokens'] += tokens
        account['estimated_cost'] += cost
        project = account['projects'].setdefault(row['project_id'], {
            'project_id': row['project_id'], 'project_name': row['project_name'], 'requests': 0,
            'total_tokens': 0, 'estimated_cost': 0.0, 'last_at': ''})
        project['requests'] += requests
        project['total_tokens'] += tokens
        project['estimated_cost'] += cost
        project['last_at'] = max(project['last_at'], row['last_at'])
        flat = projects_map.setdefault((row['account_id'], row['project_id']), {
            'account_id': row['account_id'], 'username': row['username'], 'display_name': row['display_name'],
            'project_id': row['project_id'], 'project_name': row['project_name'], 'requests': 0,
            'unknown_requests': 0, 'total_tokens': 0, 'estimated_cost': 0.0, 'last_at': ''})
        flat['requests'] += requests
        flat['unknown_requests'] += int(row['unknown_requests'])
        flat['total_tokens'] += tokens
        flat['estimated_cost'] += cost
        flat['last_at'] = max(flat['last_at'], row['last_at'])
    totals['estimated_cost'] = _round_cost(totals['estimated_cost'])
    totals['accounts'] = len(accounts_map)
    totals['projects'] = len(projects_map)
    buckets = []
    for value in sorted(bucket_values):
        bucket = bucket_values[value]
        buckets.append({'date': value.isoformat(), 'requests': int(bucket['requests']),
                        'unknown_requests': int(bucket['unknown_requests']),
                        'input_tokens': int(bucket['input_tokens']), 'output_tokens': int(bucket['output_tokens']),
                        'cached_tokens': int(bucket['cached_tokens']), 'total_tokens': int(bucket['total_tokens']),
                        'estimated_cost': _round_cost(bucket['estimated_cost'])})
    feature_rows = [{key: (value if key != 'estimated_cost' else _round_cost(value)) for key, value in row.items()}
                    for row in by_feature.values()]
    feature_rows.sort(key=lambda row: (-row['total_tokens'], -row['requests'], row['feature']))
    model_rows = [{key: (value if key != 'estimated_cost' else _round_cost(value)) for key, value in row.items()}
                  for row in by_model.values()]
    model_rows.sort(key=lambda row: (-row['total_tokens'], row['model_name'], row['model_id']))
    account_rows = []
    for account in sorted(accounts_map.values(), key=lambda value: (value['username'], value['account_id'])):
        projects = list(account['projects'].values())
        for project in projects:
            project['estimated_cost'] = _round_cost(project['estimated_cost'])
        projects.sort(key=lambda value: (-value['total_tokens'], value['project_name'], value['project_id']))
        account_rows.append({'account_id': account['account_id'], 'username': account['username'],
                             'display_name': account['display_name'], 'requests': account['requests'],
                             'total_tokens': account['total_tokens'],
                             'estimated_cost': _round_cost(account['estimated_cost']), 'projects': projects})
    project_rows = [{key: (value if key != 'estimated_cost' else _round_cost(value)) for key, value in row.items()}
                    for row in projects_map.values()]
    project_rows.sort(key=lambda row: (-row['total_tokens'], row['username'], row['project_name'], row['project_id']))
    return {'range': {'from': start.isoformat(), 'to': end.isoformat(), 'granularity': granularity,
                      'timezone': TIMEZONE},
            'totals': totals, 'buckets': buckets, 'by_feature': feature_rows, 'by_model': model_rows,
            'accounts': account_rows, 'projects': project_rows, 'sources': sources}


def _export_line(row) -> list:
    return [row['username'], row['display_name'], row['project_name'] or row['project_id'],
            row['local_date'], row['feature'], row['model_name'] or row['model_id'],
            int(row['requests']), int(row['cost_unpriced_requests']),
            int(row['input_tokens']), int(row['output_tokens']), int(row['cached_tokens']),
            int(row['total_tokens']), _round_cost(row['estimated_cost']), row['source_kind']]


def export_rows(root, accounts, *, from_date, to_date, granularity='day', account_id='',
                project_id='', feature='', model_id='') -> list[list]:
    """One CSV row per account/date/project/feature/model/source aggregate."""
    start, end = resolve_range(from_date, to_date)
    validate_granularity(granularity)
    rows, _ = _collect(root, accounts, start=start, end=end, account_id=account_id or '',
                       project_id=project_id or '', feature=feature or '', model_id=model_id or '')
    values = sorted(rows, key=lambda row: (row['username'], row['local_date'], row['project_name'],
                                           row['project_id'], row['feature'], row['model_name'],
                                           row['model_id'], row['source_kind']))
    return [_export_line(row) for row in values]


def export_csv(root, accounts, *, from_date, to_date, granularity='day', account_id='',
               project_id='', feature='', model_id='') -> bytes:
    """UTF-8 BOM CSV body used by ``/api/admin/usage/export.csv``."""
    rows = export_rows(root, accounts, from_date=from_date, to_date=to_date, granularity=granularity,
                       account_id=account_id, project_id=project_id, feature=feature, model_id=model_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(CSV_HEADER)
    writer.writerows(rows)
    return '\ufeff'.encode('utf-8') + buffer.getvalue().encode('utf-8')
