"""Single write path for bibliographic records and original attachments."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from . import repository as r
from .literature_categories import LiteratureCategories, category_sql


class CatalogService:
    def __init__(self, root: Path):
        self.root = root

    def create(self, payload):
        if not payload.get('title','').strip():raise HTTPException(400,'题名不能为空')
        with r.database.transaction(self.root) as connection:
            categories = payload.get('category_ids', [])
            manager = LiteratureCategories(self.root)
            if categories: manager._state(connection)
            document = r.create_edition(connection, payload)
            if categories:
                manager.assign_in_transaction(connection,[document['work_id']],categories)
                manager._advance(connection)
            return document

    def list(self, *, query='', limit=100, cursor=None, edition_ids=None, kind='all', source='all', category='', include_core=False, year='', tag='', state='all', category_id=None, uncategorized=False):
        if category_id and uncategorized: raise HTTPException(400,'不能同时指定分类和未分类')
        values = {'query': '%' + query + '%', 'limit': min(200, max(1, limit)), 'library': r.DEFAULT_LIBRARY}
        where = 'NOT e.deleted AND w.library_id=:library'
        where += category_sql(category_id,uncategorized,values)
        if kind == 'ancient': where += " AND w.item_type='ancient'"
        elif kind == 'papers': where += " AND w.item_type IN ('article','thesis')"
        elif kind == 'unclassified': where += " AND w.item_type NOT IN ('ancient','article','thesis')"
        if source == 'core': where += ' AND EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)'
        elif source == 'personal': where += ' AND NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)'
        if category:
            where += ' AND EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id AND cb.category=:category)'
            values['category'] = category
        if year:
            where += " AND (w.date_label ILIKE :year OR e.publication_year::text ILIKE :year)"
            values['year'] = '%' + year + '%'
        if tag:
            where += " AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(e.tags) AS tag_value(value) WHERE tag_value.value ILIKE :tag)"
            values['tag'] = '%' + tag + '%'
        if state == 'metadata': where += ' AND NOT EXISTS(SELECT 1 FROM kb_edition_assets ea WHERE ea.edition_id=e.id)'
        elif state == 'text': where += ' AND e.active_revision IS NOT NULL'
        elif state == 'ready': where += " AND EXISTS(SELECT 1 FROM kb_indexes i WHERE i.id=e.active_index AND i.state='ready')"
        if query:
            where += " AND (w.title ILIKE :query OR w.authors_json::text ILIKE :query OR e.label ILIKE :query)"
        if edition_ids is not None:
            where += ' AND e.id=ANY(CAST(:ids AS uuid[]))'
            values['ids'] = [str(value) for value in edition_ids]
        elif not include_core:
            where += " AND (NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id) OR EXISTS(SELECT 1 FROM kb_project_sources ps WHERE ps.edition_id=e.id AND ps.state='active'))"
        if cursor:
            try:
                created, identifier = json.loads(base64.urlsafe_b64decode(cursor))
            except Exception:
                raise HTTPException(400, '列表游标无效') from None
            where += ' AND (e.created_at,e.id)<(CAST(:created AS timestamptz),CAST(:identifier AS uuid))'
            values.update(created=created, identifier=identifier)
        with r.database.transaction(self.root) as connection:
            items = r.rows(connection, 'SELECT e.*,w.title,w.item_type,w.authors_json,w.date_label,w.version work_version, '
                           "COALESCE((SELECT jsonb_agg(lm.category_id) FROM kb_literature_memberships lm JOIN kb_literature_categories lc ON lc.id=lm.category_id WHERE lm.work_id=e.work_id AND lc.workspace_key=current_setting('shixue.workspace')),'[]'::jsonb) category_ids, "
                           '(EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)) is_core, '
                           '(SELECT category FROM kb_core_books cb WHERE cb.edition_id=e.id) category, '
                           '(SELECT count(*) FROM kb_edition_assets ea WHERE ea.edition_id=e.id) file_count, '
                           '(SELECT state FROM kb_indexes i WHERE i.id=e.active_index) index_state '
                           'FROM kb_editions e JOIN kb_works w ON w.id=e.work_id WHERE ' + where +
                           ' ORDER BY e.created_at DESC,e.id DESC LIMIT :limit', **values)
        next_cursor = base64.urlsafe_b64encode(r.j([items[-1]['created_at'].isoformat(), str(items[-1]['id'])]).encode()).decode() if len(items) == values['limit'] else None
        return {'items': items, 'next_cursor': next_cursor}

    def update_work(self, work_id, data, version):
        with r.database.transaction(self.root) as connection:
            item = r.execute(connection, 'UPDATE kb_works SET title=:t,item_type=:kind,authors_json=CAST(:a AS jsonb), '
                             'date_label=:date,version=version+1 WHERE id=:id AND version=:version RETURNING *',
                             id=work_id, version=version, t=data['title'].strip(), kind=data['item_type'],
                             a=r.j(data.get('authors', [])), date=data.get('date_label', '')).mappings().first()
            if not item:
                raise HTTPException(409, '作品资料已更新，请刷新后重试')
            return dict(item)

    def update_edition(self, identifier, data, version):
        with r.database.transaction(self.root) as connection:
            item = r.execute(connection, 'UPDATE kb_editions SET label=:label,publisher=:p,publication_year=:year, '
                             'doi=:doi,source_note=:note,tags=CAST(:tags AS jsonb),version=version+1 '
                             'WHERE id=:id AND version=:version AND NOT deleted RETURNING *',
                             id=identifier, version=version, label=data.get('label', ''), p=data.get('publisher', ''),
                             year=data.get('publication_year'), doi=data.get('doi', ''), note=data.get('source_note', ''),
                             tags=r.j(data.get('tags', []))).mappings().first()
            if not item:
                raise HTTPException(409, '版本资料已更新，请刷新后重试')
            return dict(item)

    def import_file(self, identifier, filename, data):
        filename = Path(filename.replace('\\', '/')).name
        if not data or len(data) > 250 * 1024 * 1024:
            raise HTTPException(400, '原件为空或超过 250 MB')
        file_digest = hashlib.sha256(data).hexdigest()
        with r.database.transaction(self.root) as connection:
            r.one(connection, 'SELECT id FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE', id=identifier)
            if r.execute(connection,'SELECT edition_id FROM kb_core_books WHERE edition_id=:id',id=identifier).first():
                raise HTTPException(409,'核心古籍原始版本保持只读。请在项目中制作校对稿，或另建文献版本后添加原件')
            existing = r.execute(connection, 'SELECT * FROM kb_assets WHERE sha256=:h FOR UPDATE', h=file_digest).mappings().first()
            if existing and existing['availability'] == 'present':
                asset = existing['id']
            else:
                _, key = r.store_file(self.root, filename, data)
                asset = r.execute(connection, 'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes,mime_type) '
                                  'VALUES(:id,:h,:key,:size,:mime) ON CONFLICT(sha256) DO UPDATE SET '
                                  "storage_key=excluded.storage_key,size_bytes=excluded.size_bytes,availability='present' RETURNING id",
                                  id=r.uid(), h=file_digest, key=key, size=len(data),
                                  mime='application/pdf' if filename.lower().endswith('.pdf') else 'application/octet-stream').scalar_one()
            attached = r.execute(connection, 'INSERT INTO kb_edition_assets(edition_id,asset_id,filename,ordinal) '
                                 'VALUES(:e,:a,:f,(SELECT count(*) FROM kb_edition_assets WHERE edition_id=:e)) ON CONFLICT DO NOTHING',
                                 e=identifier, a=asset, f=filename)
            if not attached.rowcount:
                job = r.execute(connection, "SELECT * FROM kb_jobs WHERE kind='import' AND payload->>'edition_id'=:e "
                                "AND payload->>'asset_id'=:a ORDER BY created_at DESC LIMIT 1",
                                e=str(identifier), a=str(asset)).mappings().first()
                return {**dict(job), 'duplicate': True, 'asset_id': str(asset)} if job else {
                    'state': 'succeeded', 'duplicate': True, 'asset_id': str(asset)}
            job = r.queue(connection, 'import', {'edition_id': str(identifier), 'asset_id': str(asset)})
            return {**job, 'asset_id': str(asset), 'duplicate': False}

    def add_candidate(self, candidate_id):
        with r.database.transaction(self.root) as connection:
            candidate = r.one(connection, 'SELECT * FROM kb_reference_candidates WHERE id=:id FOR UPDATE', id=candidate_id)
            if candidate['matched_edition_id']:
                return r.edition(connection, candidate['matched_edition_id'])
            # A title-only match does not establish edition identity.
            document = r.create_edition(connection, {'title': candidate['title'], 'item_type': 'other',
                                        'authors': [candidate['authors_raw']] if candidate['authors_raw'] else [],
                                        'date_label': candidate['year_label'],
                                        'source_note': candidate['raw_text']})
            r.execute(connection, 'UPDATE kb_reference_candidates SET matched_edition_id=:e,match_status=:s WHERE id=:id',
                      e=document['id'], s='已加入书目，版本待核验', id=candidate_id)
            return document
