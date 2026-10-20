"""The sole authority for present-day project document selection and ranges."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from . import repository as r


ROLES = {'base', 'witness', 'citation', 'reference', 'auxiliary'}


def normalized_ranges(connection, edition_id, ranges):
    files = r.rows(connection, 'SELECT ea.asset_id,ea.ordinal,COALESCE(max(p.page_index),0) maximum '
                   'FROM kb_edition_assets ea LEFT JOIN kb_pages p ON p.asset_id=ea.asset_id '
                   'WHERE ea.edition_id=:e GROUP BY ea.asset_id,ea.ordinal', e=edition_id)
    assets = {str(item['asset_id']): item for item in files}
    groups = {}
    for selected in ranges:
        asset = str(UUID(str(selected['asset_id'])))
        first, last = int(selected['page_from']), int(selected['page_to'])
        if asset not in assets or first < 1 or first > last or last > assets[asset]['maximum']:
            raise HTTPException(400, '附件或页段不属于该版本，或范围超出原件')
        groups.setdefault(asset, []).append((first, last))
    result = []
    for asset in sorted(groups, key=lambda key: (assets[key]['ordinal'], key)):
        merged = []
        for first, last in sorted(groups[asset]):
            if merged and first <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        result.extend({'asset_id': asset, 'page_from': first, 'page_to': last} for first, last in merged)
    return result


def range_key(mode, ranges):
    return hashlib.sha256(r.j({'format': 1, 'mode': mode, 'ranges': ranges}).encode()).hexdigest()


class ProjectSourceService:
    def __init__(self, root: Path, local_connection):
        self.root = root
        self.local_connection = local_connection

    def project(self, identifier, *, writable=False):
        with self.local_connection() as local:
            item = local.execute('SELECT * FROM projects WHERE id=? AND deleted=0', (identifier,)).fetchone()
            if not item:
                raise HTTPException(404, '找不到项目')
            if writable and item['archived']:
                raise HTTPException(409, '归档项目请先恢复后再修改资料用途')
            return dict(item)

    def put(self, project_id, edition_id, payload, *, historical=False):
        project=self.project(project_id, writable=not historical)
        roles = list(dict.fromkeys(payload.get('roles', ['reference'])))
        if not roles or set(roles) - ROLES:
            raise HTTPException(400, '项目用途无效')
        mode = payload.get('mode', 'edition')
        if mode not in {'edition', 'ranges'}:
            raise HTTPException(400, '项目范围模式无效')
        with r.database.transaction(self.root) as connection:
            r.edition(connection, edition_id)
            ranges = normalized_ranges(connection, edition_id, payload.get('ranges', [])) if mode == 'ranges' else []
            if mode == 'ranges' and not ranges:
                raise HTTPException(400, '请选择附件与页段')
            key = range_key(mode, ranges)
            existing = r.execute(connection, 'SELECT * FROM kb_project_sources WHERE project_id=:p '
                                 'AND edition_id=:e AND range_key=:k FOR UPDATE', p=project_id, e=edition_id, k=key).mappings().first()
            if existing:
                if historical:return dict(existing)
                expected = payload.get('version')
                if expected is not None and expected != existing['version']:
                    raise HTTPException(409, '项目资料用途已被修改，请刷新后重试')
                return r.one(connection, 'UPDATE kb_project_sources SET roles=CAST(:roles AS jsonb), '
                             'witness_label=:label,scope_note=:note,state=\'active\',version=version+1 '
                             'WHERE id=:id RETURNING *', id=existing['id'], roles=r.j(roles),
                             label=payload.get('witness_label', ''), note=payload.get('scope_note', ''))
            created=r.one(connection, 'INSERT INTO kb_project_sources '
                         '(id,project_id,edition_id,roles,witness_label,scope_note,mode,ranges,range_key) '
                         'VALUES(:id,:p,:e,CAST(:roles AS jsonb),:label,:note,:mode,CAST(:ranges AS jsonb),:key) RETURNING *',
                         id=r.uid(), p=project_id, e=edition_id, roles=r.j(roles),
                         label=payload.get('witness_label', ''), note=payload.get('scope_note', ''),
                         mode=mode, ranges=r.j(ranges), key=key)
            if historical and project['archived']:
                r.execute(connection,"UPDATE kb_project_sources SET state='archived' WHERE id=:id",id=created['id'])
            return created

    def list(self, project_id, *, include_archived=False):
        project = self.project(project_id)
        with r.database.transaction(self.root) as connection:
            items=r.rows(connection, 'SELECT s.*,e.label,w.title,w.item_type,w.authors_json '
                          'FROM kb_project_sources s JOIN kb_editions e ON e.id=s.edition_id '
                          'JOIN kb_works w ON w.id=e.work_id WHERE s.project_id=:p AND NOT e.deleted '
                          'AND s.state IN (\'active\',\'archived\') ORDER BY s.created_at,s.id', p=project_id)
            for item in items:
                item['state']='archived' if project['archived'] else 'active'
            return items

    def update(self,project_id,source_id,payload):
        self.project(project_id,writable=True)
        roles=list(dict.fromkeys(payload.get('roles',[])))
        if not roles or set(roles)-ROLES:raise HTTPException(400,'项目用途无效')
        mode=payload.get('mode','edition')
        if mode not in {'edition','ranges'}:raise HTTPException(400,'项目范围模式无效')
        with r.database.transaction(self.root) as connection:
            current=r.one(connection,"SELECT * FROM kb_project_sources WHERE id=:id AND project_id=:p AND state IN ('active','archived') FOR UPDATE",id=source_id,p=project_id)
            if payload.get('version')!=current['version']:raise HTTPException(409,'项目资料范围已更新，请刷新后重试')
            r.edition(connection,current['edition_id'])
            ranges=normalized_ranges(connection,current['edition_id'],payload.get('ranges',[])) if mode=='ranges' else []
            if mode=='ranges' and not ranges:raise HTTPException(400,'请选择附件与页段')
            key=range_key(mode,ranges)
            duplicate=r.execute(connection,'SELECT id FROM kb_project_sources WHERE project_id=:p AND edition_id=:e AND range_key=:key AND id<>:id',
                                p=project_id,e=current['edition_id'],key=key,id=source_id).scalar()
            if duplicate:raise HTTPException(409,'此范围已有项目关联，请编辑已有范围')
            return r.one(connection,'UPDATE kb_project_sources SET roles=CAST(:roles AS jsonb),mode=:mode,ranges=CAST(:ranges AS jsonb),range_key=:key,'
                         "witness_label=:label,scope_note=:note,version=version+1,state='active' WHERE id=:id RETURNING *",
                         id=source_id,roles=r.j(roles),mode=mode,ranges=r.j(ranges),key=key,label=payload.get('witness_label',''),note=payload.get('scope_note',''))

    def remove(self, project_id, source_id):
        self.project(project_id, writable=True)
        with r.database.transaction(self.root) as connection:
            r.one(connection, "UPDATE kb_project_sources SET state='invalid',version=version+1 "
                  'WHERE id=:id AND project_id=:p RETURNING id', id=source_id, p=project_id)

    def resolved_scope(self, project_id):
        project = self.project(project_id)
        sources = self.list(project_id)
        return {'_project_sources': [{'edition_id': str(item['edition_id']), 'mode': item['mode'],
                                      'ranges': item['ranges']} for item in sources], '_empty': not bool(sources)}

    def allows(self, project_id, edition_id, requested):
        self.project(project_id, writable=True)
        try:
            with r.database.transaction(self.root) as connection:
                requested=normalized_ranges(connection,edition_id,requested)
        except HTTPException:
            return False
        sources = self.list(project_id)
        applicable = [item for item in sources if str(item['edition_id']) == str(edition_id) and item['state'] == 'active']
        if any(item['mode'] == 'edition' for item in applicable):
            return True
        for selected in requested:
            intervals = sorted((int(span['page_from']), int(span['page_to'])) for item in applicable
                               for span in item['ranges'] if str(span['asset_id']) == str(selected['asset_id']))
            cursor = selected['page_from']
            for first, last in intervals:
                if first > cursor:
                    break
                cursor = max(cursor, last + 1)
                if cursor > selected['page_to']:
                    break
            if cursor <= selected['page_to']:
                return False
        return bool(requested)

    def sync_events(self):
        with self.local_connection() as local:
            events = local.execute('SELECT * FROM project_source_events WHERE done=0 ORDER BY sequence').fetchall()
        for event in events:
            with self.local_connection() as local:
                current = local.execute('SELECT archived,deleted FROM projects WHERE id=?', (event['project_id'],)).fetchone()
            state = 'invalid' if not current or current['deleted'] else 'archived' if current['archived'] else 'active'
            with r.database.transaction(self.root) as connection:
                # A user-removed relationship must not be resurrected by project restoration.
                r.execute(connection, 'UPDATE kb_project_sources SET state=:s WHERE project_id=:p '
                          "AND state IN ('active','archived')", s=state, p=event['project_id'])
            with self.local_connection() as local:
                local.execute('UPDATE project_source_events SET done=1 WHERE sequence=?', (event['sequence'],))
