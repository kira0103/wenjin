"""Copy-and-verify migration; old task inputs remain immutable compatibility snapshots."""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from . import repository as r
from .ingestion import publish,job_gate
from .audit import read_only


def preview(root,material_ids):
    with closing(read_only(root/'library.sqlite')) as source:
        source.row_factory=sqlite3.Row
        items=[]
        for row in source.execute('SELECT m.*,lm.entry_id FROM materials m JOIN library_materials lm ON lm.material_id=m.id'):
            if material_ids and row['id'] not in material_ids:continue
            path=Path(row['asset_path'])
            if not path.is_absolute():path=root/path
            busy=source.execute("SELECT count(*) FROM tasks WHERE material_id=? AND status IN ('queued','running')",(row['id'],)).fetchone()[0]
            if source.execute("SELECT 1 FROM sqlite_master WHERE name='collation_batches'").fetchone():
                busy+=source.execute("SELECT count(*) FROM collation_items i JOIN collation_batches b ON b.id=i.batch_id WHERE i.material_id=? AND b.status IN ('running','pausing','stopping')",(row['id'],)).fetchone()[0]
            items.append({'material_id':row['id'],'entry_id':row['entry_id'],'title':row['title'],
                          'file_present':path.is_file(),'busy':bool(busy),'page_count':row['page_count']})
        missing=set(material_ids)-{item['material_id'] for item in items}
        bibliography_count=source.execute('SELECT count(*) FROM bibliography').fetchone()[0]
    return {'items':items,'blocking':bool(missing) or any(not item['file_present'] or item['busy'] for item in items),
            'unknown_ids':sorted(missing),'bibliography_count':bibliography_count,'note':'迁移后原 ID 保留；已有点校任务使用原始输入快照，资料修改请在文献库完成。'}


def migrate(root,payload,check,progress):
    plan=preview(root,payload.get('material_ids',[]))
    if plan['blocking']:raise HTTPException(409,'原件缺失或资料仍有运行任务，迁移未切换')
    # Preserve an independent SQLite snapshot before any route markers are changed.
    backup=root/'backups'/('before-knowledge-'+r.uid()+'.sqlite');backup.parent.mkdir(exist_ok=True)
    with closing(read_only(root/'library.sqlite')) as source,closing(sqlite3.connect(backup)) as target:source.backup(target)
    entry_editions={};affected=set();copied=0
    with closing(read_only(backup)) as source:
        source.row_factory=sqlite3.Row
        selected_entries={item['entry_id'] for item in plan['items']}
        for row in source.execute('SELECT * FROM bibliography'):
            book=dict(row)
            if payload.get('material_ids') and book['id'] not in selected_entries:continue
            with r.database.transaction(root) as connection:
                existing=r.execute(connection,'SELECT edition_id FROM kb_legacy_entries WHERE entry_id=:id',id=book['id']).scalar()
                if existing:
                    entry_editions[book['id']]=existing
                    # A durable ID mapping is sufficient: a resumed migration must
                    # never overwrite edits made after PostgreSQL publication.
                    continue
                new=r.create_edition(connection,{'title':book['title'],'item_type':book.get('item_type','other'),
                    'authors':[book['authors']] if book.get('authors') else [],'date_label':book.get('year',''),
                    'label':book.get('version_label',''),'source_note':book.get('raw_reference','')})
                entry_editions[book['id']]=new['id']
                r.execute(connection,'INSERT INTO kb_legacy_entries(entry_id,edition_id) VALUES(:old,:new)',old=book['id'],new=new['id'])
                for link in source.execute('SELECT project_id,roles_json FROM project_bibliography WHERE entry_id=?',(book['id'],)):
                    r.execute(connection,'INSERT INTO kb_project_editions(project_id,edition_id,roles) VALUES(:p,:e,CAST(:r AS jsonb)) ON CONFLICT DO NOTHING',p=link[0],e=new['id'],r=link[1])
        for number,item in enumerate(plan['items']):
            check();progress('copy',int(number/max(1,len(plan['items']))*80))
            material=source.execute('SELECT m.*,lm.entry_id,lm.file_format,lm.position_kind FROM materials m JOIN library_materials lm ON lm.material_id=m.id WHERE m.id=?',(item['material_id'],)).fetchone()
            path=Path(material['asset_path']);path=path if path.is_absolute() else root/path
            data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
            if digest!=material['sha256']:raise HTTPException(409,'原件哈希与旧库不一致，请先核对原件')
            digest,key=r.store_file(root,material['filename'],data)
            with r.database.transaction(root) as connection:
                old=r.execute(connection,'SELECT * FROM kb_legacy_links WHERE material_id=:id',id=item['material_id']).mappings().first()
                if old:
                    affected.add(str(old['edition_id']));continue
                book=dict(source.execute('SELECT * FROM bibliography WHERE id=?',(material['entry_id'],)).fetchone())
                existing=r.execute(connection,'SELECT edition_id FROM kb_legacy_links WHERE entry_id=:id LIMIT 1',id=material['entry_id']).scalar()
                edition_id=existing or entry_editions.get(material['entry_id'])
                if not edition_id:
                    new=r.create_edition(connection,{'title':book['title'],'item_type':book.get('item_type','other'),
                      'authors':[book['authors']] if book.get('authors') else [],'date_label':book.get('year',''),
                      'label':book.get('version_label',''),'source_note':book.get('raw_reference','')})
                    edition_id=new['id'];entry_editions[material['entry_id']]=edition_id
                asset=r.execute(connection,'INSERT INTO kb_assets(id,sha256,storage_key,size_bytes,mime_type) VALUES(:id,:h,:k,:s,:mime) '
                         'ON CONFLICT(sha256) DO UPDATE SET sha256=excluded.sha256 RETURNING id',id=r.uid(),h=digest,k=key,s=len(data),mime='application/pdf' if material['file_format']=='pdf' else 'application/octet-stream').scalar_one()
                r.execute(connection,'INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind,ordinal) '
                          'VALUES(:e,:a,:f,:p,:o) ON CONFLICT DO NOTHING',e=edition_id,a=asset,f=material['filename'],p=material['position_kind'],o=number)
                r.execute(connection,'INSERT INTO kb_legacy_links(material_id,entry_id,edition_id,asset_id,source_digest) VALUES(:m,:entry,:e,:a,:h)',
                          m=material['id'],entry=material['entry_id'],e=edition_id,a=asset,h=digest)
                for project in source.execute('SELECT project_id,roles_json FROM project_materials WHERE material_id=?',(material['id'],)):
                    r.execute(connection,'INSERT INTO kb_project_editions(project_id,edition_id,roles) VALUES(:p,:e,CAST(:r AS jsonb)) ON CONFLICT DO NOTHING',p=project[0],e=edition_id,r=project[1])
                affected.add(str(edition_id));copied+=1
        # Publish each complete entry once, including all volume files, using exact legacy text.
        for edition_id in affected:
            check();pages=[]
            with r.database.transaction(root) as connection:
                links=r.rows(connection,'SELECT * FROM kb_legacy_links WHERE edition_id=:e',e=edition_id)
                current=r.edition(connection,edition_id)
                if current['active_revision']:continue # Idempotent retry never overwrites later edits.
                for link in links:
                    for page in source.execute('SELECT page_number,text FROM pages WHERE material_id=? ORDER BY page_number',(link['material_id'],)):
                        pages.append({'asset_id':str(link['asset_id']),'page_index':page[0],'body':page[1],
                                      'quality':{'source':'legacy-snapshot','review_required':True}})
                result=publish(connection,edition_id,pages,'legacy')
                r.queue(connection,'index',{'edition_id':edition_id,'revision_id':result['revision_id']})
    # Commit PostgreSQL readiness first; SQLite only receives routing markers.
    check();progress('cutover',90)
    with r.database.transaction(root) as ownership,closing(sqlite3.connect(root/'library.sqlite',timeout=10)) as local,closing(read_only(backup)) as baseline:
        job_gate(ownership,payload)
        local.execute('BEGIN IMMEDIATE')
        for entry_id in entry_editions:
            previous=baseline.execute('SELECT * FROM bibliography WHERE id=?',(entry_id,)).fetchone()
            actual=local.execute('SELECT * FROM bibliography WHERE id=?',(entry_id,)).fetchone()
            if previous!=actual:raise HTTPException(409,'旧书目在迁移期间发生修改，未切换，请重新迁移')
        for item in plan['items']:
            previous=list(baseline.execute('SELECT page_number,text FROM pages WHERE material_id=? ORDER BY page_number',(item['material_id'],)))
            actual=list(local.execute('SELECT page_number,text FROM pages WHERE material_id=? ORDER BY page_number',(item['material_id'],)))
            if previous!=actual:raise HTTPException(409,'旧文本在迁移期间发生修改，未切换')
        for item in plan['items']:
            with r.database.transaction(root) as connection:
                link=r.one(connection,'SELECT l.*,e.active_revision FROM kb_legacy_links l JOIN kb_editions e ON e.id=l.edition_id WHERE l.material_id=:id',id=item['material_id'])
            if not link['active_revision']:raise HTTPException(409,'迁移尚未完整发布，不切换资料路由')
            local.execute('INSERT OR REPLACE INTO app_settings(setting_key,setting_value) VALUES(?,?)',
                          ('knowledge-material:'+item['material_id'],r.j({'edition_id':str(link['edition_id']),'asset_id':str(link['asset_id'])})))
            local.execute('INSERT OR REPLACE INTO app_settings(setting_key,setting_value) VALUES(?,?)',
                          ('knowledge-entry:'+item['entry_id'],str(link['edition_id'])))
        for entry_id,edition_id in entry_editions.items():
            local.execute('INSERT OR REPLACE INTO app_settings(setting_key,setting_value) VALUES(?,?)',('knowledge-entry:'+entry_id,str(edition_id)))
        local.commit()
        result={'copied':copied,'verified':len(plan['items']),'bibliographies':len(entry_editions),'snapshots_retained':True,'edition_ids':sorted(affected)}
        job_gate(ownership,payload,result)
    return result


def lookup(root,material_id):
    import json
    with closing(read_only(root/'library.sqlite')) as connection:
        row=connection.execute('SELECT setting_value FROM app_settings WHERE setting_key=?',('knowledge-material:'+material_id,)).fetchone()
    return json.loads(row[0]) if row else None


def material_detail(root,material_id,legacy):
    link=lookup(root,material_id)
    if not link:return legacy
    with r.database.transaction(root) as connection:
        current=r.edition(connection,link['edition_id'])
        blocks=r.rows(connection,'SELECT page_index,body FROM kb_blocks WHERE revision_id=:r AND asset_id=:a ORDER BY ordinal',r=current['active_revision'],a=link['asset_id'])
    grouped={}
    for block in blocks:grouped.setdefault(block['page_index'],[]).append(block['body'])
    legacy.update(title=current['title'],item_type=current['item_type'],version_label=current['label'],
                  pages=[{'page_number':n,'text':'\n\n'.join(values)} for n,values in sorted(grouped.items())],
                  knowledge_edition_id=str(current['id']))
    return legacy


def guard_entry(connection,entry_id):
    if connection.execute('SELECT 1 FROM app_settings WHERE setting_key=?',('knowledge-entry:'+entry_id,)).fetchone():
        raise HTTPException(409,'该资料已迁移至文献库，请在文献库修改；旧点校输入快照保持不变')


def bridge_snapshot(root,identifier,payload,legacy_connection):
    from datetime import datetime,timezone
    from .ingestion import parse
    with r.database.transaction(root) as connection:
        edition=r.edition(connection,identifier)
        if not edition['active_revision']:raise HTTPException(409,'资料尚未完成文本处理')
        blocks=r.rows(connection,'SELECT body,asset_id,page_index FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=edition['active_revision'])
        files={str(item['asset_id']):item['filename'] for item in r.rows(connection,'SELECT asset_id,filename FROM kb_edition_assets WHERE edition_id=:e',e=identifier)}
        created=r.one(connection,'SELECT created_at FROM kb_revisions WHERE id=:id',id=edition['active_revision'])['created_at'].isoformat()
    body='【文献库固定输入快照】\n'+edition['title']+' · '+edition['label']+'\n来源文本修订时间：'+created+'\n\n'
    body+='\n\n'.join(f"[{files.get(str(item['asset_id']),'原件')} 第 {item['page_index']} 页 / 段]\n{item['body']}" for item in blocks)
    data=body.encode('utf-8');digest,key=r.store_file(root,'输入快照.txt',data)
    parsed,_=parse('输入快照.txt',data)
    timestamp=datetime.now(timezone.utc).isoformat();material_id=r.uid();entry_id=r.uid()
    with legacy_connection() as connection:
        existing=connection.execute('SELECT id FROM materials WHERE sha256=? AND project_id=?',(digest,payload.project_id)).fetchone()
        if existing:material_id=existing['id']
        else:
            connection.execute('INSERT INTO bibliography(id,title,authors,year,raw_reference,item_type,version_label,created_at) VALUES(?,?,?,?,?,?,?,?)',
              (entry_id,edition['title']+' · 固定输入快照','、'.join(edition['authors_json']),edition['date_label'],'来源文献库 '+str(identifier),'other',edition['label'],timestamp))
            connection.execute('INSERT INTO materials(id,project_id,filename,title,sha256,asset_path,page_count,extraction_coverage,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(material_id,payload.project_id,'文献库输入快照.txt',edition['title']+' · 输入快照',digest,str(root/key),len(parsed),1,timestamp))
            connection.execute('INSERT INTO library_materials(material_id,entry_id,file_format,position_kind,encoding) VALUES(?,?,?,?,?)',(material_id,entry_id,'txt','section','utf-8'))
            connection.executemany('INSERT INTO pages(material_id,page_number,text) VALUES(?,?,?)',[(material_id,n,body) for n,body,_ in parsed])
        connection.execute('INSERT OR REPLACE INTO project_materials(project_id,material_id,roles_json) VALUES(?,?,?)',(payload.project_id,material_id,r.j(payload.roles)))
    return {'material_id':material_id,'revision_id':str(edition['active_revision']),'snapshot':True}
