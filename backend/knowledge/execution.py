"""Immutable execution inputs kept beside the existing SQLite task engine."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from fastapi import HTTPException

from . import repository as r
from .project_sources import ProjectSourceService, normalized_ranges, range_key


def digest(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def migrate(db):
    for table, column, definition in [('materials', 'kind', "TEXT NOT NULL DEFAULT 'catalog'"),
                                      ('tasks', 'working_input_id', 'TEXT')]:
        if column not in {row['name'] for row in db.execute('PRAGMA table_info(' + table + ')')}:
            db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + definition)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS working_inputs (
        id TEXT PRIMARY KEY, material_id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL,
        kind TEXT NOT NULL, edition_id TEXT, revision_id TEXT, source_key TEXT NOT NULL,
        ranges_json TEXT NOT NULL, range_format INTEGER NOT NULL DEFAULT 1, range_digest TEXT NOT NULL,
        body TEXT NOT NULL, body_digest TEXT NOT NULL, positions_json TEXT NOT NULL,
        source_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(project_id,kind,source_key,range_digest),
        CHECK(kind IN ('catalog_range','manual_text','unit_selection'))
      );
      CREATE TRIGGER IF NOT EXISTS working_inputs_immutable_update BEFORE UPDATE ON working_inputs
        BEGIN SELECT RAISE(ABORT,'working input is immutable'); END;
      CREATE TABLE IF NOT EXISTS project_source_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT ''
      );
      CREATE TRIGGER IF NOT EXISTS project_source_state AFTER UPDATE OF archived,deleted ON projects
        WHEN OLD.archived!=NEW.archived OR OLD.deleted!=NEW.deleted
        BEGIN INSERT INTO project_source_events(project_id) VALUES(NEW.id); END;
    ''')


def activated(db):
    value = db.execute("SELECT setting_value FROM app_settings WHERE setting_key='catalog-activation'").fetchone()
    return bool(value and json.loads(value[0]).get('state') == 'activated')


def root_for(db):
    path = next(row['file'] for row in db.execute('PRAGMA database_list') if row['name'] == 'main')
    return Path(path).resolve().parent


class WorkingInputService:
    def __init__(self, root: Path, local_connection, now):
        self.root = root
        self.local_connection = local_connection
        self.now = now
        self.projects = ProjectSourceService(root, local_connection)

    def _publish(self, project_id, kind, source_key, body, positions, ranges, source,
                 edition_id=None, revision_id=None, db=None):
        scope_digest = digest(r.j({'format': 1, 'ranges': ranges}))
        def write(local):
            existing = local.execute('SELECT * FROM working_inputs WHERE project_id=? AND kind=? '
                                     'AND source_key=? AND range_digest=?', (project_id, kind, source_key, scope_digest)).fetchone()
            if existing:
                if existing['body_digest'] != digest(body):
                    raise HTTPException(409, '相同来源修订的输入不一致，未覆盖已有快照')
                return self._value(existing)
            identifier, material = str(uuid.uuid4()), str(uuid.uuid4())
            # This is a virtual execution handle, not a new file or bibliographic item.
            # Its legacy hash is an identity key; the actual text hash lives in working_inputs.
            local.execute('INSERT INTO materials '
                          '(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at,kind) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (material, project_id, '固定输入.txt', source.get('title', '固定输入'), digest(identifier),
                           '', max(1, len(positions)), 1, self.now(), 'working_input'))
            local.execute('INSERT INTO working_inputs '
                          '(id,material_id,project_id,kind,edition_id,revision_id,source_key,ranges_json,range_digest,'
                          'body,body_digest,positions_json,source_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (identifier, material, project_id, kind, str(edition_id) if edition_id else None,
                           str(revision_id) if revision_id else None, source_key, r.j(ranges), scope_digest,
                           body, digest(body), r.j(positions), r.j(source), self.now()))
            # Compatibility pages reconstruct the exact snapshot by simple concatenation.
            previous = 0
            pages = []
            for number, position in enumerate(positions, 1):
                end = position['snapshot_end']
                pages.append((material, number, body[previous:end]))
                previous = end
            if not pages:
                pages = [(material, 1, body)]
            elif previous < len(body):
                pages[-1] = (material, pages[-1][1], pages[-1][2] + body[previous:])
            local.executemany('INSERT INTO pages(material_id,page_number,text) VALUES(?,?,?)', pages)
            copy = ''.join(row[0] for row in local.execute('SELECT text FROM pages WHERE material_id=? ORDER BY page_number', (material,)))
            if digest(copy) != digest(body):
                raise HTTPException(409, '固定输入副本校验失败')
            return self._value(local.execute('SELECT * FROM working_inputs WHERE id=?', (identifier,)).fetchone())
        if db is not None:
            return write(db)
        with self.local_connection() as local:
            local.execute('BEGIN IMMEDIATE')
            return write(local)

    @staticmethod
    def _value(row):
        value = dict(row)
        for column, name in [('ranges_json', 'ranges'), ('positions_json', 'positions'), ('source_json', 'source')]:
            value[name] = json.loads(value.pop(column))
        return value

    def catalog(self, project_id, edition_id, ranges=None, *, db=None):
        self.projects.project(project_id, writable=True)
        with r.database.transaction(self.root) as connection:
            edition = r.edition(connection, edition_id)
            revision = edition['active_revision']
            if not revision:
                raise HTTPException(409, '此版本尚无已发布文本')
            files = r.rows(connection, 'SELECT ea.asset_id,ea.filename,ea.position_kind,ea.ordinal,max(p.page_index) maximum '
                           'FROM kb_edition_assets ea JOIN kb_pages p ON p.asset_id=ea.asset_id WHERE ea.edition_id=:e '
                           'GROUP BY ea.asset_id,ea.filename,ea.position_kind,ea.ordinal ORDER BY ea.ordinal,ea.asset_id', e=edition_id)
            selected = normalized_ranges(connection, edition_id, ranges if ranges is not None else
                                         [{'asset_id': str(file['asset_id']), 'page_from': 1, 'page_to': file['maximum']} for file in files])
            blocks = r.rows(connection, 'SELECT * FROM kb_blocks WHERE revision_id=:id ORDER BY ordinal', id=revision)
        if not self.projects.allows(project_id, edition_id, selected):
            raise HTTPException(409, '所选原文不在当前项目有效资料范围内')
        by_page = {}
        for block in blocks:
            by_page.setdefault((str(block['asset_id']), block['page_index']), []).append(block)
        body, positions = '', []
        formats = {str(file['asset_id']): file['position_kind'] for file in files}
        for span in selected:
            for page in range(span['page_from'], span['page_to'] + 1):
                items = by_page.get((span['asset_id'], page), [])
                if not items:
                    positions.append({'number': len(positions) + 1, 'snapshot_start': len(body), 'snapshot_end': len(body),
                                      'asset_id': span['asset_id'], 'page_index': page, 'block_id': None,
                                      'source_block_start': 0, 'source_block_end': 0, 'missing': True})
                for index, block in enumerate(items):
                    separator = '\n\n' if body and (index > 0 or formats[span['asset_id']] == 'page') else ''
                    body += separator
                    start = len(body)
                    source_body=block['body'][:block['quality']['primary_length']] if block['quality'].get('source')=='core-markdown' else block['body']
                    body += source_body
                    positions.append({'number': len(positions) + 1, 'snapshot_start': start, 'snapshot_end': len(body),
                                      'asset_id': span['asset_id'], 'page_index': page, 'block_id': str(block['id']),
                                      'source_block_start': 0, 'source_block_end': len(source_body), 'missing': False})
        if not body.strip():
            raise HTTPException(409, '所选页段无录文，请先补充原文')
        return self._publish(project_id, 'catalog_range', str(edition_id) + ':' + str(revision), body,
                             positions, selected, {'title': edition['title'], 'edition_label': edition['label'],
                             'position_kind': 'section', 'coordinate_system': 'unicode-codepoints',
                             'separator_rule': 'paragraph-v1'}, edition_id, revision, db=db)

    def text(self, project_id, body, *, kind='manual_text', source=None, db=None):
        # SQLite-only inputs remain usable when the document database is unavailable.
        def check(local):
            project = local.execute('SELECT archived FROM projects WHERE id=? AND deleted=0', (project_id,)).fetchone()
            if not project or project['archived']:
                raise HTTPException(409, '请在活动项目中建立任务输入')
        if db is not None:
            check(db)
        else:
            with self.local_connection() as local:
                check(local)
        if kind not in {'manual_text', 'unit_selection'} or not body.strip():
            raise HTTPException(400, '任务输入为空或类型无效')
        source = source or {'title': '用户提供原文'}
        source_key = digest(r.j(source) + '\n' + body)
        return self._publish(project_id, kind, source_key, body, [], [], source, db=db)

    def get(self, identifier, project_id=None):
        with self.local_connection() as local:
            row = local.execute('SELECT * FROM working_inputs WHERE id=?', (identifier,)).fetchone()
            if not row or project_id is not None and row['project_id'] != project_id:
                raise HTTPException(404, '找不到当前项目的固定输入')
            value = self._value(row)
            if digest(value['body']) != value['body_digest']:
                raise HTTPException(409, '固定输入校验失败，未执行任务')
            return value
