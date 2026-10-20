"""Library/project relationships and durable, human-reviewed research drafts."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

ROLES = {'base', 'witness', 'citation', 'reference', 'auxiliary'}


def migrate(db, root: Path, now):
    existing = db.execute("SELECT 1 FROM sqlite_master WHERE name='project_materials'").fetchone()
    if not existing and db.execute('SELECT 1 FROM materials LIMIT 1').fetchone():
        folder = root / 'backups'
        folder.mkdir(exist_ok=True)
        backup = folder / 'before-0.2.0.sqlite'
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as destination:
                db.backup(destination)
    for table, columns in {
        'projects': {'project_type': "TEXT NOT NULL DEFAULT 'general'", 'structure_kind': "TEXT NOT NULL DEFAULT 'custom'", 'rules': "TEXT NOT NULL DEFAULT ''", 'archived': 'INTEGER NOT NULL DEFAULT 0', 'deleted': 'INTEGER NOT NULL DEFAULT 0'},
        'bibliography': {'item_type': "TEXT NOT NULL DEFAULT 'other'", 'version_label': "TEXT NOT NULL DEFAULT ''", 'genre': "TEXT NOT NULL DEFAULT ''"},
    }.items():
        known = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in known:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS library_materials (
        material_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, file_format TEXT NOT NULL DEFAULT 'pdf',
        position_kind TEXT NOT NULL DEFAULT 'page', encoding TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS project_materials (
        project_id TEXT NOT NULL, material_id TEXT NOT NULL, roles_json TEXT NOT NULL DEFAULT '["reference"]',
        witness_label TEXT NOT NULL DEFAULT '', scope_note TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(project_id,material_id)
      );
      CREATE TABLE IF NOT EXISTS project_bibliography (
        project_id TEXT NOT NULL, entry_id TEXT NOT NULL, roles_json TEXT NOT NULL DEFAULT '["reference"]',
        PRIMARY KEY(project_id,entry_id)
      );
      CREATE TABLE IF NOT EXISTS research_units (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
        volume TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', material_id TEXT NOT NULL,
        page_from INTEGER NOT NULL, page_to INTEGER NOT NULL, location_label TEXT NOT NULL DEFAULT '',
        original_text TEXT NOT NULL, content TEXT NOT NULL, notes_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'working', revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS unit_versions (
        id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
        notes_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(unit_id,revision)
      );
      CREATE INDEX IF NOT EXISTS idx_project_link ON project_materials(project_id);
      CREATE INDEX IF NOT EXISTS idx_library_entry ON library_materials(entry_id);
      CREATE INDEX IF NOT EXISTS idx_units_project ON research_units(project_id,updated_at);
    ''')
    if not db.execute("SELECT 1 FROM app_settings WHERE setting_key='schema-0.2.0'").fetchone():
        # Stable material IDs preserve historical tasks, reference occurrences,
        # reading state and provenance. Never merge editions merely by title.
        for material in db.execute('SELECT * FROM materials').fetchall():
            entry_id = secrets.token_hex(12)
            db.execute('INSERT INTO bibliography(id,title,raw_reference,authors,year,source_material_id,created_at) VALUES(?,?,?,?,?,?,?)',
                       (entry_id, material['title'], '', '', '', None, material['created_at']))
            db.execute('INSERT OR IGNORE INTO library_materials(material_id,entry_id) VALUES(?,?)', (material['id'], entry_id))
            db.execute('INSERT OR IGNORE INTO project_materials(project_id,material_id) VALUES(?,?)', (material['project_id'], material['id']))
        db.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES('schema-0.2.0',?)", (now(),))


def attached(db, project_id: str, material_id: str, *, historical=False):
    try:
        from backend.knowledge.execution import activated
        from backend.knowledge.compat import attached as unified_attached,snapshot
    except ModuleNotFoundError:
        from knowledge.execution import activated
        from knowledge.compat import attached as unified_attached,snapshot
    if activated(db) or snapshot(db,material_id):
        return unified_attached(db,project_id,material_id,historical=historical)
    return db.execute('SELECT 1 FROM project_materials WHERE project_id=? AND material_id=?', (project_id, material_id)).fetchone() is not None


def material_info(db, material_id: str, project_id: str | None = None, *, historical=False):
    try:
        from backend.knowledge.execution import activated
        from backend.knowledge.compat import material_info as unified_info,snapshot,snapshot_info
    except ModuleNotFoundError:
        from knowledge.execution import activated
        from knowledge.compat import material_info as unified_info,snapshot,snapshot_info
    frozen=snapshot(db,material_id)
    if frozen:return snapshot_info(frozen)
    if activated(db) and not historical:
        try:return unified_info(db,material_id,project_id)
        except Exception:
            # Old work keeps its local original text and metadata during outages.
            if not db.execute("SELECT 1 FROM materials WHERE id=? AND kind='legacy_handle'",(material_id,)).fetchone():raise
    row = db.execute('''SELECT m.*,lm.entry_id,lm.file_format,lm.position_kind,lm.encoding,
      b.item_type,b.version_label,b.genre,b.authors,b.year FROM materials m
      LEFT JOIN library_materials lm ON lm.material_id=m.id LEFT JOIN bibliography b ON b.id=lm.entry_id WHERE m.id=?''', (material_id,)).fetchone()
    if not row:
        raise HTTPException(404, '找不到这份材料')
    value = dict(row)
    value.pop('asset_path', None)
    value.pop('sha256', None)
    links = [dict(r) for r in db.execute('SELECT * FROM project_materials WHERE material_id=?', (material_id,))]
    value.update({'project_ids': [r['project_id'] for r in links], 'roles': ['reference'], 'witness_label': '', 'scope_note': '', 'file_format': value.get('file_format') or 'pdf', 'position_kind': value.get('position_kind') or 'page'})
    if project_id:
        link = next((r for r in links if r['project_id'] == project_id), None)
        value['project_id'] = project_id
        if link:
            value.update(roles=json.loads(link['roles_json']), witness_label=link['witness_label'], scope_note=link['scope_note'])
    value['reference_count'] = db.execute('SELECT count(*) FROM references_found WHERE material_id=?', (material_id,)).fetchone()[0]
    return value


def materials_for(db, project_id: str | None = None, query: str = ''):
    try:
        from backend.knowledge.execution import activated
        from backend.knowledge.compat import catalog_materials
    except ModuleNotFoundError:
        from knowledge.execution import activated
        from knowledge.compat import catalog_materials
    if activated(db):
        values=catalog_materials(db,project_id)
        return [item for item in values if query.lower().strip() in (item['title']+' '+item['filename']).lower()]
    if project_id:
        ids = db.execute('SELECT material_id FROM project_materials WHERE project_id=? ORDER BY rowid DESC', (project_id,)).fetchall()
    else:
        ids = db.execute("SELECT id material_id FROM materials WHERE kind<>'working_input' ORDER BY created_at DESC,rowid DESC").fetchall()
    result = [material_info(db, row['material_id'], project_id) for row in ids]
    term = query.strip().lower()
    return [m for m in result if not term or term in (m['title'] + ' ' + m['filename']).lower()]


class ProjectConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    project_type: Literal['general', 'collation', 'literature', 'translation'] = 'general'
    rules: str = Field(default='', max_length=6000)


class LinkConfig(BaseModel):
    roles: list[Literal['base', 'witness', 'citation', 'reference', 'auxiliary']] = Field(default=['reference'], min_length=1, max_length=5)
    witness_label: str = Field(default='', max_length=60)
    scope_note: str = Field(default='', max_length=300)


class EntryConfig(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    authors: str = Field(default='', max_length=300)
    year: str = Field(default='', max_length=20)
    raw_reference: str = Field(default='', max_length=2500)
    item_type: Literal['ancient', 'article', 'thesis', 'book', 'archive', 'other'] = 'other'
    version_label: str = Field(default='', max_length=300)
    genre: str = Field(default='', max_length=100)
    version: int | None = None


class UnitCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal['collation', 'translation', 'reading'] = 'collation'
    volume: str = Field(default='', max_length=100)
    category: str = Field(default='', max_length=100)
    material_id: str
    page_from: int = Field(default=1, ge=1)
    page_to: int = Field(default=1, ge=1)
    location_label: str = Field(default='', max_length=200)
    source_text: str = Field(default='', max_length=100000)


class EditorialNote(BaseModel):
    id: str = Field(max_length=80)
    kind: Literal['variant', 'citation', 'question', 'ai']
    location: str = Field(default='', max_length=300)
    base_reading: str = Field(default='', max_length=3000)
    witness_id: str = Field(default='', max_length=80)
    witness_reading: str = Field(default='', max_length=3000)
    decision: str = Field(default='', max_length=3000)
    evidence: str = Field(default='', max_length=3000)
    status: Literal['pending', 'accepted', 'rejected'] = 'pending'


class UnitSave(BaseModel):
    expected_revision: int = Field(ge=1)
    content: str = Field(max_length=10000000)
    notes: list[EditorialNote] = Field(default=[], max_length=20000)
    status: Literal['working', 'review', 'approved'] = 'working'
    confirm_review: bool = False


def baseline_for(db, row):
    if 'baseline_text' in row.keys() and row['baseline_text'] is not None:
        return row['baseline_text']
    provenance = json.loads(row['preprocess_json']) if 'preprocess_json' in row.keys() else {}
    if provenance.get('run_id'):
        saved = db.execute('SELECT * FROM preprocess_runs WHERE id=?', (provenance['run_id'],)).fetchone()
        if saved:
            try:
                from backend.preprocess_engine import Recipe, prepared, formatted
            except ModuleNotFoundError:
                from preprocess_engine import Recipe, prepared, formatted
            return formatted(prepared(saved['source_text'], json.loads(saved['result_json'])['edits'],
                                     provenance['source_start'], provenance['source_end']), Recipe.model_validate_json(saved['recipe_json']))
    return row['original_text']


def unit_info(row, detail=False):
    value = dict(row)
    if 'notes_json' in value:
        value['notes'] = json.loads(value.pop('notes_json'))
    if 'preprocess_json' in value:
        value['preprocess'] = json.loads(value.pop('preprocess_json'))
    if not detail:
        value.pop('content', None)
        value.pop('original_text', None)
        value.pop('baseline_text', None)
    return value


def units_for(db, project_id: str):
    return [unit_info(row) for row in db.execute('SELECT * FROM research_units WHERE project_id=? ORDER BY created_at,rowid', (project_id,))]


def register(app, connection, now):
    def project_exists(db, project_id):
        if not db.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0', (project_id,)).fetchone():
            raise HTTPException(404, '找不到项目')

    @app.put('/api/projects/{project_id}')
    def configure_project(project_id: str, payload: ProjectConfig):
        if not payload.name.strip():
            raise HTTPException(400, '请输入项目名称')
        with connection() as db:
            project_exists(db, project_id)
            db.execute('UPDATE projects SET name=?,description=?,project_type=?,rules=? WHERE id=?', (payload.name.strip(), payload.description, payload.project_type, payload.rules, project_id))
            return dict(db.execute('SELECT * FROM projects WHERE id=? AND deleted=0', (project_id,)).fetchone())

    @app.get('/api/global-materials')
    def global_materials():
        with connection() as db:
            return materials_for(db)

    @app.post('/api/projects/{project_id}/archive')
    def archive_project(project_id: str):
        with connection() as db:
            project_exists(db, project_id)
            if db.execute('SELECT count(*) FROM projects WHERE deleted=0 AND archived=0 AND id!=?', (project_id,)).fetchone()[0] == 0:
                raise HTTPException(400, '请保留至少一个活动项目；可以先创建新项目')
            db.execute('UPDATE projects SET archived=1 WHERE id=?', (project_id,))
        return {'archived': True, 'data_retained': True}

    @app.post('/api/projects/{project_id}/restore')
    def restore_project(project_id: str):
        with connection() as db:
            project_exists(db, project_id)
            db.execute('UPDATE projects SET archived=0 WHERE id=?', (project_id,))
        return {'archived': False}

    @app.get('/api/library-items')
    def entries():
        with connection() as db:
            try:
                from backend.knowledge import compat
            except ModuleNotFoundError:
                from knowledge import compat
            if compat.activated(db):return compat.books(db)
            result = []
            for row in db.execute('SELECT * FROM bibliography ORDER BY created_at DESC,rowid DESC').fetchall():
                item = dict(row)
                item['material_ids'] = [r[0] for r in db.execute('SELECT material_id FROM library_materials WHERE entry_id=?', (row['id'],))]
                item['project_ids'] = sorted({r[0] for r in db.execute('SELECT DISTINCT pm.project_id FROM project_materials pm JOIN library_materials lm ON lm.material_id=pm.material_id WHERE lm.entry_id=? UNION SELECT project_id FROM project_bibliography WHERE entry_id=?', (row['id'], row['id']))})
                item['project_roles'] = {r['project_id']: json.loads(r['roles_json']) for r in db.execute('SELECT project_id,roles_json FROM project_bibliography WHERE entry_id=?', (row['id'],))}
                for link in db.execute('SELECT pm.project_id,pm.roles_json FROM project_materials pm JOIN library_materials lm ON lm.material_id=pm.material_id WHERE lm.entry_id=?', (row['id'],)):
                    item['project_roles'][link['project_id']] = sorted(set(item['project_roles'].get(link['project_id'], []) + json.loads(link['roles_json'])))
                result.append(item)
            return result

    @app.post('/api/library-items')
    def create_entry(payload: EntryConfig):
        if not payload.title.strip():
            raise HTTPException(400, '请输入题名')
        entry_id = secrets.token_hex(12)
        with connection() as db:
            try:
                from backend.knowledge.execution import activated,root_for
                from backend.knowledge.catalog import CatalogService
            except ModuleNotFoundError:
                from knowledge.execution import activated,root_for
                from knowledge.catalog import CatalogService
            if activated(db):
                created=CatalogService(root_for(db)).create({'title':payload.title,'authors':[payload.authors] if payload.authors else [],
                    'date_label':payload.year,'label':payload.version_label,'source_note':payload.raw_reference,'item_type':payload.item_type})
                return {'id':str(created['id'])}
            db.execute('INSERT INTO bibliography(id,title,authors,year,raw_reference,item_type,version_label,genre,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (entry_id, payload.title.strip(), payload.authors, payload.year, payload.raw_reference, payload.item_type, payload.version_label, payload.genre, now()))
        return {'id': entry_id}

    @app.put('/api/library-items/{entry_id}')
    def update_entry(entry_id: str, payload: EntryConfig):
        if not payload.title.strip():
            raise HTTPException(400, '请输入题名')
        with connection() as db:
            try:
                from backend.knowledge.execution import activated
            except ModuleNotFoundError:
                from knowledge.execution import activated
            if activated(db):
                try:
                    from backend.knowledge.catalog import CatalogService
                    from backend.knowledge import repository as r
                    from backend.knowledge.execution import root_for
                except ModuleNotFoundError:
                    from knowledge.catalog import CatalogService
                    from knowledge import repository as r
                    from knowledge.execution import root_for
                with r.database.transaction(root_for(db)) as pg:current=r.edition(pg,entry_id)
                if payload.title!=current['title'] or payload.authors!='、'.join(current['authors_json']) or payload.year!=current['date_label'] or payload.item_type!=current['item_type']:
                    raise HTTPException(409,'作品公共信息请在文献库的“作品信息”中修改')
                CatalogService(root_for(db)).update_edition(entry_id,{'label':payload.version_label,'publisher':current['publisher'],
                    'publication_year':current['publication_year'],'doi':current['doi'],'source_note':payload.raw_reference,'tags':current['tags']},payload.version or current['version'])
                return {'id':entry_id}
            try:
                from backend.knowledge.legacy import guard_entry
            except ModuleNotFoundError:
                from knowledge.legacy import guard_entry
            guard_entry(db,entry_id)
            if not db.execute('SELECT 1 FROM bibliography WHERE id=?', (entry_id,)).fetchone():
                raise HTTPException(404, '找不到资料条目')
            db.execute('UPDATE bibliography SET title=?,authors=?,year=?,raw_reference=?,item_type=?,version_label=?,genre=? WHERE id=?', (payload.title.strip(), payload.authors, payload.year, payload.raw_reference, payload.item_type, payload.version_label, payload.genre, entry_id))
            db.execute("UPDATE references_found SET match_status='书目已修改 · 版本待核' WHERE match_id=?", (entry_id,))
        return {'id': entry_id}

    @app.put('/api/projects/{project_id}/materials/{material_id}')
    def link_material(project_id: str, material_id: str, payload: LinkConfig):
        with connection() as db:
            project_exists(db, project_id)
            try:
                from backend.knowledge import compat
            except ModuleNotFoundError:
                from knowledge import compat
            if compat.activated(db):
                edition,asset=compat.resolve(db,material_id)
                info=compat.material_info(db,material_id)
                compat.services(db)[0].put(project_id,edition,{**payload.model_dump(),'mode':'ranges',
                    'ranges':[{'asset_id':asset,'page_from':1,'page_to':info['page_count']}]})
                return compat.material_info(db,material_id,project_id)
            material_info(db, material_id)
            db.execute('INSERT INTO project_materials(project_id,material_id,roles_json,witness_label,scope_note) VALUES(?,?,?,?,?) ON CONFLICT(project_id,material_id) DO UPDATE SET roles_json=excluded.roles_json,witness_label=excluded.witness_label,scope_note=excluded.scope_note', (project_id, material_id, json.dumps(list(dict.fromkeys(payload.roles))), payload.witness_label, payload.scope_note))
            return material_info(db, material_id, project_id)

    @app.delete('/api/projects/{project_id}/materials/{material_id}')
    def unlink_material(project_id: str, material_id: str):
        with connection() as db:
            project_exists(db, project_id)
            try:
                from backend.knowledge import compat
            except ModuleNotFoundError:
                from knowledge import compat
            if compat.activated(db):
                edition,asset=compat.resolve(db,material_id);projects=compat.services(db)[0]
                for link in projects.list(project_id):
                    if str(link['edition_id'])!=edition:continue
                    if link['mode']=='edition':raise HTTPException(409,'项目选用了整个版本，请调整版本关联范围后再移除分册')
                    if any(span['asset_id']==asset for span in link['ranges']):
                        remaining=[span for span in link['ranges'] if span['asset_id']!=asset]
                        if remaining:projects.put(project_id,edition,{**link,'mode':'ranges','ranges':remaining})
                        projects.remove(project_id,link['id'])
                return {'removed':True,'original_retained':True}
            db.execute('DELETE FROM project_materials WHERE project_id=? AND material_id=?', (project_id, material_id))
        return {'removed': True, 'original_retained': True}

    @app.put('/api/projects/{project_id}/library-items/{entry_id}')
    def link_entry(project_id: str, entry_id: str, payload: LinkConfig):
        with connection() as db:
            project_exists(db, project_id)
            try:
                from backend.knowledge import compat
            except ModuleNotFoundError:
                from knowledge import compat
            if compat.activated(db):
                compat.services(db)[0].put(project_id,entry_id,payload.model_dump())
                return {'linked':True}
            if not db.execute('SELECT 1 FROM bibliography WHERE id=?', (entry_id,)).fetchone():
                raise HTTPException(404, '找不到资料条目')
            db.execute('INSERT INTO project_bibliography(project_id,entry_id,roles_json) VALUES(?,?,?) ON CONFLICT(project_id,entry_id) DO UPDATE SET roles_json=excluded.roles_json', (project_id, entry_id, json.dumps(payload.roles)))
            for row in db.execute('SELECT material_id FROM library_materials WHERE entry_id=?', (entry_id,)).fetchall():
                db.execute('INSERT INTO project_materials(project_id,material_id,roles_json,witness_label,scope_note) VALUES(?,?,?,?,?) ON CONFLICT(project_id,material_id) DO UPDATE SET roles_json=excluded.roles_json,witness_label=excluded.witness_label,scope_note=excluded.scope_note', (project_id, row[0], json.dumps(payload.roles), payload.witness_label, payload.scope_note))
        return {'linked': True}

    @app.delete('/api/projects/{project_id}/library-items/{entry_id}')
    def unlink_entry(project_id: str, entry_id: str):
        with connection() as db:
            project_exists(db, project_id)
            try:
                from backend.knowledge import compat
            except ModuleNotFoundError:
                from knowledge import compat
            if compat.activated(db):
                projects=compat.services(db)[0]
                for link in projects.list(project_id):
                    if str(link['edition_id'])==entry_id:projects.remove(project_id,link['id'])
                return {'removed':True,'original_retained':True}
            db.execute('DELETE FROM project_bibliography WHERE project_id=? AND entry_id=?', (project_id, entry_id))
            db.execute('DELETE FROM project_materials WHERE project_id=? AND material_id IN (SELECT material_id FROM library_materials WHERE entry_id=?)', (project_id, entry_id))
        return {'removed': True, 'original_retained': True}

    @app.get('/api/projects/{project_id}/units')
    def list_units(project_id: str):
        with connection() as db:
            project_exists(db, project_id)
            return units_for(db, project_id)

    @app.post('/api/projects/{project_id}/units')
    def create_unit(project_id: str, payload: UnitCreate):
        if not payload.title.strip():
            raise HTTPException(400, '请输入工作单元名称')
        with connection() as db:
            project_exists(db, project_id)
            if not attached(db, project_id, payload.material_id):
                raise HTTPException(400, '请先将底本或原文材料加入当前项目')
            material = material_info(db, payload.material_id, project_id)
            try:
                from backend.knowledge.execution import activated
                from backend.knowledge.compat import freeze,snapshot,services
            except ModuleNotFoundError:
                from knowledge.execution import activated
                from knowledge.compat import freeze,snapshot,services
            unified=activated(db) or snapshot(db,payload.material_id)
            if unified:
                if payload.source_text:
                    frozen=services(db)[1].text(project_id,payload.source_text,source={'title':material['title'],'original_material_id':payload.material_id},db=db)
                else:
                    frozen=freeze(db,project_id,payload.material_id,payload.page_from,payload.page_to)
                payload.material_id=frozen['material_id'];payload.page_from=1;payload.page_to=max(1,len(frozen['positions']))
                material=material_info(db,payload.material_id,project_id)
            if payload.page_from > payload.page_to or payload.page_to > material['page_count']:
                raise HTTPException(400, '原文范围无效')
            original = frozen['body'] if unified else payload.source_text or '\n\n'.join(r['text'] for r in db.execute('SELECT text FROM pages WHERE material_id=? AND page_number BETWEEN ? AND ? ORDER BY page_number', (payload.material_id, payload.page_from, payload.page_to)))
            if len(original) > 100000:
                raise HTTPException(400, '工作单元原文超过 100,000 字，请缩小范围')
            unit_id, timestamp = secrets.token_hex(12), now()
            content = original if payload.kind == 'collation' else ''
            db.execute('INSERT INTO research_units(id,project_id,kind,title,volume,category,material_id,page_from,page_to,location_label,original_text,content,notes_json,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (unit_id, project_id, payload.kind, payload.title.strip(), payload.volume, payload.category, payload.material_id, payload.page_from, payload.page_to, payload.location_label, original, content, '[]', 'working', 1, timestamp, timestamp))
            # Snapshot includes draft and decisions; original_text stays immutable.
            db.execute('INSERT INTO unit_versions(id,unit_id,revision,content,notes_json,status,created_at) VALUES(?,?,?,?,?,?,?)', (secrets.token_hex(12), unit_id, 1, content, '[]', 'working', timestamp))
            db.execute('UPDATE research_units SET baseline_text=original_text WHERE id=? AND baseline_text IS NULL', (unit_id,))
            return unit_info(db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone(), True)

    @app.get('/api/units/{unit_id}')
    def get_unit(unit_id: str):
        with connection() as db:
            row = db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone()
            if not row:
                raise HTTPException(404, '找不到研究工作')
            result = unit_info(row, True)
            result['versions'] = [dict(r) for r in db.execute('SELECT id,revision,status,created_at FROM unit_versions WHERE unit_id=? ORDER BY revision DESC', (unit_id,))]
            result['tasks'] = [r['id'] for r in db.execute('SELECT id,input_json FROM tasks WHERE project_id=? ORDER BY created_at DESC', (row['project_id'],)) if json.loads(r['input_json']).get('unit_id') == unit_id]
            return result

    @app.put('/api/units/{unit_id}')
    def save_unit(unit_id: str, payload: UnitSave):
        with connection() as db:
            row = db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone()
            if not row:
                raise HTTPException(404, '找不到研究工作')
            if row['revision'] != payload.expected_revision:
                raise HTTPException(409, '工作稿已在其他窗口更新。请导出当前草稿后重新打开，避免覆盖。')
            if len({note.id for note in payload.notes}) != len(payload.notes):
                raise HTTPException(400, '复核记录编号重复')
            historical_witnesses = {note.get('witness_id') for note in json.loads(row['notes_json'])}
            for note in payload.notes:
                if note.witness_id and note.witness_id not in historical_witnesses and not attached(db, row['project_id'], note.witness_id):
                    raise HTTPException(400, '校记使用的参校材料未加入当前项目')
            changed = row['content'] != payload.content or json.loads(row['notes_json']) != [n.model_dump() for n in payload.notes]
            status = payload.status
            if status == 'approved':
                if not payload.confirm_review:
                    status = 'review' if changed or row['status'] != 'approved' else 'approved'
                elif not payload.content.strip() or any(n.status == 'pending' for n in payload.notes):
                    raise HTTPException(400, '请先完成工作稿并处理所有待核记录，再审定')
            notes = json.dumps([n.model_dump() for n in payload.notes], ensure_ascii=False)
            revision, timestamp = row['revision'] + 1, now()
            cursor = db.execute('UPDATE research_units SET content=?,notes_json=?,status=?,revision=?,updated_at=? WHERE id=? AND revision=?', (payload.content, notes, status, revision, timestamp, unit_id, payload.expected_revision))
            if not cursor.rowcount:
                raise HTTPException(409, '工作稿已更新，请重新打开')
            db.execute('INSERT INTO unit_versions(id,unit_id,revision,content,notes_json,status,created_at) VALUES(?,?,?,?,?,?,?)', (secrets.token_hex(12), unit_id, revision, payload.content, notes, status, timestamp))
            return unit_info(db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone(), True)

    @app.get('/api/units/{unit_id}/versions/{revision}')
    def unit_version(unit_id: str, revision: int):
        with connection() as db:
            row = db.execute('SELECT * FROM unit_versions WHERE unit_id=? AND revision=?', (unit_id, revision)).fetchone()
            if not row:
                raise HTTPException(404, '找不到此稿件版本')
            return unit_info(row, True)
