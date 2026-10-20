"""File-oriented literature: named import groups and preserved relative paths."""
from fastapi import HTTPException
from . import repository as r

LIVE="(de.origin_key NOT LIKE 'core:%' OR EXISTS(SELECT 1 FROM kb_core_files live_file WHERE live_file.revision_id=e.active_revision AND live_file.asset_id=de.asset_id AND live_file.source_path=de.relative_path))"


def relative_path(value, *, empty=False):
    value=str(value).replace('\\','/')
    if not value and empty:return ''
    parts=value.split('/')
    if not value or len(value)>2000 or any(p in ('','..','.') for p in parts) or any(':' in p or '\x00' in p for p in parts) or value.startswith('/'):
        raise HTTPException(400,'原目录路径无效')
    return value


def selection_where(scope, values, alias='de'):
    where=f"{alias}.workspace_key=current_setting('shixue.workspace')"
    if scope.get('document_group_id'):
        values['doc_group']=str(scope['document_group_id']);where+=f' AND {alias}.group_id=CAST(:doc_group AS uuid)'
    if scope.get('document_uncategorized'):where+=f' AND {alias}.group_id IS NULL'
    if scope.get('document_path'):
        values['doc_path']=relative_path(scope['document_path'])
        where+=f" AND ({alias}.relative_path=:doc_path OR starts_with({alias}.relative_path,:doc_path || '/'))"
    if scope.get('document_entry_ids'):
        values['doc_entries']=[str(v) for v in scope['document_entry_ids']]
        where+=f' AND {alias}.id=ANY(CAST(:doc_entries AS uuid[]))'
    return where


def has_selection(scope):
    return bool(scope.get('document_group_id') or scope.get('document_uncategorized') or scope.get('document_path') or scope.get('document_entry_ids'))


def scope_sql(scope, values):
    if '_document_sources' in scope:
        values['doc_sources']=r.j(scope['_document_sources'])
        return """ AND EXISTS(SELECT 1 FROM jsonb_array_elements(CAST(:doc_sources AS jsonb)) ds
          WHERE (ds->>'edition_id')::uuid=e.id AND (ds->>'asset_id')::uuid=b.asset_id
            AND (ds->>'source_path' IS NULL OR b.quality->>'source_path'=ds->>'source_path'))"""
    if not has_selection(scope):return ''
    where=selection_where(scope,values)
    return ' AND EXISTS(SELECT 1 FROM kb_document_entries de WHERE '+where+" AND de.edition_id=e.id AND (de.asset_id IS NULL OR de.asset_id=b.asset_id) AND (de.origin_key NOT LIKE 'core:%' OR b.quality->>'source_path'=de.relative_path))"


def freeze_scope(db,scope):
    if not has_selection(scope) or '_document_sources' in scope:return scope
    from .retrieval import scope_sql as all_scope
    values={};where=all_scope(scope,values)
    sources=r.rows(db,"SELECT DISTINCT e.id edition_id,b.asset_id,b.quality->>'source_path' source_path FROM kb_blocks b JOIN kb_revisions rev ON rev.id=b.revision_id JOIN kb_editions e ON e.id=rev.edition_id WHERE "+where,**values)
    return {**scope,'_document_sources':[{k:str(v) if v is not None else None for k,v in row.items()} for row in sources]}


class DocumentLibrary:
    def __init__(self, root):self.root=root

    def state(self,db,expected=None):
        r.execute(db,"INSERT INTO kb_document_library_state(workspace_key) VALUES(current_setting('shixue.workspace')) ON CONFLICT DO NOTHING")
        row=r.one(db,"SELECT * FROM kb_document_library_state WHERE workspace_key=current_setting('shixue.workspace') FOR UPDATE")
        if expected is not None and expected!=row['revision']:raise HTTPException(409,'资料目录已更新，请刷新后重试')
        return row

    def advance(self,db):r.execute(db,"UPDATE kb_document_library_state SET revision=revision+1 WHERE workspace_key=current_setting('shixue.workspace')")

    def group(self,db,name):
        name=name.strip()
        if not name or name=='未分类':return None
        if len(name)>100:raise HTTPException(400,'分类名称不能超过 100 字')
        return r.execute(db,"INSERT INTO kb_document_groups(id,workspace_key,name) VALUES(:id,current_setting('shixue.workspace'),:name) ON CONFLICT(workspace_key,lower(name)) DO UPDATE SET name=kb_document_groups.name RETURNING id",id=r.uid(),name=name).scalar_one()

    def sync(self,db,core_groups=None):
        """Only add missing references. Manual moves and names survive later scans."""
        state=self.state(db)
        # Include later source imports and active core revisions as well.
        for corpus in r.rows(db,'SELECT id,slug,name,source_path FROM kb_corpora'):
            missing=r.execute(db,"""SELECT EXISTS(SELECT 1 FROM kb_core_books cb JOIN kb_editions e ON e.id=cb.edition_id
              JOIN kb_core_files f ON f.revision_id=e.active_revision WHERE cb.corpus_id=:corpus AND NOT e.deleted
              AND NOT EXISTS(SELECT 1 FROM kb_document_entries de WHERE de.workspace_key=current_setting('shixue.workspace')
                AND de.origin_key='core:' || e.id::text || ':' || f.asset_id::text || ':' || f.source_path))""",corpus=corpus['id']).scalar_one()
            if missing:
                name='daizhige' if 'daizhige' in (corpus['slug']+' '+corpus['source_path']).lower() else corpus['name']
                previous=r.execute(db,"SELECT de.group_id FROM kb_document_entries de JOIN kb_core_books cb ON cb.edition_id=de.edition_id WHERE de.workspace_key=current_setting('shixue.workspace') AND cb.corpus_id=:corpus GROUP BY de.group_id ORDER BY count(*) DESC LIMIT 1",corpus=corpus['id']).first()
                group=(core_groups or {}).get(str(corpus['id'])) if str(corpus['id']) in (core_groups or {}) else previous[0] if previous else self.group(db,name)
                r.execute(db,"""INSERT INTO kb_document_entries(id,workspace_key,group_id,edition_id,asset_id,relative_path,origin_key)
                  SELECT md5(current_setting('shixue.workspace') || e.id::text || f.asset_id::text || f.source_path)::uuid,
                    current_setting('shixue.workspace'),:group,e.id,f.asset_id,f.source_path,
                    'core:' || e.id::text || ':' || f.asset_id::text || ':' || f.source_path
                  FROM kb_core_books cb JOIN kb_editions e ON e.id=cb.edition_id
                  JOIN kb_core_files f ON f.revision_id=e.active_revision WHERE cb.corpus_id=:corpus AND NOT e.deleted
                  ON CONFLICT(workspace_key,origin_key) DO NOTHING""",group=group,corpus=corpus['id'])
        if not state['initialized']:
            # Preserve old research classifications as named groups without changing their tables.
            for category in r.rows(db,"SELECT * FROM kb_literature_categories WHERE workspace_key=current_setting('shixue.workspace') ORDER BY created_at,id"):
                group=self.group(db,category['name'])
                r.execute(db,"""INSERT INTO kb_document_entries(id,workspace_key,group_id,edition_id,asset_id,relative_path,origin_key)
                  SELECT md5(current_setting('shixue.workspace') || :category || e.id::text || COALESCE(ea.asset_id::text,'') || COALESCE(f.source_path,ea.filename,w.title))::uuid,
                    current_setting('shixue.workspace'),:group,e.id,ea.asset_id,COALESCE(f.source_path,ea.filename,w.title),
                    'legacy-category:' || :category || ':' || e.id::text || ':' || COALESCE(ea.asset_id::text,'') || ':' || COALESCE(f.source_path,ea.filename,w.title)
                  FROM kb_literature_memberships m JOIN kb_works w ON w.id=m.work_id JOIN kb_editions e ON e.work_id=w.id
                  LEFT JOIN kb_edition_assets ea ON ea.edition_id=e.id
                  LEFT JOIN kb_core_files f ON f.revision_id=e.active_revision AND f.asset_id=ea.asset_id
                  WHERE m.category_id=CAST(:category AS uuid) AND NOT e.deleted
                    AND NOT EXISTS(SELECT 1 FROM kb_document_entries existing WHERE existing.workspace_key=current_setting('shixue.workspace')
                      AND existing.group_id IS NOT DISTINCT FROM CAST(:group AS uuid) AND existing.edition_id=e.id
                      AND existing.asset_id IS NOT DISTINCT FROM ea.asset_id AND existing.relative_path=COALESCE(f.source_path,ea.filename,w.title))
                  ON CONFLICT DO NOTHING""",category=str(category['id']),group=group)
            r.execute(db,"UPDATE kb_document_library_state SET initialized=true WHERE workspace_key=current_setting('shixue.workspace')")
        # Older import and metadata editors continue to appear, without modifying existing entries.
        r.execute(db,"""INSERT INTO kb_document_entries(id,workspace_key,group_id,edition_id,asset_id,relative_path,origin_key)
          SELECT md5(current_setting('shixue.workspace') || e.id::text || COALESCE(ea.asset_id::text,''))::uuid,
            current_setting('shixue.workspace'),(SELECT old.group_id FROM kb_document_entries old WHERE old.workspace_key=current_setting('shixue.workspace') AND old.edition_id=e.id AND old.asset_id IS NULL LIMIT 1),e.id,ea.asset_id,COALESCE(ea.filename,w.title),
            'catalog:' || e.id::text || ':' || COALESCE(ea.asset_id::text,'')
          FROM kb_editions e JOIN kb_works w ON w.id=e.work_id LEFT JOIN kb_edition_assets ea ON ea.edition_id=e.id
          WHERE NOT e.deleted AND w.library_id=:library
            AND NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)
            AND NOT EXISTS(SELECT 1 FROM kb_document_entries de WHERE de.workspace_key=current_setting('shixue.workspace') AND de.edition_id=e.id AND de.asset_id IS NOT DISTINCT FROM ea.asset_id)
          ON CONFLICT(workspace_key,origin_key) DO NOTHING""",library=r.DEFAULT_LIBRARY)
        # Hide obsolete metadata placeholders once actual files have been attached.
        r.execute(db,"DELETE FROM kb_document_entries de WHERE de.workspace_key=current_setting('shixue.workspace') AND de.asset_id IS NULL AND EXISTS(SELECT 1 FROM kb_edition_assets ea WHERE ea.edition_id=de.edition_id)")

    def groups(self):
        with r.database.transaction(self.root) as db:
            self.sync(db)
            items=r.rows(db,"""SELECT g.*,count(de.id) FILTER(WHERE NOT e.deleted AND """+LIVE+""") files
              FROM kb_document_groups g LEFT JOIN kb_document_entries de ON de.group_id=g.id AND de.workspace_key=g.workspace_key
              LEFT JOIN kb_editions e ON e.id=de.edition_id WHERE g.workspace_key=current_setting('shixue.workspace')
              GROUP BY g.id ORDER BY g.created_at,g.id""")
            counts=r.one(db,"SELECT count(*) total,count(*) FILTER(WHERE de.group_id IS NULL) uncategorized FROM kb_document_entries de JOIN kb_editions e ON e.id=de.edition_id WHERE de.workspace_key=current_setting('shixue.workspace') AND NOT e.deleted AND "+LIVE)
            return {'revision':self.state(db)['revision'],'items':items,**counts}

    def browse(self,scope,path='',query='',kind='all',offset=0):
        path=relative_path(path,empty=True)
        with r.database.transaction(self.root) as db:
            self.sync(db)
            values={'offset':offset,'prefix':path+'/'};where=selection_where(scope,values)
            if path:where+=" AND starts_with(de.relative_path,:prefix)"
            if kind=='ancient':where+=" AND w.item_type='ancient'"
            elif kind=='papers':where+=" AND w.item_type IN ('article','thesis')"
            if query:where+=" AND (strpos(lower(de.relative_path),lower(:query))>0 OR w.title ILIKE :q)";values.update(query=query,q='%'+query+'%')
            tail="substring(de.relative_path FROM length(:prefix)+1)" if path else 'de.relative_path'
            base=' FROM kb_document_entries de JOIN kb_editions e ON e.id=de.edition_id JOIN kb_works w ON w.id=e.work_id WHERE NOT e.deleted AND '+LIVE+' AND '+where
            # A query searches recursively; ordinary navigation shows only the current directory.
            directories=[] if query else r.rows(db,"SELECT split_part("+tail+",'/',1) name,count(*) files"+base+" AND strpos("+tail+",'/')>0 GROUP BY 1 ORDER BY 1",**values)
            file_where='' if query else " AND strpos("+tail+",'/')=0"
            files=r.rows(db,"SELECT de.*,w.title,w.item_type,e.label,e.active_revision,g.name group_name,a.size_bytes,a.availability,"
                         "EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id) is_core,"
                         "(SELECT i.state FROM kb_indexes i WHERE i.id=e.active_index) index_state"
                         +base.replace(' WHERE NOT e.deleted',' LEFT JOIN kb_document_groups g ON g.id=de.group_id LEFT JOIN kb_assets a ON a.id=de.asset_id WHERE NOT e.deleted')
                         +file_where+' ORDER BY de.relative_path,de.id OFFSET :offset LIMIT 100',**values)
            total=r.execute(db,'SELECT count(*)'+base+file_where,**values).scalar_one()
            return {'directories':[{'path':(path+'/' if path else '')+d['name'],**d} for d in directories],
                    'files':files,'path':path,'total':total,'next_offset':offset+len(files) if offset+len(files)<total else None}

    def rename(self,identifier,name,version):
        name=name.strip()
        if not name or name=='未分类':raise HTTPException(400,'请输入分类名称，“未分类”为保留名称')
        with r.database.transaction(self.root) as db:
            self.state(db)
            if r.execute(db,"SELECT id FROM kb_document_groups WHERE workspace_key=current_setting('shixue.workspace') AND lower(name)=lower(:name) AND id<>:id",name=name,id=identifier).first():raise HTTPException(409,'已有同名分类')
            value=r.execute(db,"UPDATE kb_document_groups SET name=:name,version=version+1 WHERE id=:id AND workspace_key=current_setting('shixue.workspace') AND version=:version RETURNING *",name=name,id=identifier,version=version).mappings().first()
            if not value:raise HTTPException(409,'分类已更新，请刷新后重试')
            self.advance(db);return dict(value)

    def delete(self,identifier,version):
        with r.database.transaction(self.root) as db:
            self.state(db)
            value=r.execute(db,"DELETE FROM kb_document_groups WHERE id=:id AND workspace_key=current_setting('shixue.workspace') AND version=:version RETURNING id",id=identifier,version=version).scalar()
            if not value:raise HTTPException(409,'分类已更新，请刷新后重试')
            self.advance(db);return {'documents_retained':True,'moved_to_uncategorized':True}

    def move(self,scope,target_name,revision):
        with r.database.transaction(self.root) as db:
            self.state(db,revision)
            target=self.group(db,target_name);values={'target':target};where=selection_where(scope,values)
            result=r.execute(db,'UPDATE kb_document_entries de SET group_id=:target,version=version+1 WHERE '+where,**values)
            self.advance(db);return {'files':result.rowcount,'group_id':target}

    def entry(self,identifier):
        with r.database.transaction(self.root) as db:
            entry=r.one(db,"SELECT de.*,w.title,e.active_revision,e.label FROM kb_document_entries de JOIN kb_editions e ON e.id=de.edition_id JOIN kb_works w ON w.id=e.work_id WHERE de.id=:id AND de.workspace_key=current_setting('shixue.workspace') AND NOT e.deleted",id=identifier)
            entry['pages']=r.execute(db,'SELECT COALESCE(max(page_index),0) FROM kb_pages WHERE asset_id=:asset',asset=entry['asset_id']).scalar_one() if entry['asset_id'] else 0
            return entry
