"""A workspace-owned, at most three-level tree and many-to-many work membership."""
from fastapi import HTTPException
from . import repository as r


def category_sql(category_id, uncategorized, values, work='e.work_id'):
    # The transaction sets this key. Correlated EXISTS never duplicates editions.
    if uncategorized:
        return f" AND NOT EXISTS(SELECT 1 FROM kb_literature_memberships lm JOIN kb_literature_categories lc ON lc.id=lm.category_id WHERE lm.work_id={work} AND lc.workspace_key=current_setting('shixue.workspace'))"
    if category_id:
        values['literature_category'] = str(category_id)
        return f""" AND EXISTS (
          WITH RECURSIVE selected AS (
            SELECT id FROM kb_literature_categories
            WHERE id=CAST(:literature_category AS uuid) AND workspace_key=current_setting('shixue.workspace')
            UNION ALL SELECT c.id FROM kb_literature_categories c JOIN selected p ON c.parent_id=p.id
            WHERE c.workspace_key=current_setting('shixue.workspace')
          ) SELECT 1 FROM kb_literature_memberships lm JOIN selected s ON s.id=lm.category_id WHERE lm.work_id={work}
        )"""
    return ''


class LiteratureCategories:
    def __init__(self, root):
        self.root = root

    def _state(self, db, expected=None):
        from .shared import read_only_library
        if read_only_library():
            return r.execute(db,"SELECT revision FROM kb_literature_category_state WHERE workspace_key=current_setting('shixue.workspace')").scalar() or 0
        r.execute(db, "INSERT INTO kb_literature_category_state(workspace_key) VALUES(current_setting('shixue.workspace')) ON CONFLICT DO NOTHING")
        version = r.execute(db, "SELECT revision FROM kb_literature_category_state WHERE workspace_key=current_setting('shixue.workspace') FOR UPDATE").scalar_one()
        if expected is not None and expected != version:
            raise HTTPException(409, '文献分类已更新，请刷新分类后重试')
        return version

    def _items(self, db):
        return r.rows(db, "SELECT id,parent_id,name,position FROM kb_literature_categories WHERE workspace_key=current_setting('shixue.workspace') ORDER BY position,created_at,id")

    def _validate(self, items):
        by_id = {str(c['id']): c for c in items}
        sibling_names = set()
        for c in items:
            key = (str(c['parent_id']), c['name'].casefold())
            if key in sibling_names: raise HTTPException(409, '同一层级已有同名分类')
            sibling_names.add(key)
            seen = {str(c['id'])}
            parent = c['parent_id']
            depth = 1
            while parent is not None:
                parent = str(parent)
                if parent in seen: raise HTTPException(400, '分类不能移到自身或其子分类中')
                if parent not in by_id: raise HTTPException(404, '上级分类不存在')
                seen.add(parent); depth += 1
                if depth > 3: raise HTTPException(400, '文献分类最多支持三级，请调整上级分类')
                parent = by_id[parent]['parent_id']

    def tree(self):
        with r.database.transaction(self.root) as db:
            version = self._state(db)
            items = self._items(db)
            counts = r.rows(db, """
              WITH RECURSIVE descendants AS (
                SELECT id ancestor,id FROM kb_literature_categories WHERE workspace_key=current_setting('shixue.workspace')
                UNION ALL SELECT d.ancestor,c.id FROM descendants d JOIN kb_literature_categories c ON c.parent_id=d.id
                WHERE c.workspace_key=current_setting('shixue.workspace')
              ) SELECT d.ancestor,
                count(DISTINCT w.id) FILTER(WHERE w.item_type='ancient') ancient,
                count(DISTINCT w.id) FILTER(WHERE w.item_type IN ('article','thesis')) papers
              FROM descendants d JOIN kb_literature_memberships m ON m.category_id=d.id
              JOIN kb_works w ON w.id=m.work_id
              WHERE w.library_id=:library AND EXISTS(SELECT 1 FROM kb_editions e WHERE e.work_id=w.id AND NOT e.deleted)
              GROUP BY d.ancestor
            """, library=r.DEFAULT_LIBRARY)
            by_id = {str(c['ancestor']): c for c in counts}
            totals = {}
            for name, unfiled in [('all',False),('uncategorized',True)]:
                values = {'library': r.DEFAULT_LIBRARY}
                totals[name] = r.one(db, "SELECT count(*) FILTER(WHERE w.item_type='ancient') ancient, count(*) FILTER(WHERE w.item_type IN ('article','thesis')) papers FROM kb_works w WHERE w.library_id=:library AND EXISTS(SELECT 1 FROM kb_editions e WHERE e.work_id=w.id AND NOT e.deleted)" + category_sql(None,unfiled,values,'w.id'), **values)
            return {'revision': version, 'items': [{**c,'ancient':by_id.get(str(c['id']),{}).get('ancient',0),'papers':by_id.get(str(c['id']),{}).get('papers',0)} for c in items], **totals}

    def save(self, payload, identifier=None):
        name = payload['name'].strip()
        if not name: raise HTTPException(400, '分类名称不能为空')
        with r.database.transaction(self.root) as db:
            self._state(db,payload['revision'])
            items = self._items(db)
            current = next((c for c in items if str(c['id'])==str(identifier)),None)
            if identifier and not current: raise HTTPException(404, '分类不存在')
            value = {'id':identifier or r.uid(),'name':name,'parent_id':payload.get('parent_id'),'position':payload.get('position',0)}
            self._validate([c for c in items if str(c['id'])!=str(identifier)] + [value])
            if current:
                r.execute(db,'UPDATE kb_literature_categories SET name=:name,parent_id=:parent_id,position=:position WHERE id=:id',**value)
            else:
                r.execute(db,"INSERT INTO kb_literature_categories(id,workspace_key,name,parent_id,position) VALUES(:id,current_setting('shixue.workspace'),:name,:parent_id,:position)",**value)
            self._advance(db)
            return value

    def _advance(self, db):
        r.execute(db,"UPDATE kb_literature_category_state SET revision=revision+1 WHERE workspace_key=current_setting('shixue.workspace')")

    def delete(self, identifier, revision, subtree=False):
        with r.database.transaction(self.root) as db:
            self._state(db,revision)
            items = self._items(db)
            if not any(str(c['id'])==str(identifier) for c in items): raise HTTPException(404,'分类不存在')
            selected = {str(identifier)}
            for _ in range(3):
                selected.update(str(c['id']) for c in items if str(c['parent_id']) in selected)
            if len(selected)>1 and not subtree: raise HTTPException(409,'此分类还有子分类，请明确选择删除整棵分支')
            # Parent foreign keys require removing leaves first. Memberships cascade; works never do.
            for _ in range(3):
                r.execute(db,'DELETE FROM kb_literature_categories c WHERE c.id=ANY(CAST(:ids AS uuid[])) AND NOT EXISTS(SELECT 1 FROM kb_literature_categories child WHERE child.parent_id=c.id)',ids=list(selected))
            self._advance(db)
            return {'removed':len(selected),'documents_retained':True}

    def memberships(self, work_ids):
        with r.database.transaction(self.root) as db:
            version = self._state(db)
            items = r.rows(db,"SELECT m.work_id,m.category_id FROM kb_literature_memberships m JOIN kb_literature_categories c ON c.id=m.category_id WHERE c.workspace_key=current_setting('shixue.workspace') AND m.work_id=ANY(CAST(:ids AS uuid[]))",ids=[str(v) for v in work_ids])
            return {'revision':version,'items':items}

    def assign(self, payload):
        with r.database.transaction(self.root) as db:
            self._state(db,payload['revision'])
            self.assign_in_transaction(db,payload['work_ids'],payload['category_ids'],payload['mode'])
            self._advance(db)
            return {'updated':len(set(payload['work_ids']))}

    def assign_in_transaction(self, db, works, categories, mode='add'):
        works = list({str(v) for v in works}); categories = list({str(v) for v in categories})
        found = r.execute(db,'SELECT count(*) FROM kb_works w WHERE w.id=ANY(CAST(:ids AS uuid[])) AND w.library_id=:library AND EXISTS(SELECT 1 FROM kb_editions e WHERE e.work_id=w.id AND NOT e.deleted)',ids=works,library=r.DEFAULT_LIBRARY).scalar_one()
        if found!=len(works): raise HTTPException(404,'所选文献已移除，请刷新后重试')
        found = r.execute(db,"SELECT count(*) FROM kb_literature_categories WHERE id=ANY(CAST(:ids AS uuid[])) AND workspace_key=current_setting('shixue.workspace')",ids=categories).scalar_one()
        if found!=len(categories): raise HTTPException(404,'目标分类不存在，请刷新后重试')
        if mode in ('replace','remove'):
            r.execute(db,"DELETE FROM kb_literature_memberships m USING kb_literature_categories c WHERE c.id=m.category_id AND c.workspace_key=current_setting('shixue.workspace') AND m.work_id=ANY(CAST(:works AS uuid[]))" + (' AND m.category_id=ANY(CAST(:categories AS uuid[]))' if mode=='remove' else ''),works=works,categories=categories)
        if mode in ('replace','add'):
            r.execute(db,'INSERT INTO kb_literature_memberships(category_id,work_id) SELECT c,w FROM unnest(CAST(:categories AS uuid[])) c CROSS JOIN unnest(CAST(:works AS uuid[])) w ON CONFLICT DO NOTHING',categories=categories,works=works)
