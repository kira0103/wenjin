from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from .database import database
from .text import tokenize

DEFAULT_LIBRARY = '00000000-0000-0000-0000-000000000001'


def uid():
    return str(uuid.uuid4())


def j(value):
    return json.dumps(value, ensure_ascii=False)


def execute(connection, statement, **values):
    return connection.execute(text(statement), values)


def rows(connection, statement, **values):
    return [dict(row) for row in execute(connection, statement, **values).mappings()]


def one(connection, statement, **values):
    value = execute(connection, statement, **values).mappings().first()
    if value is None:
        raise HTTPException(404, '找不到目标资料')
    return dict(value)


def edition(connection, identifier, deleted=False):
    return one(connection, 'SELECT e.*,w.title,w.item_type,w.authors_json,w.date_label,w.version work_version FROM kb_editions e '
               'JOIN kb_works w ON w.id=e.work_id WHERE e.id=:id' + ('' if deleted else ' AND NOT e.deleted'), id=identifier)


def create_edition(connection, payload):
    execute(connection, 'INSERT INTO kb_libraries(id,name) VALUES(:id,:name) ON CONFLICT DO NOTHING', id=DEFAULT_LIBRARY, name='我的文献库')
    work_id = payload.get('work_id') or uid()
    if payload.get('work_id'):
        one(connection, 'SELECT id FROM kb_works WHERE id=:id AND library_id=:lib', id=work_id, lib=DEFAULT_LIBRARY)
    else:
        execute(connection, 'INSERT INTO kb_works(id,library_id,title,item_type,authors_json,date_label) '
                'VALUES(:id,:lib,:title,:kind,CAST(:authors AS jsonb),:date)', id=work_id, lib=DEFAULT_LIBRARY,
                title=payload['title'].strip(), kind=payload.get('item_type','other'), authors=j(payload.get('authors',[])), date=payload.get('date_label',''))
    identifier = uid()
    execute(connection, 'INSERT INTO kb_editions(id,work_id,label,publisher,publication_year,doi,source_note,tags) '
            'VALUES(:id,:work,:label,:publisher,:year,:doi,:note,CAST(:tags AS jsonb))', id=identifier,work=work_id,
            label=payload.get('label',''), publisher=payload.get('publisher',''),year=payload.get('publication_year'),doi=payload.get('doi',''),
            note=payload.get('source_note',''),tags=j(payload.get('tags',[])))
    return edition(connection, identifier)


def list_editions(connection, query='', project_id=None, before=None, limit=100):
    where = 'NOT e.deleted AND w.library_id=:lib'
    if query:
        where += " AND (w.title ILIKE :q OR e.label ILIKE :q OR w.authors_json::text ILIKE :q)"
    if project_id:
        where += ' AND EXISTS(SELECT 1 FROM kb_project_editions p WHERE p.edition_id=e.id AND p.project_id=:project)'
    if before:
        where += ' AND e.id::text < :before'
    return rows(connection, 'SELECT e.*,w.title,w.item_type,w.authors_json,w.date_label, '
                '(SELECT count(*) FROM kb_edition_assets a WHERE a.edition_id=e.id) file_count, '
                '(SELECT state FROM kb_indexes i WHERE i.id=e.active_index) index_state '
                'FROM kb_editions e JOIN kb_works w ON w.id=e.work_id WHERE ' + where + ' ORDER BY e.id::text DESC LIMIT :limit',
                lib=DEFAULT_LIBRARY,q='%'+query+'%',project=project_id,before=before,limit=limit)


def asset_path(root: Path, key: str):
    base = root.resolve()
    path = (base / key).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, '原件不可读取')
    return path


def store_file(root, filename, data):
    if not data or len(data)>250*1024*1024:
        raise HTTPException(400, '文件为空或超过 250 MB')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf','.txt','.md','.docx','.doc','.png','.jpg','.jpeg','.tif','.tiff'}:
        raise HTTPException(400,'支持 PDF、Word、TXT 与扫描图片')
    digest = hashlib.sha256(data).hexdigest()
    key = f'assets/knowledge/{digest[:2]}/{digest}{suffix}'
    target = root / key
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        temporary = target.with_name(target.name+'.'+uid()+'.tmp')
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return digest,key


def queue(connection, kind, payload):
    identifier = uid()
    execute(connection, "INSERT INTO kb_jobs(id,kind,payload,workspace_key) VALUES(:id,:kind,CAST(:payload AS jsonb),current_setting('shixue.workspace'))",
            id=identifier,kind=kind,payload=j(payload))
    return one(connection,'SELECT * FROM kb_jobs WHERE id=:id',id=identifier)


def anchor(connection, block_id, start, end):
    block = one(connection, 'SELECT b.*,r.edition_id,e.deleted,w.title,e.label FROM kb_blocks b '
                'JOIN kb_revisions r ON r.id=b.revision_id JOIN kb_editions e ON e.id=r.edition_id '
                'JOIN kb_works w ON w.id=e.work_id WHERE b.id=:id', id=block_id)
    if block['deleted']:
        raise HTTPException(410,'来源已删除，无法新建引用')
    if not 0 <= start < end <= len(block['body']):
        raise HTTPException(400,'摘录范围无效')
    quote = block['body'][start:end]
    locator = {'asset_id':str(block['asset_id']),'page_index':block['page_index'],'revision_id':str(block['revision_id']),
               'title':block['title'],'edition_label':block['label'],'section_path':block['section_path'],
               'start':start,'end':end,'quality':block['quality']}
    labels=execute(connection,'SELECT printed_label,volume_label,leaf_label,side FROM kb_page_labels '
                   'WHERE edition_id=:e AND asset_id=:a AND page_index=:p',e=block['edition_id'],a=block['asset_id'],p=block['page_index']).mappings().first()
    if labels:
        locator.update(dict(labels))
    identifier=uid()
    execute(connection,'INSERT INTO kb_anchors(id,edition_id,block_id,start_offset,end_offset,quote,quote_hash,locator) '
            'VALUES(:id,:e,:b,:s,:n,:quote,:hash,CAST(:loc AS jsonb))',id=identifier,e=block['edition_id'],b=block_id,
            s=start,n=end,quote=quote,hash=hashlib.sha256(quote.encode()).hexdigest(),loc=j(locator))
    return one(connection,'SELECT * FROM kb_anchors WHERE id=:id',id=identifier)


def save_note(connection, identifier, payload):
    if identifier:
        previous=one(connection,'SELECT * FROM kb_notes WHERE id=:id AND NOT deleted FOR UPDATE',id=identifier)
        if previous['version']!=payload['version']:
            raise HTTPException(409,'笔记已更新，请重新读取后合并修改')
        version=previous['version']+1
        execute(connection,'UPDATE kb_notes SET title=:title,body=:body,note_type=:kind,status=:status,tags=CAST(:tags AS jsonb),'
                'version=:v,updated_at=now(),tokens=to_tsvector(\'simple\',:tokens) WHERE id=:id',id=identifier,v=version,
                title=payload['title'],body=payload['body'],kind=payload['note_type'],status=payload['status'],tags=j(payload['tags']),
                tokens=tokenize(payload['title']+' '+payload['body']))
    else:
        identifier=uid();version=1
        execute(connection,'INSERT INTO kb_notes(id,title,body,note_type,status,tags,tokens) '
                'VALUES(:id,:title,:body,:kind,:status,CAST(:tags AS jsonb),to_tsvector(\'simple\',:tokens))',
                id=identifier,title=payload['title'],body=payload['body'],kind=payload['note_type'],status=payload['status'],
                tags=j(payload['tags']),tokens=tokenize(payload['title']+' '+payload['body']))
    execute(connection,'INSERT INTO kb_note_revisions(note_id,version,title,body,origin) VALUES(:id,:v,:title,:body,:origin)',
            id=identifier,v=version,title=payload['title'],body=payload['body'],origin=payload.get('origin','human'))
    execute(connection,'DELETE FROM kb_note_sources WHERE note_id=:id',id=identifier)
    for source in payload.get('sources',[]):
        one(connection,'SELECT id FROM kb_anchors WHERE id=:id',id=source['anchor_id'])
        execute(connection,'INSERT INTO kb_note_sources(note_id,anchor_id,relation,comment) VALUES(:n,:a,:r,:c)',
                n=identifier,a=source['anchor_id'],r=source.get('relation','citation'),c=source.get('comment',''))
    return one(connection,'SELECT * FROM kb_notes WHERE id=:id',id=identifier)
