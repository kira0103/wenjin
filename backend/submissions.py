"""Local author workflow. Research literature remains authoritative in PostgreSQL."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

HEADERS = ('期刊名称', '学科门类', '期刊等级', '官网', '投稿地址')
SEED = Path(__file__).parent / 'data' / 'CSSCI_2025-2026.csv'
SEED_LABEL = 'CSSCI 2025–2026（用户提供目录）'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def uid():
    return uuid.uuid4().hex


def name_key(name):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', name)).strip().casefold()


def catalog_fingerprint(content):
    # Native text reads and the browser decoder differ on BOM / Windows newlines.
    normalized = content.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def url(value, allow_email=False):
    value = value.strip()
    if not value:
        return ''
    if allow_email and re.fullmatch(r'mailto:[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value):
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ValueError('链接须为完整的 http:// 或 https:// 地址，且不能包含账户信息')
    return value


def migrate(db, seed=True):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS submission_journals (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        discipline TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT '',
        website TEXT NOT NULL DEFAULT '', submission_url TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS submission_datasets (
        id TEXT PRIMARY KEY, label TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
        row_count INTEGER NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS submission_memberships (
        dataset_id TEXT NOT NULL REFERENCES submission_datasets(id),
        journal_id TEXT NOT NULL REFERENCES submission_journals(id),
        source_json TEXT NOT NULL, PRIMARY KEY(dataset_id,journal_id)
      );
      CREATE TABLE IF NOT EXISTS submission_papers (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '',
        discipline TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'medium',
        progress INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'writing',
        target_journal_id TEXT REFERENCES submission_journals(id), project_id TEXT,
        source_kind TEXT, source_id TEXT, source_title TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS submission_attempts (
        id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES submission_papers(id),
        journal_id TEXT NOT NULL REFERENCES submission_journals(id), journal_name TEXT NOT NULL,
        submitted_date TEXT NOT NULL, status TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 1,
        last_date TEXT NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS submission_events (
        id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES submission_papers(id),
        attempt_id TEXT REFERENCES submission_attempts(id), kind TEXT NOT NULL,
        event_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS submission_attempt_paper ON submission_attempts(paper_id,created_at);
      CREATE INDEX IF NOT EXISTS submission_event_paper ON submission_events(paper_id,created_at);
    ''')
    if seed and SEED.is_file():
        content = SEED.read_text(encoding='utf-8-sig')
        digest = catalog_fingerprint(content)
        if not db.execute('SELECT 1 FROM submission_datasets WHERE sha256=?', (digest,)).fetchone():
            import_catalog(db, ImportRequest(content=content, label=SEED_LABEL))


class JournalBody(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    discipline: str = Field(default='', max_length=120)
    level: str = Field(default='', max_length=80)
    website: str = Field(default='', max_length=2000)
    submission_url: str = Field(default='', max_length=2000)
    notes: str = Field(default='', max_length=12000)
    favorite: bool = False
    revision: int = Field(default=1, ge=1)

    @field_validator('name', 'discipline', 'level', 'notes')
    @classmethod
    def trim(cls, value):
        return value.strip()

    @field_validator('name')
    @classmethod
    def nonempty(cls, value):
        if not value:
            raise ValueError('期刊名称不能为空')
        return value

    @field_validator('website')
    @classmethod
    def links(cls, value):
        return url(value)

    @field_validator('submission_url')
    @classmethod
    def submission_link(cls, value):
        return url(value, allow_email=True)


class ImportRequest(BaseModel):
    content: str = Field(max_length=5_000_000)
    label: str = Field(default='导入期刊目录', min_length=1, max_length=120)
    fingerprint: str | None = None


def parse_catalog(content):
    digest = catalog_fingerprint(content)
    errors, rows, seen = [], [], {}
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff'), newline=''), strict=True)
        if not reader.fieldnames or set(reader.fieldnames) != set(HEADERS) or len(reader.fieldnames) != 5:
            return digest, [], ['表头须为：' + '、'.join(HEADERS)]
        for line, raw in enumerate(reader, 2):
            if line > 20001:
                errors.append('每次最多导入 20,000 行'); break
            try:
                if None in raw or any(v is None for v in raw.values()):
                    raise ValueError('列数与表头不一致')
                body = JournalBody(**dict(zip(('name', 'discipline', 'level', 'website', 'submission_url'),
                                               (raw[h] for h in HEADERS))))
                row = {k: getattr(body, k) for k in ('name', 'discipline', 'level', 'website', 'submission_url')}
                key = name_key(body.name)
                if key in seen:
                    if seen[key] != row:
                        raise ValueError('同名期刊存在互相冲突的信息，请合并后再导入')
                    continue
                seen[key] = row
                rows.append(row)
            except ValueError as error:
                errors.append(f'第 {line} 行：{error}')
    except csv.Error as error:
        errors.append(f'CSV 格式错误：{error}')
    if not rows and not errors:
        errors.append('目录中没有期刊')
    return digest, rows, errors


def catalog_names(db):
    names = {}
    for row in db.execute('SELECT id,name_key FROM submission_journals'):
        names.setdefault(row['name_key'], set()).add(row['id'])
    for row in db.execute('SELECT journal_id,source_json FROM submission_memberships'):
        names.setdefault(name_key(json.loads(row['source_json'])['name']), set()).add(row['journal_id'])
    return names


def preview(db, body):
    digest, rows, errors = parse_catalog(body.content)
    existing = catalog_names(db)
    errors.extend(f"期刊“{r['name']}”的名称与历史目录名称存在冲突，请先核对期刊卡片"
                  for r in rows if len(existing.get(name_key(r['name']), set())) > 1)
    matched = sum(name_key(r['name']) in existing for r in rows)
    return {'fingerprint': digest, 'label': body.label.strip(), 'rows': len(rows), 'new': len(rows)-matched,
            'existing': matched, 'errors': errors[:100], 'error_count': len(errors), 'sample': rows[:8],
            'already_imported': bool(db.execute('SELECT 1 FROM submission_datasets WHERE sha256=?', (digest,)).fetchone()),
            'policy': '已有期刊保留所有人工信息，只补全空白字段；原始目录信息另存为来源快照。'}


def import_catalog(db, body):
    report = preview(db, body)
    if body.fingerprint is not None and body.fingerprint != report['fingerprint']:
        raise HTTPException(409, '预览后文件发生变化，请重新预览')
    if report['errors']:
        raise HTTPException(422, {'message': '目录含错误，未导入任何行', 'errors': report['errors']})
    if report['already_imported']:
        return {**report, 'imported': 0}
    _, rows, _ = parse_catalog(body.content)
    names = catalog_names(db)
    dataset_id, timestamp = uid(), now()
    db.execute('INSERT INTO submission_datasets VALUES(?,?,?,?,?)',
               (dataset_id, body.label.strip() or '导入期刊目录', report['fingerprint'], len(rows), timestamp))
    for row in rows:
        matches = names.get(name_key(row['name']), set())
        existing = required(db, 'submission_journals', next(iter(matches))) if matches else None
        journal_id = existing['id'] if existing else uid()
        if not existing:
            db.execute('''INSERT INTO submission_journals(id,name,name_key,discipline,level,website,submission_url,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?)''', (journal_id, row['name'], name_key(row['name']), row['discipline'],
                          row['level'], row['website'], row['submission_url'], timestamp, timestamp))
        # Existing fields can be deliberately blank: never refill a manually edited record.
        elif existing['revision'] == 1:
            changes = {k: v for k, v in row.items() if k != 'name' and not existing[k] and v}
            if changes:
                db.execute('UPDATE submission_journals SET '+','.join(k+'=?' for k in changes)+', revision=revision+1, updated_at=? WHERE id=?',
                           (*changes.values(), timestamp, journal_id))
        db.execute('INSERT INTO submission_memberships VALUES(?,?,?)',
                   (dataset_id, journal_id, json.dumps(row, ensure_ascii=False)))
    return {**report, 'dataset_id': dataset_id, 'imported': len(rows)}


class PaperBody(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    authors: str = Field(default='', max_length=1000)
    discipline: str = Field(default='', max_length=120)
    priority: Literal['high', 'medium', 'low'] = 'medium'
    progress: int = Field(default=0, ge=0, le=100)
    status: Literal['writing', 'ready'] = 'writing'
    target_journal_id: str | None = None
    project_id: str | None = None
    source_kind: Literal['artifact', 'unit'] | None = None
    source_id: str | None = None
    notes: str = Field(default='', max_length=12000)
    archived: bool = False
    revision: int = Field(default=1, ge=1)

    @field_validator('title')
    @classmethod
    def title_required(cls, value):
        if not value.strip():
            raise ValueError('论文题目不能为空')
        return value.strip()


class ActionBody(BaseModel):
    action: Literal['submit', 'request_revision', 'revise', 'accept', 'reject', 'publish', 'withdraw']
    revision: int = Field(ge=1)
    date: date
    journal_id: str | None = None
    note: str = Field(default='', max_length=12000)


def required(db, table, identity):
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
    if not row:
        raise HTTPException(404, '记录不存在')
    return row


def check_revision(row, revision):
    if row['revision'] != revision:
        raise HTTPException(409, '记录已在其他窗口修改，请刷新后重试；本次修改尚未保存')


def event(db, paper_id, kind, day, note='', attempt_id=None, detail=None):
    db.execute('INSERT INTO submission_events VALUES(?,?,?,?,?,?,?,?)',
               (uid(), paper_id, attempt_id, kind, day, note, json.dumps(detail or {}, ensure_ascii=False), now()))


def validate_links(db, body, old=None):
    if body.target_journal_id:
        required(db, 'submission_journals', body.target_journal_id)
    source_title = old['source_title'] if old else ''
    # Keep historical associations readable even after a project/result was removed.
    unchanged = old and all(old[k] == getattr(body, k) for k in ('project_id', 'source_kind', 'source_id'))
    if not unchanged:
        if body.project_id:
            required(db, 'projects', body.project_id)
        if bool(body.source_kind) != bool(body.source_id):
            raise HTTPException(422, '成果来源类型与 ID 必须同时填写')
        if body.source_id:
            source = required(db, 'artifacts' if body.source_kind == 'artifact' else 'research_units', body.source_id)
            if source['project_id'] != body.project_id:
                raise HTTPException(422, '成果不属于所选项目')
            source_title = source['title']
        else:
            source_title = ''
    return source_title


def paper_detail(db, identity):
    paper = dict(required(db, 'submission_papers', identity))
    paper['attempts'] = [dict(r) for r in db.execute('SELECT * FROM submission_attempts WHERE paper_id=? ORDER BY created_at', (identity,))]
    paper['events'] = [{**dict(r), 'detail': json.loads(r['detail_json'])} for r in
                       db.execute('SELECT * FROM submission_events WHERE paper_id=? ORDER BY created_at', (identity,))]
    return paper


def register(app, connection):
    @app.get('/api/submissions/overview')
    def overview():
        with connection() as db:
            journals = [dict(r) for r in db.execute('SELECT * FROM submission_journals ORDER BY name')]
            datasets = [dict(r) for r in db.execute('SELECT * FROM submission_datasets ORDER BY created_at DESC')]
            papers = [dict(r) for r in db.execute('''SELECT p.*, a.journal_name current_journal_name,
                      (SELECT json_group_array(journal_id) FROM submission_attempts WHERE paper_id=p.id) journal_ids,
                      a.submitted_date, a.last_date, a.round FROM submission_papers p LEFT JOIN submission_attempts a
                      ON a.id=(SELECT id FROM submission_attempts WHERE paper_id=p.id ORDER BY created_at DESC LIMIT 1)
                      ORDER BY p.updated_at DESC''')]
            for paper in papers:
                paper['journal_ids'] = json.loads(paper['journal_ids'])
            return {'journals': journals, 'papers': papers, 'datasets': datasets}

    @app.get('/api/submissions/journals/{identity}')
    def journal_detail(identity: str):
        with connection() as db:
            return {**dict(required(db, 'submission_journals', identity)),
                    'sources': [{**dict(r), 'original': json.loads(r['source_json'])} for r in db.execute('''
                      SELECT d.label,d.created_at,m.source_json FROM submission_memberships m
                      JOIN submission_datasets d ON d.id=m.dataset_id WHERE m.journal_id=? ORDER BY d.created_at DESC''', (identity,))]}

    @app.post('/api/submissions/journals')
    def create_journal(body: JournalBody):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM submission_journals WHERE name_key=?', (name_key(body.name),)).fetchone():
                raise HTTPException(409, '同名期刊已存在，请编辑已有卡片')
            identity, timestamp = uid(), now()
            db.execute('''INSERT INTO submission_journals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (identity, body.name, name_key(body.name), body.discipline, body.level, body.website,
                        body.submission_url, body.notes, int(body.favorite), 2, timestamp, timestamp))
            return dict(required(db, 'submission_journals', identity))

    @app.put('/api/submissions/journals/{identity}')
    def edit_journal(identity: str, body: JournalBody):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = required(db, 'submission_journals', identity)
            check_revision(old, body.revision)
            collision = db.execute('SELECT id FROM submission_journals WHERE name_key=? AND id!=?', (name_key(body.name), identity)).fetchone()
            if collision:
                raise HTTPException(409, '同名期刊已存在，请使用不同名称')
            db.execute('''UPDATE submission_journals SET name=?,name_key=?,discipline=?,level=?,website=?,submission_url=?,
                          notes=?,favorite=?,revision=revision+1,updated_at=? WHERE id=?''',
                       (body.name, name_key(body.name), body.discipline, body.level, body.website, body.submission_url,
                        body.notes, int(body.favorite), now(), identity))
            return dict(required(db, 'submission_journals', identity))

    @app.post('/api/submissions/import/preview')
    def import_preview(body: ImportRequest):
        with connection() as db:
            return preview(db, body)

    @app.post('/api/submissions/import')
    def import_confirm(body: ImportRequest):
        if body.fingerprint is None:
            raise HTTPException(422, '请先预览目录')
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return import_catalog(db, body)

    @app.get('/api/submissions/papers/{identity}')
    def get_paper(identity: str):
        with connection() as db:
            return paper_detail(db, identity)

    @app.post('/api/submissions/papers')
    def create_paper(body: PaperBody):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            source_title = validate_links(db, body)
            identity, timestamp = uid(), now()
            db.execute('''INSERT INTO submission_papers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (identity, body.title, body.authors, body.discipline, body.priority,
                        100 if body.status == 'ready' else body.progress, body.status, body.target_journal_id,
                        body.project_id, body.source_kind, body.source_id, source_title, body.notes,
                        int(body.archived), 1, timestamp, timestamp))
            event(db, identity, 'created', date.today().isoformat(), detail={'source_title': source_title})
            return paper_detail(db, identity)

    @app.put('/api/submissions/papers/{identity}')
    def edit_paper(identity: str, body: PaperBody):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = required(db, 'submission_papers', identity)
            check_revision(old, body.revision)
            source_title = validate_links(db, body, old)
            status = body.status if old['status'] in {'writing', 'ready'} else old['status']
            db.execute('''UPDATE submission_papers SET title=?,authors=?,discipline=?,priority=?,progress=?,status=?,
                       target_journal_id=?,project_id=?,source_kind=?,source_id=?,source_title=?,notes=?,archived=?,
                       revision=revision+1,updated_at=? WHERE id=?''',
                       (body.title, body.authors, body.discipline, body.priority, 100 if status != 'writing' else body.progress,
                        status, body.target_journal_id, body.project_id, body.source_kind, body.source_id, source_title,
                        body.notes, int(body.archived), now(), identity))
            event(db, identity, 'edited', date.today().isoformat(), detail={'archived': body.archived})
            return paper_detail(db, identity)

    @app.post('/api/submissions/papers/{identity}/actions')
    def action(identity: str, body: ActionBody):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = required(db, 'submission_papers', identity)
            check_revision(old, body.revision)
            if old['archived']:
                raise HTTPException(409, '请先取消归档')
            allowed = {'submit': {'ready', 'rejected'}, 'request_revision': {'review'}, 'revise': {'revision'},
                       'accept': {'review', 'revision'}, 'reject': {'review', 'revision'}, 'publish': {'accepted'},
                       'withdraw': {'review', 'revision'}}
            if old['status'] not in allowed[body.action]:
                raise HTTPException(409, '当前阶段不能执行此操作，请刷新论文状态')
            day = body.date.isoformat()
            if body.date > date.today():
                raise HTTPException(422, '记录实际发生的日期，不能填写未来日期')
            attempt = db.execute('SELECT * FROM submission_attempts WHERE paper_id=? ORDER BY created_at DESC LIMIT 1', (identity,)).fetchone()
            if attempt and day < attempt['last_date']:
                raise HTTPException(422, '日期不能早于上一条投稿或审稿记录')
            if body.action == 'submit':
                journal = required(db, 'submission_journals', body.journal_id or old['target_journal_id'])
                attempt_id = uid()
                db.execute('INSERT INTO submission_attempts VALUES(?,?,?,?,?,?,?,?,?)',
                           (attempt_id, identity, journal['id'], journal['name'], day, 'review', 1, day, now()))
                status = 'review'
            else:
                if not attempt:
                    raise HTTPException(409, '缺少投稿记录')
                attempt_id = attempt['id']
                status = {'request_revision': 'revision', 'revise': 'review', 'accept': 'accepted', 'reject': 'rejected',
                          'publish': 'published', 'withdraw': 'ready'}[body.action]
                db.execute('UPDATE submission_attempts SET status=?,last_date=?,round=round+? WHERE id=?',
                           ('withdrawn' if body.action == 'withdraw' else status, day, int(body.action == 'revise'), attempt_id))
            db.execute('''UPDATE submission_papers SET status=?,progress=100,
                          target_journal_id=CASE WHEN ?='submit' THEN NULL ELSE target_journal_id END,
                          revision=revision+1,updated_at=? WHERE id=?''', (status, body.action, now(), identity))
            event(db, identity, body.action, day, body.note, attempt_id)
            return paper_detail(db, identity)
