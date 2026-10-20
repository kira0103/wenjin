"""Historical IDs and current catalog projections for the existing task UI."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from . import repository as r
from .execution import activated, root_for, WorkingInputService, digest
from .project_sources import ProjectSourceService


@contextmanager
def borrowed(db):
    yield db


def services(db):
    root = root_for(db)
    local = lambda: borrowed(db)
    return ProjectSourceService(root, local), WorkingInputService(root, local, lambda: datetime.now(timezone.utc).isoformat())


def alias(edition, asset):
    return str(edition) + '~' + str(asset)


def resolve(db, identifier):
    if '~' in identifier:
        try:
            edition, asset = identifier.split('~')
            return str(UUID(edition)), str(UUID(asset))
        except ValueError:
            raise HTTPException(404, '来源编号无效') from None
    with r.database.transaction(root_for(db)) as connection:
        item = r.execute(connection, 'SELECT edition_id,asset_id FROM kb_legacy_links WHERE material_id=:id', id=identifier).mappings().first()
        if not item:
            raise HTTPException(404, '找不到文献来源')
        return str(item['edition_id']), str(item['asset_id'])


def snapshot(db, identifier):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='working_inputs'").fetchone():
        return None
    row = db.execute('SELECT * FROM working_inputs WHERE material_id=?', (identifier,)).fetchone()
    if row and digest(row['body']) != row['body_digest']:
        raise HTTPException(409, '固定输入校验失败')
    return row


def snapshot_info(row):
    source = json.loads(row['source_json'])
    return {'id': row['material_id'], 'project_id': row['project_id'], 'project_ids': [row['project_id']],
            'title': source.get('title') or source.get('material_title') or '固定输入', 'filename': '固定输入.txt',
            'page_count': max(1, len(json.loads(row['positions_json']))), 'file_format': 'txt', 'position_kind': 'section',
            'roles': ['reference'], 'witness_label': '', 'scope_note': '固定修订输入',
            'kind': 'working_input', 'working_input_id': row['id'], 'revision_id': row['revision_id'],
            'edition_id': row['edition_id'], 'extraction_coverage': 1, 'reference_count': 0,
            'created_at': row['created_at']}


def catalog_materials(db, project_id=None):
    projects, _ = services(db)
    selected = projects.list(project_id) if project_id else None
    values={}
    if selected is not None:
        visible='e.id=ANY(CAST(:selected AS uuid[]))'
        values['selected']=[str(item['edition_id']) for item in selected]
    else:
        visible="(NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id) OR EXISTS(SELECT 1 FROM kb_project_sources ps WHERE ps.edition_id=e.id AND ps.state='active'))"
    with r.database.transaction(root_for(db)) as connection:
        items = r.rows(connection, 'SELECT e.id edition_id,e.label,w.title,w.item_type,w.authors_json,w.date_label, '
                       'a.id asset_id,a.availability,ea.filename,ea.position_kind,e.created_at, '
                       '(SELECT COALESCE(max(p.page_index),0) FROM kb_pages p WHERE p.asset_id=a.id) page_count '
                       'FROM kb_editions e JOIN kb_works w ON w.id=e.work_id '
                       'JOIN kb_edition_assets ea ON ea.edition_id=e.id JOIN kb_assets a ON a.id=ea.asset_id '
                       'WHERE NOT e.deleted AND '+visible+' ORDER BY e.created_at DESC,ea.ordinal,ea.asset_id',**values)
        links = r.rows(connection, "SELECT * FROM kb_project_sources WHERE state IN ('active','archived')")
    valid_projects = {row['id'] for row in db.execute('SELECT id FROM projects WHERE deleted=0')}
    result = []
    for item in items:
        relevant = [link for link in (selected if selected is not None else links)
                    if str(link['edition_id']) == str(item['edition_id']) and link['project_id'] in valid_projects
                    and (link['mode'] == 'edition' or any(span['asset_id'] == str(item['asset_id']) for span in link['ranges']))]
        if selected is not None and not relevant:
            continue
        result.append({'id': alias(item['edition_id'], item['asset_id']), 'entry_id': str(item['edition_id']),
                       'edition_id': str(item['edition_id']), 'asset_id': str(item['asset_id']),
                       'project_id': project_id or '', 'project_ids': sorted({link['project_id'] for link in relevant}),
                       'title': item['title'], 'version_label': item['label'], 'item_type': item['item_type'],
                       'authors': '、'.join(item['authors_json']), 'year': item['date_label'],
                       'filename': item['filename'], 'file_format': Path(item['filename']).suffix.lstrip('.').lower(),
                       'position_kind': item['position_kind'], 'page_count': item['page_count'],
                       'availability': item['availability'], 'extraction_coverage': 1,
                       'roles': sorted({role for link in relevant for role in link['roles']}) or ['reference'],
                       'witness_label': ' / '.join(dict.fromkeys(link['witness_label'] for link in relevant if link['witness_label'])),
                       'scope_note': '；'.join(dict.fromkeys(link['scope_note'] for link in relevant if link['scope_note'])),
                       'selected_ranges': [span for link in relevant for span in link['ranges'] if span['asset_id'] == str(item['asset_id'])],
                       'reference_count': 0, 'created_at': item['created_at']})
    return result


def material_info(db, identifier, project_id=None):
    frozen = snapshot(db, identifier)
    if frozen:
        return snapshot_info(frozen)
    edition, asset = resolve(db, identifier)
    items = catalog_materials(db, project_id)
    item = next((item for item in items if item['edition_id'] == edition and item['asset_id'] == asset), None)
    if not item:
        raise HTTPException(404, '资料不在当前范围内')
    return {**item, 'id': identifier}


def attached(db, project_id, identifier, *, historical=False):
    frozen = snapshot(db, identifier)
    if frozen:
        return frozen['project_id'] == project_id
    legacy = db.execute("SELECT 1 FROM materials WHERE id=? AND kind='legacy_handle'", (identifier,)).fetchone()
    if historical and legacy and db.execute('SELECT 1 FROM project_materials WHERE project_id=? AND material_id=?', (project_id, identifier)).fetchone():
        return True
    try:
        material_info(db, identifier, project_id)
        return True
    except HTTPException:
        return False


def freeze(db, project_id, identifier, first=None, last=None):
    projects, inputs = services(db)
    existing = snapshot(db, identifier)
    if existing:
        if existing['project_id'] != project_id:
            raise HTTPException(403, '固定输入不属于当前项目')
        value = WorkingInputService._value(existing)
        if first is None and last is None:
            return value
        positions = value['positions']
        first, last = first or 1, last or max(1, len(positions))
        if not 1 <= first <= last <= max(1, len(positions)):
            raise HTTPException(400, '固定输入范围无效')
        if not positions or (first == 1 and last == len(positions)):
            return value
        start = positions[first-1]['snapshot_start']
        end = positions[last-1]['snapshot_end']
        return inputs.text(project_id, value['body'][start:end], source={**value['source'], 'parent_input_id': value['id']}, db=db)
    edition, asset = resolve(db, identifier)
    with r.database.transaction(root_for(db)) as connection:
        maximum = r.execute(connection, 'SELECT max(page_index) FROM kb_pages WHERE asset_id=:a', a=asset).scalar() or 0
    if first is None and last is None:
        sources = projects.list(project_id)
        if any(str(link['edition_id']) == edition and link['mode'] == 'edition' for link in sources):
            ranges = [{'asset_id': asset, 'page_from': 1, 'page_to': maximum}]
        else:
            ranges = [span for link in sources if str(link['edition_id']) == edition for span in link['ranges'] if span['asset_id'] == asset]
    else:
        ranges = [{'asset_id': asset, 'page_from': first or 1, 'page_to': last or maximum}]
    return inputs.catalog(project_id, edition, ranges, db=db)


def positions(value):
    return [{**p, 'start': p['snapshot_start'], 'end': p['snapshot_end']} for p in value['positions']] or [
        {'number': 1, 'start': 0, 'end': len(value['body'])}]


def execution_handles(db,identifier):
    """Find frozen preparations for a catalog alias without a PostgreSQL read."""
    result=[identifier]
    if '~' not in identifier:return result
    edition,asset=resolve(db,identifier)
    result.extend(row[0] for row in db.execute('SELECT w.material_id FROM working_inputs w WHERE w.edition_id=? '
                  "AND EXISTS(SELECT 1 FROM json_each(w.ranges_json) span WHERE json_extract(span.value,'$.asset_id')=?)",
                  (edition,asset)))
    return result


def books(db):
    materials=catalog_materials(db)
    with r.database.transaction(root_for(db)) as connection:
        entries=r.rows(connection,'SELECT e.*,w.title,w.authors_json,w.date_label,w.item_type '
                       'FROM kb_editions e JOIN kb_works w ON w.id=e.work_id WHERE NOT e.deleted '
                       "AND (NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id) OR EXISTS(SELECT 1 FROM kb_project_sources ps WHERE ps.edition_id=e.id AND ps.state='active')) ORDER BY e.created_at DESC,e.id")
        links=r.rows(connection,"SELECT * FROM kb_project_sources WHERE state IN ('active','archived')")
    valid={row['id'] for row in db.execute('SELECT id FROM projects WHERE deleted=0')}
    return [{'id':str(item['id']),'title':item['title'],'authors':'、'.join(item['authors_json']),
             'year':item['date_label'],'version_label':item['label'],'raw_reference':item['source_note'],
             'item_type':item['item_type'],'genre':'、'.join(item.get('tags',[])),
             'material_ids':[m['id'] for m in materials if m['entry_id']==str(item['id'])],
             'project_ids':sorted({link['project_id'] for link in links if str(link['edition_id'])==str(item['id']) and link['project_id'] in valid}),
             'project_roles':{project:sorted({role for link in links if str(link['edition_id'])==str(item['id']) and link['project_id']==project for role in link['roles']}) for project in valid},
             'created_at':item['created_at'],'version':item['version']} for item in entries]


def detail(db,identifier):
    frozen=snapshot(db,identifier)
    info=material_info(db,identifier)
    if frozen:
        pages=[dict(row) for row in db.execute('SELECT page_number,text FROM pages WHERE material_id=? ORDER BY page_number',(identifier,))]
        changed=None
        if frozen['edition_id']:
            try:
                with r.database.transaction(root_for(db)) as connection:
                    current=r.edition(connection,frozen['edition_id'],deleted=True)
                    changed=str(current['active_revision'])!=str(frozen['revision_id'])
            except Exception:pass  # The execution snapshot is independently readable.
        return {**info,'pages':pages,'references':[],'reference_summary':{'total':0,'in_library':0,'missing':0,'version_unverified':0},
                'source_positions':json.loads(frozen['positions_json']),'source_revision':frozen['revision_id'],'source_revision_changed':changed}
    edition,asset=resolve(db,identifier)
    with r.database.transaction(root_for(db)) as connection:
        current=r.edition(connection,edition)
        blocks=r.rows(connection,'SELECT page_index,body FROM kb_blocks WHERE revision_id=:revision AND asset_id=:asset ORDER BY ordinal',revision=current['active_revision'],asset=asset)
        refs=r.rows(connection,'SELECT c.*,w.title matched_title FROM kb_reference_candidates c LEFT JOIN kb_editions e ON e.id=c.matched_edition_id LEFT JOIN kb_works w ON w.id=e.work_id '
                    'WHERE c.source_edition_id=:e AND c.source_asset_id=:a ORDER BY c.ordinal',e=edition,a=asset)
    grouped={}
    for block in blocks:grouped.setdefault(block['page_index'],[]).append(block['body'])
    references=[{**item,'match_id':str(item['matched_edition_id']) if item['matched_edition_id'] else None,
                 'authors':item['authors_raw'],'year':item['year_label'],'page_number':item['page_index']} for item in refs]
    return {**info,'pages':[{'page_number':page,'text':'\n\n'.join(values)} for page,values in sorted(grouped.items())],
            'references':references,'reference_summary':{'total':len(refs),'in_library':sum(bool(item['match_id']) for item in references),
            'missing':sum(not item['match_id'] for item in references),'version_unverified':sum(bool(item['match_id']) and '人工确认' not in item['match_status'] for item in references)}}
