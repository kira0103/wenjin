"""Local ancient-text corpora: one catalog authority, derived lexical index only.

Offsets are Unicode code points into the immutable extracted transcript. Each
primary 6000-character range carries 200 characters of lookahead, so queries of
up to 160 characters cannot fall through an artificial passage boundary.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import numpy as np
import yaml
from fastapi import HTTPException, Query
from opencc import OpenCC
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import repository as r

CATEGORIES = ('儒藏','史藏','子藏','集藏','诗藏','艺藏','易藏','医藏','佛藏','道藏','四庫全書')
PRIMARY = 6000
LOOKAHEAD = 200
_converter = OpenCC('t2s')
_html_tag = re.compile(r'</?[A-Za-z][A-Za-z0-9]*(?:[\s/>])')


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=0

    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.hidden+=1
        elif not self.hidden and tag=='br':self.parts.append('\n')

    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
        elif not self.hidden and tag in {'p','div','tr','li','h1','h2','h3','h4','h5','h6'}:
            if self.parts and not self.parts[-1].endswith('\n'):self.parts.append('\n')

    def handle_data(self,value):
        if not self.hidden:self.parts.append(value)


def readable_body(value):
    """Extract visible HTML text, keeping annotations and existing line breaks.

    This is a derived transcript, never a rewrite of the content-addressed raw
    attachment. Offsets refer to this extracted transcript, not HTML bytes.
    """
    if not _html_tag.search(value):return value
    parser=PlainHTML();parser.feed(value);parser.close()
    return ''.join(parser.parts)


class GlyphMap(dict):
    def __missing__(self, code):
        original = chr(code)
        converted = _converter.convert(original).lower()
        # A one-code-point mapping makes source locations exact, including astral
        # characters. Phrase conversion is deliberately not applied to names.
        self[code] = converted if len(converted) == 1 else original
        return self[code]


_glyphs = GlyphMap()


def canonical(value):
    return value.translate(_glyphs)


def terms(value):
    """Compact, deterministic unigram/bigram candidates, followed by exact checks.

    Collisions may add candidates but cannot manufacture a final match. A custom
    C extension and a Chinese tokenizer are unnecessary for literal retrieval.
    """
    codes = np.frombuffer(value.encode('utf-32-le'), dtype='<u4').astype(np.int64)
    if not len(codes):
        return []
    pairs = 0x200000 + (((codes[:-1] * 1103515245) ^ codes[1:]) & 0x3fffffff)
    return np.unique(np.concatenate((codes, pairs))).astype(np.int32).tolist()


def parse_markdown(raw, fallback):
    value = raw.decode('utf-8-sig', errors='strict')
    if '\x00' in value:
        raise ValueError('原文件含空字符，须核验后再导入')
    metadata = {}
    offset = 0
    if value.startswith(('---\n','---\r\n')):
        close = re.search(r'(?m)^---[ \t]*\r?$', value[4:65536])
        if not close:
            raise ValueError('Markdown 元数据未闭合或超过 64 KB')
        end = 4 + close.start()
        metadata = yaml.safe_load(value[value.index('\n')+1:end]) or {}
        if not isinstance(metadata, dict):
            raise ValueError('Markdown 元数据格式错误')
        offset = 4 + close.end()
        if value[offset:offset+1] == '\n':
            offset += 1
    title = metadata.get('title') or fallback
    if isinstance(title, dict):
        title = title.get('zh-hant') or title.get('zh-hans') or fallback
    # Dates in YAML become datetime/date values; preserve their textual value.
    metadata = json.loads(json.dumps(metadata, ensure_ascii=False, default=str))
    return str(title), metadata, value[offset:], offset


def inventory(source):
    source = Path(source).resolve()
    if not source.is_dir():
        raise HTTPException(400, '古籍源目录不存在')
    files = []
    for category in CATEGORIES:
        folder = source/category
        if not folder.is_dir():
            continue
        for file in folder.rglob('*.md'):
            rel = file.relative_to(source)
            if any(p.startswith('.') for p in rel.parts) or file.is_symlink():
                continue
            if source not in file.resolve().parents:
                raise HTTPException(400, '古籍文件超出源目录')
            files.append(rel.as_posix())
    if not files:
        raise HTTPException(400, '未找到支持的藏部 Markdown 文件')
    paths = set(files)
    # Some Kanripo imports contain numbered parts only, with no parent .md.
    # Group only a uniformly numeric part directory, not arbitrary categories.
    numbered={}
    for value in files:numbered.setdefault(Path(value).parent.as_posix(),[]).append(value)
    numbered={parent:items for parent,items in numbered.items() if len(items)>1 and len(Path(parent).parts)>=3
              and parent+'.md' not in paths and all(re.fullmatch(r'\d+(?:_\d+)*',Path(p).stem) for p in items)}
    for parent,items in list(numbered.items()):
        identifiers=set()
        for file in items:
            with (source/file).open('r',encoding='utf-8-sig') as stream:header=stream.read(8192)
            match=re.search(r"(?m)^\s*-\s*['\"]?ID:\s*(KR[A-Za-z0-9]+)",header)
            identifiers.add(match.group(1) if match else None)
        if len(identifiers)!=1 or None in identifiers:del numbered[parent]
    books = {}
    for value in sorted(files):
        rel = Path(value)
        key = value
        # Outermost actual parent book wins; category folders are never works.
        for parent in reversed(rel.parents[:-1]):
            candidate = parent.as_posix()+'.md'
            if candidate in paths:
                key = candidate
                break
        if key==value and rel.parent.as_posix() in numbered:
            key=rel.parent.as_posix()+'/@parts'
        books.setdefault(key, []).append(value)
    head = source/'.git/HEAD'
    revision = ''
    if head.is_file():
        content = head.read_text(encoding='utf-8').strip()
        if content.startswith('ref: '):
            reference=content[5:]
            ref = source/'.git'/reference
            if re.fullmatch(r'refs/[A-Za-z0-9._/-]+',reference) and '..' not in Path(reference).parts and ref.is_file():
                revision = ref.read_text(encoding='utf-8').strip()
        elif re.fullmatch('[0-9a-f]{40,64}', content):
            revision = content
    return books, revision


def status(root):
    with r.database.transaction(root) as c:
        items=r.rows(c, 'SELECT * FROM kb_corpora ORDER BY name')
        active=r.execute(c,"SELECT COALESCE(payload->>'slug','daizhige') FROM kb_jobs WHERE kind='core_import' AND state IN ('queued','running') AND workspace_key=current_setting('shixue.workspace')").scalars().all()
        for item in items:
            if item['state']=='importing' and item['slug'] not in active:item['state']='paused'
        return items


def _copy(c, table, columns, values):
    if not values:
        return
    with c.connection.driver_connection.cursor() as cursor:
        with cursor.copy('COPY '+table+' ('+','.join(columns)+') FROM STDIN') as stream:
            for row in values:
                stream.write_row(row)


def _fence(c, payload):
    if payload.get('_job_id'):
        item = r.execute(c, "SELECT id FROM kb_jobs WHERE id=:id AND owner=:owner AND state='running' "
                         'AND lease_until>clock_timestamp() FOR UPDATE', id=payload['_job_id'], owner=payload['_job_owner']).first()
        if not item:
            from .worker import Cancelled
            raise Cancelled()


def _import_book(root, source, corpus_id, source_revision, key, paths, payload, check):
    # Read once per file; hold at most one logical book. A repeated run verifies
    # content hashes rather than relying on mutable filesystem mtimes.
    loaded = []
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: (p != key, p)):
        check()
        file = source/path
        if file.is_symlink() or source not in file.resolve().parents:
            raise ValueError('古籍文件超出源目录')
        raw = file.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        digest.update(path.encode()); digest.update(bytes.fromhex(sha))
        loaded.append((path, raw, sha))
    if any(re.search(rb'</?[A-Za-z][A-Za-z0-9]*(?:[\s/>])',raw) for _,raw,_ in loaded):
        digest.update(b'\x00visible-html-v1')
    fingerprint = digest.hexdigest()
    with r.database.transaction(root) as c:
        previous = r.execute(c, 'SELECT cb.*,e.active_revision FROM kb_core_books cb JOIN kb_editions e ON e.id=cb.edition_id '
                             'WHERE cb.corpus_id=:c AND cb.source_key=:k', c=corpus_id, k=key).mappings().first()
        if previous and previous['fingerprint'] == fingerprint:
            return {'skipped': True, 'files': len(paths), 'bytes': sum(len(v[1]) for v in loaded)}
        if previous and previous['active_revision'] != previous['imported_revision']:
            raise ValueError('此书已有人工修订，自动更新已停止，请保留并核验版本')
    title, metadata, _, _ = parse_markdown(loaded[0][1], Path(key).stem)
    if key.endswith('/@parts'):
        # Preserve part metadata separately; the parent title is the actual
        # source directory, not the first numbered volume's title.
        title=Path(key).parent.name
        metadata={**metadata,'grouping':'numbered-parts-directory','group_title':title}
    authors = metadata.get('authors') or []
    author_names = [str(a.get('name','')) if isinstance(a, dict) else str(a) for a in authors]
    dynasties = list(dict.fromkeys(str(a['dynasty']) for a in authors if isinstance(a, dict) and a.get('dynasty')))
    category = key.split('/')[0]
    byte_count = sum(len(v[1]) for v in loaded)
    with r.database.transaction(root) as c:
        r.execute(c, "SET LOCAL statement_timeout='10min'")
        r.execute(c, "SET LOCAL gin_pending_list_limit='64MB'")
        if previous:
            edition_id = previous['edition_id']
            current = r.one(c, 'SELECT active_revision FROM kb_editions WHERE id=:id FOR UPDATE', id=edition_id)
            if current['active_revision'] != previous['imported_revision']:
                raise ValueError('导入期间原文版本发生变化，请重新核验')
        else:
            edition = r.create_edition(c, {'title':title, 'item_type':'ancient','authors':author_names,
                'date_label':'、'.join(dynasties), 'label':'殆知阁数字录文',
                'tags':['核心古籍', category], 'source_note':str(metadata.get('daizhige_url',''))})
            edition_id = edition['id']
        revision_id = r.uid()
        r.execute(c, "INSERT INTO kb_revisions(id,edition_id,source_kind,parent_id,config) VALUES(:id,:e,'core-markdown',:parent,CAST(:config AS jsonb))",
                  id=revision_id, e=edition_id, parent=previous['imported_revision'] if previous else None,
                  config=r.j({'corpus_id':str(corpus_id),'source_revision':source_revision,'fingerprint':fingerprint,
                              'source_key':key,'normalization':'one-code-point-t2s-v1','primary':PRIMARY,'lookahead':LOOKAHEAD}))
        ordinal = 0
        assets_seen = set()
        for file_ordinal, (path, raw, sha) in enumerate(loaded):
            check()
            part_title, part_metadata, body, body_offset = parse_markdown(raw, Path(path).stem)
            body=readable_body(body)
            asset = r.execute(c, 'SELECT id,storage_key FROM kb_assets WHERE sha256=:sha', sha=sha).mappings().first()
            if asset:
                asset_id = asset['id']
            else:
                asset_id = r.uid()
                _, storage_key = r.store_file(root, Path(path).name, raw)
                r.execute(c, "INSERT INTO kb_assets(id,sha256,storage_key,size_bytes,mime_type) VALUES(:id,:sha,:key,:size,'text/markdown')",
                          id=asset_id, sha=sha, key=storage_key, size=len(raw))
            r.execute(c, 'INSERT INTO kb_core_files(revision_id,source_path,asset_id,title,metadata,body_offset) '
                        'VALUES(:rev,:p,:a,:t,CAST(:m AS jsonb),:o)', rev=revision_id,p=path,a=asset_id,t=part_title,m=r.j(part_metadata),o=body_offset)
            if str(asset_id) not in assets_seen:
                r.execute(c, "INSERT INTO kb_edition_assets(edition_id,asset_id,filename,position_kind,ordinal) VALUES(:e,:a,:f,'section',:o) ON CONFLICT DO NOTHING",
                          e=edition_id,a=asset_id,f=path,o=file_ordinal)
                assets_seen.add(str(asset_id))
            pages=[];blocks=[];passages=[]
            # A total-volume TOC is retained as original evidence with its own
            # location, and is labelled as such rather than treated as a volume.
            for page, start in enumerate(range(0, len(body), PRIMARY), 1):
                segment = body[start:start+PRIMARY+LOOKAHEAD]
                original_length = min(PRIMARY, len(body)-start)
                normalized = canonical(segment)
                block_id = r.uid()
                pages.append((asset_id,page))
                blocks.append((block_id,revision_id,asset_id,page,ordinal,'body',part_title,segment,
                               Jsonb({'source':'core-markdown','review_required':True,'source_path':path,
                                      'source_start':start,'source_end':start+len(segment),'body_offset':body_offset,
                                      'primary_length':original_length,'position_basis':'extracted-transcript-v1'})))
                index_terms = np.union1d(terms(segment), terms(normalized)).astype(np.int32).tolist()
                passages.append((block_id,corpus_id,edition_id,revision_id,category,path,start,original_length,normalized,index_terms))
                ordinal += 1
            # Assets can be byte-identical; their immutable page positions are
            # reusable, whereas each source occurrence has a distinct block.
            with c.connection.driver_connection.cursor() as cursor:
                cursor.executemany('INSERT INTO kb_pages(asset_id,page_index) VALUES(%s,%s) ON CONFLICT DO NOTHING', pages)
            _copy(c, 'kb_blocks', ['id','revision_id','asset_id','page_index','ordinal','kind','section_path','body','quality'], blocks)
            _copy(c, 'kb_core_passages', ['block_id','corpus_id','edition_id','revision_id','category','source_path','source_start','primary_length','normalized','terms'], passages)
        index_id = r.uid()
        r.execute(c, "INSERT INTO kb_indexes(id,revision_id,state,config) VALUES(:id,:rev,'keyword',CAST(:config AS jsonb))",
                  id=index_id,rev=revision_id,config=r.j({'kind':'core-lexical','normalization':'one-code-point-t2s-v1'}))
        _fence(c,payload)
        r.execute(c, 'UPDATE kb_editions SET active_revision=:rev,active_index=:idx,version=version+1 WHERE id=:e', rev=revision_id,idx=index_id,e=edition_id)
        r.execute(c,'DELETE FROM kb_core_passages WHERE edition_id=:e AND revision_id<>:rev',e=edition_id,rev=revision_id)
        r.execute(c, 'INSERT INTO kb_core_books(corpus_id,source_key,edition_id,category,fingerprint,imported_revision,file_count,byte_count,metadata) '
                    'VALUES(:c,:key,:e,:cat,:f,:rev,:n,:bytes,CAST(:m AS jsonb)) ON CONFLICT(corpus_id,source_key) DO UPDATE SET '
                    'fingerprint=excluded.fingerprint,imported_revision=excluded.imported_revision,file_count=excluded.file_count,byte_count=excluded.byte_count,metadata=excluded.metadata',
                  c=corpus_id,key=key,e=edition_id,cat=category,f=fingerprint,rev=revision_id,n=len(paths),bytes=byte_count,m=r.j(metadata))
    return {'skipped':False,'files':len(paths),'bytes':byte_count,'passages':ordinal}


def process_import(root, payload, check=lambda:None, progress=lambda *a:None):
    source = Path(payload['source_path']).resolve()
    books, revision = inventory(source)
    slug = payload.get('slug','daizhige')
    with r.database.transaction(root) as c:
        corpus = r.one(c, 'INSERT INTO kb_corpora(id,slug,name,source_path,source_revision,state) '
                       "VALUES(:id,:s,:n,:p,:rev,'importing') ON CONFLICT(slug) DO UPDATE SET source_path=excluded.source_path,"
                       "source_revision=excluded.source_revision,state='importing',errors='[]',updated_at=clock_timestamp() RETURNING *",
                       id=r.uid(),s=slug,n=payload.get('name','殆知阁核心古籍库'),p=str(source),rev=revision)
    corpus_id=corpus['id'];errors=[];skipped=0
    engine = r.database.engine(root)
    with engine.connect() as guard:
        key=int.from_bytes(hashlib.sha256(str(corpus_id).encode()).digest()[:8], 'big', signed=True)
        acquired = r.execute(guard,'SELECT pg_try_advisory_lock(:key)',key=key).scalar_one()
        guard.commit()
        if not acquired:
            raise HTTPException(409,'此核心库已有导入任务正在处理')
        try:
            for i,(book,paths) in enumerate(books.items()):
                check();progress('core_import',int(i*98/len(books)))
                try:
                    result=_import_book(root,source,corpus_id,revision,book,paths,payload,check)
                    skipped+=int(result['skipped'])
                except (ValueError, UnicodeError, yaml.YAMLError) as error:
                    errors.append({'source_key':book,'error':str(error)[:300]})
                if i%50==0 or i==len(books)-1:
                    with r.database.transaction(root) as c:
                        r.execute(c,'UPDATE kb_corpora SET statistics=CAST(:s AS jsonb),errors=CAST(:e AS jsonb),updated_at=clock_timestamp() WHERE id=:id',
                                  id=corpus_id,s=r.j({'source_books':len(books),'source_files':sum(map(len,books.values())),
                                                    'processed':i+1,'failed_books':len(errors),'skipped':skipped}),e=r.j(errors[:200]))
            with r.database.transaction(root) as c:
                counts=r.one(c,'SELECT count(*) books,COALESCE(sum(file_count),0) files,COALESCE(sum(byte_count),0) bytes FROM kb_core_books WHERE corpus_id=:id',id=corpus_id)
                counts={k:int(v) for k,v in counts.items()}
                counts.update(source_books=len(books),source_files=sum(map(len,books.values())),failed_books=len(errors),skipped=skipped,
                              excluded=['现代作品','.sources','.git','.github','根目录说明文件'])
                categories=r.rows(c,'SELECT category,count(*) books,sum(file_count) files FROM kb_core_books WHERE corpus_id=:id GROUP BY category ORDER BY category',id=corpus_id)
                counts['categories']=[{**x,'books':int(x['books']),'files':int(x['files'])} for x in categories]
                _fence(c,payload)
                r.execute(c,'UPDATE kb_corpora SET state=:state,statistics=CAST(:s AS jsonb),errors=CAST(:e AS jsonb),updated_at=clock_timestamp() WHERE id=:id',
                          id=corpus_id,state='ready' if not errors else 'partial',s=r.j(counts),e=r.j(errors[:200]))
            return {'corpus_id':str(corpus_id),**counts}
        finally:
            r.execute(guard,'SELECT pg_advisory_unlock(:key)',key=key);guard.commit()


def books(root, query='', categories=None, cursor='', limit=30, corpus_id=None):
    values={'n':limit+1,'query':canonical(query),'cursor':cursor}
    where='NOT e.deleted AND cb.source_key>:cursor'
    if corpus_id:
        where+=' AND cb.corpus_id=:corpus';values['corpus']=corpus_id
    if categories:
        where+=' AND cb.category=ANY(:categories)';values['categories']=categories
    # There are ~16k metadata rows, so metadata folding is inexpensive and avoids
    # inventing authors for files without an author declaration.
    with r.database.transaction(root) as c:
        rows=r.rows(c,'SELECT cb.*,w.title,w.authors_json,w.date_label,e.label FROM kb_core_books cb JOIN kb_editions e ON e.id=cb.edition_id '
                    'JOIN kb_works w ON w.id=e.work_id WHERE '+where+' ORDER BY cb.source_key',**values)
    if query:
        rows=[x for x in rows if canonical(query) in canonical(x['title']+' '+r.j(x['authors_json'])+' '+x['source_key']+' '+r.j(x['metadata'].get('title',{})))]
    return {'items':rows[:limit], 'next_cursor':rows[limit-1]['source_key'] if len(rows)>limit else None}


def _cursor_decode(cursor, identity):
    if not cursor:return 0,-1
    try:
        value=json.loads(base64.urlsafe_b64decode(cursor))
        if value['identity']!=identity:
            raise HTTPException(409,'检索范围或古籍版本已变化，请重新检索')
        identifier,offset=int(value['id']),int(value['offset'])
        if identifier<0 or offset<0:raise ValueError()
        return identifier,offset
    except HTTPException:raise
    except Exception:raise HTTPException(400,'检索游标无效') from None


def search_rows(c, query, mode='literal', categories=None, edition_id=None, corpus_id=None, limit=20, cursor=None, scope_where='', scope_values=None, one_per_block=False, edition_ids=None):
    if not query.strip() or len(query)>160:
        raise HTTPException(400,'请输入 1 至 160 个字的检索内容')
    needle=query if mode=='literal' else canonical(query)
    words=needle.split() if mode=='normal' else [needle]
    if not words:raise HTTPException(400,'查询不能为空')
    identity=hashlib.sha256(r.j([query,mode,categories,str(edition_id),str(corpus_id),
        [(str(x['id']),str(x['updated_at'])) for x in r.rows(c,'SELECT id,updated_at FROM kb_corpora ORDER BY id')]]).encode()).hexdigest()
    after,offset=_cursor_decode(cursor,identity)
    values={**(scope_values or {}),'terms':sorted(set(t for word in words for t in terms(word))),'after':after,'batch':max(128,limit*3)}
    where='NOT e.deleted AND e.active_revision=p.revision_id AND p.id>=:after AND p.terms @> CAST(:terms AS integer[])'
    haystack='b.body' if mode=='literal' else 'p.normalized'
    verification=''
    for i,word in enumerate(words):
        values['word'+str(i)]=word
        verification+=f' AND strpos({haystack},:word{i})>0'
    verification+=f' AND strpos({haystack},:word0)<=p.primary_length'
    if categories:
        where+=' AND p.category=ANY(:cats)';values['cats']=categories
    if edition_id:
        where+=' AND p.edition_id=:edition';values['edition']=edition_id
    if corpus_id:
        where+=' AND p.corpus_id=:corpus';values['corpus']=corpus_id
    if scope_where:where+=' AND '+scope_where
    if edition_ids is not None:
        where+=' AND p.edition_id=ANY(CAST(:core_editions AS uuid[]))';values['core_editions']=edition_ids
    verification_where=where.replace(' AND p.terms @> CAST(:terms AS integer[])','')
    probe=len(needle)==1
    found=[]
    while True:
        # Select indexed identities before touching toasted transcript bodies.
        # A single common character must not force reading gigabytes of text
        # merely to return the first page. Continue after false candidates so
        # candidate windows never become a silent recall limit.
        if probe:
            # Common one-character queries often match the very first passages.
            # Probe a bounded identity window before asking GIN to enumerate a
            # near-corpus-wide posting list. Rare characters still fall through
            # to the indexed path after this first window.
            bounds='p.id>=:after'
            if categories:bounds+=' AND p.category=ANY(:cats)'
            if edition_id:bounds+=' AND p.edition_id=:edition'
            if corpus_id:bounds+=' AND p.corpus_id=:corpus'
            if edition_ids is not None:bounds+=' AND p.edition_id=ANY(CAST(:core_editions AS uuid[]))'
            candidates=r.rows(c,'SELECT p.id FROM kb_core_passages p WHERE '+bounds+' ORDER BY p.id LIMIT :batch',**values)
            probe=False
        else:
            candidates=r.rows(c,'SELECT p.id FROM kb_core_passages p JOIN kb_blocks b ON b.id=p.block_id '
                              'JOIN kb_editions e ON e.id=p.edition_id WHERE '+where+' ORDER BY p.id LIMIT :batch',**values)
        if not candidates:break
        values['candidate_ids']=[v['id'] for v in candidates]
        rows=r.rows(c,'SELECT p.id passage_id,p.primary_length,p.source_path,p.source_start,p.normalized,b.id block_id,b.body,b.body full_body,'
                    'b.ordinal,b.asset_id,b.page_index,b.kind,b.section_path,b.quality,b.revision_id,e.id edition_id,e.label,w.title,p.category '
                    'FROM kb_core_passages p JOIN kb_blocks b ON b.id=p.block_id JOIN kb_editions e ON e.id=p.edition_id JOIN kb_works w ON w.id=e.work_id '
                    'WHERE p.id=ANY(CAST(:candidate_ids AS bigint[])) AND '+verification_where+verification+' ORDER BY p.id',**values)
        for row in rows:
            text=row['body'] if mode=='literal' else row['normalized']
            start=0
            while True:
                start=text.find(words[0],start)
                if start<0 or start>=row['primary_length']:break
                if row['passage_id']>after or start>offset:
                    found.append({**row,'start_offset':start,'end_offset':start+len(words[0]),'source_kind':'document'})
                    if len(found)>limit:break
                if mode=='normal' or one_per_block:break
                start+=1 # All overlapping occurrences, stable source positions.
            if len(found)>limit:break
        if len(found)>limit or len(candidates)<values['batch']:break
        values['after']=candidates[-1]['id']+1
    next_cursor=None
    if len(found)>limit:
        last=found[limit-1]
        next_cursor=base64.urlsafe_b64encode(r.j({'identity':identity,'id':last['passage_id'],'offset':last['start_offset']}).encode()).decode()
    return found[:limit],next_cursor


def search(root, **payload):
    with r.database.transaction(root,isolation_level='REPEATABLE READ') as c:
        r.execute(c,"SET LOCAL statement_timeout='20s'")
        items,cursor=search_rows(c,**payload)
    for item in items:
        start=item['start_offset'];end=item['end_offset'];lo=max(0,start-90);hi=min(len(item['body']),end+140)
        item['snippet']=item['body'][lo:hi]
        item['snippet_start']=lo
        item['quote']=item['body'][start:end]
        item.pop('normalized',None);item.pop('full_body',None);item.pop('body',None)
    return {'items':items,'next_cursor':cursor,'rule':'原文重叠出现位置逐一列出；人工分段交界处已去重；按字形归一的检索不改变原文'}


class ImportRequest(BaseModel):
    source_path: str = Field(min_length=1,max_length=1000)
    slug: str = Field(default='daizhige',pattern=r'^[a-z0-9-]{1,80}$')
    name: str = Field(default='殆知阁核心古籍库',min_length=1,max_length=100)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1,max_length=160)
    mode: str = Field(default='literal',pattern='^(normal|exact|literal)$')
    categories: list[str] = Field(default_factory=list,max_length=11)
    edition_id: UUID | None = None
    corpus_id: UUID | None = None
    limit: int = Field(default=20,ge=1,le=100)
    cursor: str | None = Field(default=None,max_length=3000)


def register(router,root):
    @router.get('/core/status')
    def get_status():return {'items':status(root()),'categories':CATEGORIES}

    @router.post('/core/import')
    def enqueue(payload:ImportRequest):
        inventory(payload.source_path) # Validate before queuing a durable job.
        with r.database.transaction(root()) as c:
            if r.execute(c,"SELECT id FROM kb_jobs WHERE kind='core_import' AND state IN ('queued','running') AND workspace_key=current_setting('shixue.workspace')").first():
                raise HTTPException(409,'已有核心古籍导入任务，请在处理任务中查看')
            return r.queue(c,'core_import',payload.model_dump())

    @router.get('/core/books')
    def list_books(query:str='',categories:str='',cursor:str='',limit:int=Query(30,ge=1,le=100),corpus_id:UUID|None=None):
        return books(root(),query,[v for v in categories.split(',') if v],cursor,limit,corpus_id)

    @router.post('/core/search')
    def find(payload:SearchRequest):return search(root(),**payload.model_dump())

    @router.get('/core/editions/{identifier}')
    def manifest(identifier:UUID):
        with r.database.transaction(root()) as c:
            item=r.edition(c,identifier)
            item['core']=r.one(c,'SELECT cb.*,cp.name corpus_name,cp.source_revision FROM kb_core_books cb JOIN kb_corpora cp ON cp.id=cb.corpus_id WHERE cb.edition_id=:e',e=identifier)
            item['files']=r.rows(c,'SELECT f.source_path,f.asset_id,f.title,f.metadata,a.sha256,p.pages '
                                'FROM kb_core_files f JOIN kb_assets a ON a.id=f.asset_id LEFT JOIN '
                                '(SELECT asset_id,max(page_index) pages FROM kb_blocks WHERE revision_id=:rev GROUP BY asset_id) p ON p.asset_id=f.asset_id '
                                'WHERE f.revision_id=:rev ORDER BY f.source_path',rev=item['active_revision'])
            return item

    @router.get('/core/editions/{identifier}/read')
    def read(identifier:UUID,asset_id:UUID,page:int=Query(1,ge=1)):
        with r.database.transaction(root()) as c:
            item=r.edition(c,identifier)
            r.one(c,'SELECT edition_id FROM kb_core_books WHERE edition_id=:id',id=identifier)
            return r.rows(c,'SELECT b.* FROM kb_blocks b WHERE b.revision_id=:rev AND b.asset_id=:a AND b.page_index=:p ORDER BY ordinal',rev=item['active_revision'],a=asset_id,p=page)
