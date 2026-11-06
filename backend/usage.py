"""Workspace AI usage ledger.

Only token counts, prices and small metadata are persisted; prompt text and
model responses are never written here. Every caller wraps recording in
try/except so a ledger problem can never break the main business flow.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None

TIMEZONE = 'Asia/Shanghai'

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT '',
  feature TEXT NOT NULL,
  source_kind TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  request_key TEXT NOT NULL DEFAULT '',
  model_id TEXT NOT NULL DEFAULT '',
  model_name TEXT NOT NULL DEFAULT '',
  provider_name TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  total_tokens INTEGER,
  usage_status TEXT NOT NULL DEFAULT 'measured',
  latency_ms INTEGER,
  price_input_per_million REAL,
  price_output_per_million REAL,
  price_cached_per_million REAL,
  currency TEXT NOT NULL DEFAULT '',
  price_date TEXT NOT NULL DEFAULT '',
  estimated_cost REAL,
  cost_status TEXT NOT NULL DEFAULT 'unpriced',
  created_at TEXT NOT NULL,
  local_date TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_project_date ON usage_events(project_id, local_date);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage_events(local_date);
CREATE INDEX IF NOT EXISTS idx_usage_feature ON usage_events(feature, local_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_dedupe ON usage_events(source_kind, source_id, request_key);
"""

COLUMNS = ('id','project_id','feature','source_kind','source_id','request_key','model_id','model_name',
           'provider_name','model','input_tokens','output_tokens','cached_tokens','total_tokens','usage_status',
           'latency_ms','price_input_per_million','price_output_per_million','price_cached_per_million',
           'currency','price_date','estimated_cost','cost_status','created_at','local_date','meta_json')

INSERT = f'INSERT OR IGNORE INTO usage_events({",".join(COLUMNS)}) VALUES({",".join("?" for _ in COLUMNS)})'


def _zone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE)
        except Exception:
            pass
    from datetime import timedelta
    return timezone(timedelta(hours=8))


LOCAL_ZONE = _zone()


def ensure_schema(db) -> None:
    """Create the ledger table and indexes in a workspace SQLite database."""
    db.executescript(SCHEMA)


def _count(value):
    """Non-negative integer token/latency count, or None for missing/invalid input."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _price(value):
    """Non-negative per-million price as float, or None when not configured."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _text(value, limit=500):
    if value is None:
        return ''
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)[:limit]
    return ''


def _first_text(mapping, *keys):
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ''


def _timestamp(value=None):
    """UTC ISO timestamp plus its Asia/Shanghai local date."""
    moment = None
    if isinstance(value, datetime):
        moment = value
    elif value not in (None, ''):
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            moment = None
    if moment is None:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.isoformat(timespec='seconds'), moment.astimezone(LOCAL_ZONE).date().isoformat()


def local_date(value=None) -> str:
    """Asia/Shanghai YYYY-MM-DD for an ISO timestamp (defaults to now)."""
    return _timestamp(value)[1]


def normalize_usage(usage) -> dict:
    """Normalize a provider usage object into input/output/cached/total/status/reasoning."""
    value = usage if isinstance(usage, dict) else {}

    def count(*keys):
        for key in keys:
            number = _count(value.get(key))
            if number is not None:
                return number
        return None

    input_tokens = count('prompt_tokens', 'input_tokens')
    output_tokens = count('completion_tokens', 'output_tokens')
    total_tokens = count('total_tokens')
    cached_tokens = None
    for group in ('prompt_tokens_details', 'input_tokens_details'):
        details = value.get(group)
        if isinstance(details, dict):
            cached_tokens = _count(details.get('cached_tokens'))
            if cached_tokens is not None:
                break
    if cached_tokens is None:
        cached_tokens = count('cache_read_input_tokens')
    reasoning_tokens = None
    for group in ('completion_tokens_details', 'output_tokens_details'):
        details = value.get(group)
        if isinstance(details, dict):
            reasoning_tokens = _count(details.get('reasoning_tokens'))
            if reasoning_tokens is not None:
                break
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if all(number is None for number in (input_tokens, output_tokens, cached_tokens, total_tokens)):
        status = 'unknown'
    elif input_tokens is not None and output_tokens is not None:
        status = 'measured'
    else:
        status = 'partial'
    return {'input': input_tokens, 'output': output_tokens, 'cached': cached_tokens,
            'total': total_tokens, 'status': status, 'reasoning': reasoning_tokens}


def estimate_cost(tokens, settings):
    """Return (estimated_cost, cost_status) for normalized tokens and model settings."""
    tokens = tokens if isinstance(tokens, dict) else {}
    if not any(key in tokens for key in ('input', 'output', 'cached', 'total')):
        # Also accept a raw provider usage object for convenience.
        tokens = normalize_usage(tokens)
    settings = settings if isinstance(settings, dict) else {}
    price_input = _price(settings.get('price_input_per_million'))
    price_output = _price(settings.get('price_output_per_million'))
    if price_input is None or price_output is None:
        return None, 'unpriced'
    input_tokens = _count(tokens.get('input'))
    output_tokens = _count(tokens.get('output'))
    if input_tokens is None or output_tokens is None:
        return None, 'unknown'
    cached_tokens = _count(tokens.get('cached')) or 0
    price_cached = _price(settings.get('price_cached_per_million'))
    if cached_tokens and price_cached is None:
        return None, 'unknown'
    cost = ((input_tokens - cached_tokens) * price_input
            + cached_tokens * (price_cached or 0.0)
            + output_tokens * price_output) / 1_000_000
    return cost, 'estimated'


def _meta_json(meta) -> str:
    """Keep only small scalar metadata; content-like nested values are dropped."""
    if not isinstance(meta, dict):
        return '{}'
    cleaned = {}
    for key, value in meta.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:200]
    try:
        return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return '{}'


def _write(db, *, project_id, feature, source_kind, source_id, request_key,
           settings, usage, latency_ms, meta, created_at=None):
    settings = settings if isinstance(settings, dict) else {}
    feature = _text(feature, 80)
    project_id = _text(project_id, 120)
    source_kind = _text(source_kind, 80)
    source_id = _text(source_id, 200)
    request_key = _text(request_key, 200)
    if not feature:
        feature = 'other'
    event_id = hashlib.sha256(
        f'{feature}|{source_kind}|{source_id}|{request_key}'.encode('utf-8')).hexdigest()[:24]
    tokens = normalize_usage(usage)
    estimated_cost, cost_status = estimate_cost(tokens, settings)
    created_at, local_date = _timestamp(created_at)
    values = (
        event_id, project_id, feature, source_kind, source_id, request_key,
        _first_text(settings, 'model_id', 'id'), _first_text(settings, 'model_name', 'name'),
        _text(settings.get('provider_name')), _text(settings.get('model')),
        tokens['input'], tokens['output'], tokens['cached'], tokens['total'], tokens['status'],
        _count(latency_ms),
        _price(settings.get('price_input_per_million')),
        _price(settings.get('price_output_per_million')),
        _price(settings.get('price_cached_per_million')),
        _text(settings.get('currency'), 20), _text(settings.get('price_date'), 40),
        estimated_cost, cost_status, created_at, local_date, _meta_json(meta),
    )
    cursor = db.execute(INSERT, values)
    return event_id if cursor.rowcount else None


def record(db, *, project_id, feature, source_kind='', source_id='', request_key='',
           settings=None, usage=None, latency_ms=None, meta=None):
    """Persist one usage event; returns the event id, or None when it is a duplicate.

    Database errors still propagate: callers wrap this call in try/except so that
    an accounting failure never affects the main business flow.
    """
    return _write(db, project_id=project_id, feature=feature, source_kind=source_kind,
                  source_id=source_id, request_key=request_key, settings=settings,
                  usage=usage, latency_ms=latency_ms, meta=meta)


def backfill_collation(db) -> int:
    """Backfill collation attempts that predate the ledger. Idempotent."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collation_attempts'").fetchone():
        return 0
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collation_batches'").fetchone():
        return 0
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'").fetchone():
        ensure_schema(db)
    inserted = 0
    rows = db.execute('''SELECT a.id,a.batch_id,a.input_tokens,a.output_tokens,a.total_tokens,a.usage_json,
      b.project_id,b.model_json,b.created_at FROM collation_attempts a
      JOIN collation_batches b ON b.id=a.batch_id ORDER BY a.rowid''')
    for attempt_id, batch_id, input_tokens, output_tokens, total_tokens, usage_json, project_id, model_json, created_at in rows:
        try:
            raw = json.loads(usage_json) if usage_json else None
        except (TypeError, ValueError):
            raw = None
        raw = dict(raw) if isinstance(raw, dict) else {}
        # Receipt columns remain authoritative when usage_json lacks a count.
        if normalize_usage(raw)['input'] is None and _count(input_tokens) is not None:
            raw['input_tokens'] = input_tokens
        if normalize_usage(raw)['output'] is None and _count(output_tokens) is not None:
            raw['output_tokens'] = output_tokens
        if normalize_usage(raw)['total'] is None and _count(total_tokens) is not None:
            raw['total_tokens'] = total_tokens
        try:
            settings = json.loads(model_json) if model_json else None
        except (TypeError, ValueError):
            settings = None
        event_id = _write(db, project_id=project_id, feature='collation_batch',
                          source_kind='batch_backfill', source_id=batch_id, request_key=attempt_id,
                          settings=settings if isinstance(settings, dict) else {}, usage=raw,
                          latency_ms=None, meta=None, created_at=created_at)
        if event_id:
            inserted += 1
    return inserted
