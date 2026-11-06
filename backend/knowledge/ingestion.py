from __future__ import annotations

import io
import hashlib
import os
import re
from pathlib import Path
from time import monotonic

import pymupdf as fitz
from PIL import Image
from fastapi import HTTPException

try:
    from backend.importers import parse_document
except ModuleNotFoundError:
    from importers import parse_document
from . import repository as r
from .models import recognize,embed
from .text import normalize,tokenize,tokenize_normalized,IDENTITY
from .chunking import configured_spans,index_spans,stored_config,IDENTITY as CHUNKER_IDENTITY
from .embedding_config import EmbeddingOptions,space as embedding_space

# Background publication has a separate budget from interactive 5-second reads.
# Incremental HNSW maintenance on a large desktop library can pause a batch well
# past the former 120-second cap, so these remain generous and are only applied
# inside the publication transaction (ordinary queries still use 5 s / 3 s).
INDEX_WRITE_STATEMENT_SECONDS=int(os.environ.get('SHIXUE_INDEX_WRITE_STATEMENT_SECONDS','600'))
INDEX_WRITE_LOCK_SECONDS=int(os.environ.get('SHIXUE_INDEX_WRITE_LOCK_SECONDS','60'))
INDEX_WRITE_BUDGET_SECONDS=int(os.environ.get('SHIXUE_INDEX_WRITE_BUDGET_SECONDS','3600'))
INDEX_WRITE_BATCH_SIZE=128


def page_signals(page,value):
    """Heuristic routing signals, not an assertion that extraction is correct."""
    visible=len(re.sub(r'\s','',value))
    damaged=value.count('\ufffd')+sum(ord(c)<32 and c not in '\n\r\t' for c in value)
    area=max(1,page.rect.width*page.rect.height)
    image_area=sum(max(0,item['bbox'][2]-item['bbox'][0])*max(0,item['bbox'][3]-item['bbox'][1])
                   for item in page.get_image_info())
    coverage=min(1,image_area/area)
    reasons=[]
    if not visible:reasons.append('empty_text')
    if damaged>=2 and damaged/max(1,visible)>.03:reasons.append('damaged_text_layer')
    if coverage>.65 and visible<80:reasons.append('image_page_with_sparse_text')
    blocks=[item for item in page.get_text('blocks') if len(item)>6 and item[6]==0 and len(item[4].strip())>40]
    columns=any(a[2]<page.rect.width*.6 and b[0]>page.rect.width*.4 and a[2]<=b[0]
                and min(a[3],b[3])-max(a[1],b[1])>20 for a in blocks for b in blocks)
    return {'visible_characters':visible,'damaged_characters':damaged,'image_coverage':round(coverage,3),
            'possible_multicolumn':columns,'ocr_reasons':reasons,'routing_version':'pdf-page-signals-v1'}


def parse(filename,data,check=lambda:None,ocr_mode='auto'):
    suffix=Path(filename).suffix.lower()
    if suffix in {'.html','.htm','.csv','.pptx','.xlsx'}:
        from .file_loaders import load
        from backend.importers import segments
        text=load(suffix,data)
        return [(n,body,{'source':'extracted','format':suffix[1:],'review_required':True}) for n,body in segments(text)],'section'
    if suffix=='.md':
        from .core_corpus import parse_markdown,PRIMARY
        _,_,body,_=parse_markdown(data,Path(filename).stem)
        return [(i+1,body[start:start+PRIMARY],{'source':'markdown','review_required':True}) for i,start in enumerate(range(0,len(body),PRIMARY))],'section'
    if suffix=='.pdf':
        with fitz.open(stream=data,filetype='pdf') as pdf:
            if pdf.needs_pass or pdf.page_count>5000:
                raise HTTPException(400,'PDF 加密或超过 5000 页')
            pages=[]
            for number,page in enumerate(pdf,1):
                check()
                value=page.get_text('text',sort=True)
                signals=page_signals(page,value)
                quality={'source':'extracted','review_required':True,'page_signals':signals}
                if ocr_mode=='always' or ocr_mode=='auto' and signals['ocr_reasons']:
                    zoom=min(2,4000/max(page.rect.width,page.rect.height))
                    image=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False).tobytes('png')
                    try:
                        candidate,ocr_quality=recognize(image)
                        damaged='damaged_text_layer' in signals['ocr_reasons']
                        use=bool(candidate.strip()) and (ocr_mode=='always' or not value.strip() or damaged or len(candidate.strip())>len(value.strip())+5)
                        if use:value,quality=candidate,{**ocr_quality,'page_signals':signals}
                        else:quality['ocr_candidate_used']=False
                    except Exception:
                        if not value.strip():raise
                        quality['ocr_warning']='OCR 未完成，暂保留提取文本，请人工核对'
                if ocr_mode=='never' and signals['ocr_reasons']:quality['ocr_warning']='本次禁用 OCR，请检查文本层是否完整'
                quality['ocr_mode']=ocr_mode
                pages.append((number,value,quality))
            return pages,'page'
    if suffix in {'.png','.jpg','.jpeg','.tif','.tiff'}:
        if ocr_mode=='never':raise HTTPException(400,'图片原件需要 OCR；当前入库配置禁用了 OCR')
        pages=[]
        with Image.open(io.BytesIO(data)) as image:
            for index in range(getattr(image,'n_frames',1)):
                check();image.seek(index)
                frame=image.convert('RGB');frame.thumbnail((4000,4000))
                buffer=io.BytesIO();frame.save(buffer,format='PNG')
                value,quality=recognize(buffer.getvalue())
                pages.append((index+1,value,quality))
        return pages,'page'
    result=parse_document(filename,data)
    return [(number,value,{'source':'extracted','review_required':True}) for number,value in result['pages']],result['position_kind']


_ANY=object()


def publish(connection,edition_id,pages,source_kind='extracted',expected_revision=_ANY,chunk_config=None):
    current=r.edition(connection,edition_id)
    if expected_revision is not _ANY and str(current['active_revision'])!=str(expected_revision):
        raise HTTPException(409,'资料文本已更新，请重新读取后发布')
    revision,index=r.uid(),r.uid()
    previous_config=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
    if chunk_config is None and previous_config is None:
        from .retrieval_settings import read
        chunk_config=read(None,connection)['options']['ingestion']['chunking']
    config=stored_config(chunk_config or previous_config)
    r.execute(connection,'INSERT INTO kb_revisions(id,edition_id,source_kind,parent_id,config) '
              'VALUES(:id,:e,:kind,:parent,CAST(:config AS jsonb))',id=revision,e=edition_id,kind=source_kind,
              parent=current['active_revision'],config=r.j({'parser':'structure-v1','positions':'unicode-codepoints'}))
    r.execute(connection,'INSERT INTO kb_indexes(id,revision_id,config) VALUES(:id,:r,CAST(:c AS jsonb))',
              id=index,r=revision,c=r.j({**config,'tokenizer':IDENTITY,'chunker':CHUNKER_IDENTITY}))
    ordinal=0;source_blocks=[]
    for page in pages:
        asset=page['asset_id'];number=page['page_index'];value=page['body']
        r.execute(connection,'INSERT INTO kb_pages(asset_id,page_index) VALUES(:a,:p) ON CONFLICT DO NOTHING',a=asset,p=number)
        # Paragraph blocks preserve the exact source string and its boundaries.
        for paragraph in value.split('\n\n'):
            if not paragraph.strip():continue
            ordinal+=1;block=r.uid()
            r.execute(connection,'INSERT INTO kb_blocks(id,revision_id,asset_id,page_index,ordinal,kind,section_path,body,quality) '
                      'VALUES(:id,:r,:a,:p,:o,:kind,:section,:body,CAST(:q AS jsonb))',id=block,r=revision,a=asset,p=number,o=ordinal,
                      kind=page.get('kind','body'),section=page.get('section_path',''),body=paragraph,q=r.j(page.get('quality',{})))
            source_blocks.append({'id':block,'asset_id':asset,'page_index':number,'ordinal':ordinal,'body':paragraph,
                                  'kind':page.get('kind','body'),'section_path':page.get('section_path',''),'quality':page.get('quality',{})})
    from .adaptive_chunking import plan_blocks
    plan=plan_blocks(source_blocks,config,metadata=current)
    for item in plan['items']:
        r.execute(connection,'INSERT INTO kb_chunks(id,index_id,block_id,start_offset,end_offset,body,normalized,tokens,parent_spans) '
                  "VALUES(:id,:idx,:b,:s,:e,:body,:normalized,to_tsvector('simple',:tokens),CAST(:parents AS jsonb))",id=r.uid(),idx=index,b=item['block']['id'],
                  s=item['start'],e=item['end'],body=item['body'],normalized=normalize(item['index_text']),
                  tokens=tokenize(current['title']+' '+item['block'].get('section_path','')+' '+item['index_text']),parents=r.j(item['parent_spans']))
    r.execute(connection,'UPDATE kb_indexes SET config=CAST(:config AS jsonb) WHERE id=:id',id=index,
              config=r.j({**config,'tokenizer':IDENTITY,'chunker':CHUNKER_IDENTITY,'analysis':plan['analysis'],'resolved':plan['resolved']}))
    r.execute(connection,'UPDATE kb_editions SET active_revision=:r,active_index=:i WHERE id=:e AND NOT deleted',r=revision,i=index,e=edition_id)
    return {'revision_id':revision,'index_id':index,'blocks':ordinal}


def job_gate(connection,payload,result=None):
    if not payload.get('_job_id'):return
    job=r.one(connection,'SELECT state,owner FROM kb_jobs WHERE id=:id FOR UPDATE',id=payload['_job_id'])
    if job['state']!='running' or job['owner']!=payload['_job_owner']:
        raise HTTPException(409,'任务已取消或被其他执行器接管，停止发布')
    if result is not None:
        r.execute(connection,"UPDATE kb_jobs SET state='succeeded',stage='complete',progress=100,result=CAST(:result AS jsonb)||CASE WHEN result ? 'diagnostic_history' THEN jsonb_build_object('diagnostic_history',result->'diagnostic_history') ELSE '{}'::jsonb END,lease_until=NULL,updated_at=now() WHERE id=:id",id=payload['_job_id'],result=r.j(result))


def process_import(root,payload,check,progress):
    with r.database.transaction(root) as connection:
        edition=r.edition(connection,payload['edition_id'])
        files=r.rows(connection,'SELECT a.*,ea.filename FROM kb_assets a JOIN kb_edition_assets ea ON ea.asset_id=a.id '
                     'WHERE ea.edition_id=:id ORDER BY ea.ordinal,ea.asset_id',id=payload['edition_id'])
        if 'expected_revision' in payload and str(edition['active_revision'])!=str(payload['expected_revision']):raise HTTPException(409,'录文在任务排队后已更新，请重新提交处理')
        from .retrieval_settings import read
        processing=payload.get('processing') or read(root,connection)['options']['ingestion']
    if not files:raise HTTPException(409,'此文献没有可处理原件')
    pages=[]
    candidates=[]
    for index,file in enumerate(files):
        check();progress('parse',int(index/max(1,len(files))*75),{'completed':index,'total':len(files),'unit':'files','filename':file['filename']})
        if payload.get('asset_id') and str(file['id'])!=str(payload['asset_id']):
            with r.database.transaction(root) as connection:
                previous=r.rows(connection,'SELECT asset_id,page_index,body,kind,section_path,quality FROM kb_blocks '
                                'WHERE revision_id=:revision AND asset_id=:asset ORDER BY ordinal',
                                revision=edition['active_revision'],asset=file['id'])
            pages.extend({**item,'asset_id':str(item['asset_id'])} for item in previous)
            continue
        data=r.asset_path(root,file['storage_key']).read_bytes()
        parsed,position=parse(file['filename'],data,check,processing['ocr_mode'])
        try:
            from backend.reference_extract import extract_references
        except ModuleNotFoundError:
            from reference_extract import extract_references
        for ordinal,candidate in enumerate(extract_references([(number,body) for number,body,_ in parsed]),1):
            fingerprint=hashlib.sha256((str(edition['id'])+':'+str(file['id'])+':'+str(ordinal)+':'+candidate['raw_text']).encode()).hexdigest()
            candidates.append({**candidate,'id':fingerprint,'asset':str(file['id']),'ordinal':ordinal})
        for number,value,quality in parsed:
            pages.append({'asset_id':str(file['id']),'page_index':number,'body':value,'quality':quality})
        with r.database.transaction(root) as connection:
            r.execute(connection,'UPDATE kb_edition_assets SET position_kind=:kind WHERE edition_id=:e AND asset_id=:a',
                      kind=position,e=payload['edition_id'],a=file['id'])
    check();progress('publish',80)
    with r.database.transaction(root) as connection:
        # 大篇幅录文的写入可能远超普通请求的 5 秒语句时限（例如整本类书 Markdown）；
        # 发布是单个原子事务，失败会让整份文献停在“未发布”状态，这里放宽语句时限。
        r.execute(connection, "SET LOCAL statement_timeout='10min'")
        r.one(connection,'SELECT id FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=payload['edition_id'])
        job_gate(connection,payload)
        result=publish(connection,payload['edition_id'],pages,expected_revision=edition['active_revision'],chunk_config=processing['chunking'])
        for candidate in candidates:
            r.execute(connection,'INSERT INTO kb_reference_candidates(id,source_edition_id,source_asset_id,page_index,ordinal,raw_text,title,authors_raw,year_label) '
                      'VALUES(:id,:e,:a,:p,:o,:raw,:title,:authors,:year) ON CONFLICT(id) DO NOTHING',
                      id=candidate['id'],e=payload['edition_id'],a=candidate['asset'],p=candidate['page_number'],o=candidate['ordinal'],
                      raw=candidate['raw_text'],title=candidate['title'],authors=candidate['authors'],year=candidate['year'])
        batch=payload.get('processing_batch')
        if not batch and payload.get('_job_id'):
            batch=r.execute(connection,"SELECT COALESCE(payload->>'processing_batch','legacy:'||created_at::text) FROM kb_jobs WHERE id=:id",id=payload['_job_id']).scalar()
        child=r.queue(connection,'index',{'edition_id':payload['edition_id'],'revision_id':result['revision_id'],'config':processing['chunking'],
                                   'processing_batch':batch or r.uid(),'processing_parent_job':payload.get('_job_id'),
                                   'embedding':payload.get('embedding') or EmbeddingOptions().model_dump(mode='json'),
                                   **{k:payload[k] for k in ('policy_version','policy_source','document_policy_version') if k in payload}})
        if payload.get('_job_id'):
            r.execute(connection,"UPDATE kb_jobs SET result=jsonb_build_object('diagnostic_history',parent.result->'diagnostic_history') FROM kb_jobs parent WHERE kb_jobs.id=:child AND parent.id=:parent AND parent.result ? 'diagnostic_history'",child=child['id'],parent=payload['_job_id'])
        job_gate(connection,payload,result)
    return result


def process_index(root,payload,check,progress):
    from .embedding_config import EmbeddingOptions
    from .models import embedding_context
    with embedding_context(payload.get('embedding') or EmbeddingOptions().model_dump(mode='json')):
        return _process_index(root,payload,check,progress)


def _process_index(root,payload,check,progress):
    check();progress('reading_source',1)
    with r.database.transaction(root) as connection:
        current=r.edition(connection,payload['edition_id'])
        revision=payload.get('revision_id') or str(current['active_revision'])
        if revision!=str(current['active_revision']):
            raise HTTPException(409,'旧修订已被替换，停止发布旧索引')
        blocks=r.rows(connection,'SELECT * FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=revision)
        previous_config=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
        filenames={str(row['asset_id']):row['filename'] for row in r.rows(connection,'SELECT asset_id,filename FROM kb_edition_assets WHERE edition_id=:id',id=payload['edition_id'])}
    config=stored_config(payload.get('config') or previous_config)
    from .models import configuration
    embedding=configuration(root)[0]
    from .embedding_batcher import embedding_batcher
    from .adaptive_chunking import plan_blocks
    done=0;semantic_count=0
    def chunk_progress(completed,total):
        nonlocal done
        done=completed
        progress('chunking',5+int(20*completed/max(1,total)),{'completed':completed,'total':total,'unit':'blocks'})
    def semantic_embed(texts):
        nonlocal semantic_count
        percent=5+int(20*done/max(1,len(blocks)))
        progress('semantic_chunking',percent,{'completed':semantic_count,'unit':'segments'})
        result=embed(root,texts)[0];semantic_count+=len(result)
        progress('semantic_chunking',percent,{'completed':semantic_count,'unit':'segments'})
        return result
    plan=plan_blocks(blocks,config,embedder=semantic_embed,metadata=current,check=check,progress=chunk_progress)
    items=plan['items']
    if not items:raise HTTPException(400,'清洗和切分后没有可索引文本，原有索引保持不变')
    from collections import Counter
    file_totals=Counter(str(item['block']['asset_id']) for item in items);file_completed=Counter()
    def vector_detail(completed):
        return {'completed':completed,'total':len(items),'unit':'chunks','files':[{'filename':filenames.get(asset,'原件'),'completed':file_completed[asset],'total':total} for asset,total in list(file_totals.items())[:100]]}
    # Row materialisation (normalize, jieba tokens, JSON) is pure CPU work. ONNX
    # inference releases the GIL, so one background thread hides it behind the
    # GPU batches instead of paying for it after the last batch.
    title=current['title'];headings={}
    def heading(section):
        value=headings.get(section)
        if value is None:
            value=normalize(title+' '+section) if section else normalize(title)
            headings[section]=value
        return value
    def materialize(batch_items,vectors):
        rows=[]
        for item,vector in zip(batch_items,vectors):
            # Normalising once and reusing the heading avoids a second OpenCC pass
            # over the same characters for every chunk of the same section.
            normalized=normalize(item['index_text'])
            rows.append({'id':r.uid(),'b':item['block']['id'],'s':item['start'],'e':item['end'],'body':item['body'],
                         'normalized':normalized,'tokens':tokenize_normalized(heading(item['block'].get('section_path',''))+' '+normalized),
                         'v':r.j(vector),'parents':r.j(item['parent_spans'])})
        return rows
    # One model call per configured batch; the queue below keeps up to
    # SHIXUE_EMBED_PREFETCH batches in flight per document so the shared GPU
    # batcher always has work while another document is writing to PostgreSQL.
    step=max(32,int(embedding.get('batch_size') or 16))
    prefetch=max(1,min(4,int(os.environ.get('SHIXUE_EMBED_PREFETCH','2'))))
    identifier=r.uid();model_key='';materialized=[];last_check=0.0
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='kb-chunk-materialize') as pool:
        pending=[]
        inflight=deque()
        completed=0
        def collect_oldest():
            nonlocal completed,model_key
            batch_items,future=inflight.popleft()
            values,key=future.result()
            if len(values)!=len(batch_items):raise RuntimeError('嵌入模型返回数量与分块不匹配')
            if any(len(vector)!=embedding['dimensions'] for vector in values):raise RuntimeError('模型返回维度与入库任务的固定配置不一致')
            if not model_key:model_key=key
            pending.append(pool.submit(materialize,batch_items,values))
            for item in batch_items:file_completed[str(item['block']['asset_id'])]+=1
            completed+=len(batch_items)
            progress('embedding',30+int(60*completed/len(items)),vector_detail(completed))
        for start in range(0,len(items),step):
            now=monotonic()
            if now-last_check>=1.0:
                last_check=now;check()
            progress('embedding',30+int(60*start/len(items)),vector_detail(start))
            batch_items=items[start:start+step]
            batch=[title+' '+item['block'].get('section_path','')+' '+item['index_text'] for item in batch_items]
            # Coalesce this batch with concurrent documents' batches so small
            # documents do not waste GPU capacity with half-empty model calls.
            inflight.append((batch_items,embedding_batcher.submit(root,embedding,batch,embed)))
            while len(inflight)>prefetch:collect_oldest()
        while inflight:collect_oldest()
        for future in pending:materialized.extend(future.result())
    block_assets={str(item['block']['id']):str(item['block']['asset_id']) for item in items}
    written_files=Counter()
    def writing_progress(completed):
        progress('writing_index',95+int(4*completed/max(1,len(materialized))),
                 {'completed':completed,'total':len(materialized),'embedded':len(items),'unit':'chunks',
                  'statement_timeout_seconds':INDEX_WRITE_STATEMENT_SECONDS,
                  'files':[{'filename':filenames.get(asset,'原件'),'completed':written_files[asset],'total':total} for asset,total in list(file_totals.items())[:100]]})
    check();writing_progress(0)
    write_deadline=monotonic()+INDEX_WRITE_BUDGET_SECONDS
    with r.database.transaction(root) as connection:
        def write_budget():
            remaining=write_deadline-monotonic()
            if remaining<=0:raise TimeoutError('向量索引写入超过后台总预算')
            milliseconds=max(1,int(min(INDEX_WRITE_STATEMENT_SECONDS,remaining)*1000))
            r.execute(connection,"SELECT set_config('statement_timeout',:timeout,true)",timeout=str(milliseconds)+'ms')
        write_budget()
        r.execute(connection,"SELECT set_config('lock_timeout',:timeout,true)",timeout=str(INDEX_WRITE_LOCK_SECONDS)+'s')
        active=r.one(connection,'SELECT active_revision FROM kb_editions WHERE id=:id AND NOT deleted',id=payload['edition_id'])
        if str(active['active_revision'])!=revision:raise HTTPException(409,'文本已更改，索引未发布')
        # All new rows stay uncommitted. Do not lock the edition or task during
        # bulk insertion: heartbeat, cancellation and readers must remain free.
        r.execute(connection,'INSERT INTO kb_indexes(id,revision_id,model_key,state,config) VALUES(:i,:r,:key,\'ready\',CAST(:c AS jsonb))',
                  i=identifier,r=revision,key=model_key,c=r.j({**config,'tokenizer':IDENTITY,'chunker':CHUNKER_IDENTITY,'dimensions':embedding['dimensions'],'embedding':embedding,
                                                           'embedding_space':embedding_space(embedding),
                                                           'analysis':plan['analysis'],'resolved':plan['resolved'],
                                                           'policy':{k:payload[k] for k in ('policy_version','policy_source','document_policy_version') if k in payload}}))
        for row in materialized:row['idx']=identifier
        from sqlalchemy import text
        insert=text('INSERT INTO kb_chunks(id,index_id,block_id,start_offset,end_offset,body,normalized,tokens,embedding,parent_spans) '
                    "VALUES(:id,:idx,:b,:s,:e,:body,:normalized,to_tsvector('simple',:tokens),CAST(:v AS vector),CAST(:parents AS jsonb))")
        for start in range(0,len(materialized),INDEX_WRITE_BATCH_SIZE):
            check();write_budget()
            batch_rows=materialized[start:start+INDEX_WRITE_BATCH_SIZE]
            connection.execute(insert,batch_rows)
            for row in batch_rows:written_files[block_assets[str(row['b'])]]+=1
            writing_progress(start+len(batch_rows))
        check();write_budget()
        active=r.one(connection,'SELECT active_revision FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=payload['edition_id'])
        if str(active['active_revision'])!=revision:raise HTTPException(409,'文本已更改，索引未发布')
        job_gate(connection,payload)
        r.execute(connection,'UPDATE kb_editions SET active_index=:i WHERE id=:e',i=identifier,e=payload['edition_id'])
        job_gate(connection,payload,{'index_id':identifier,'model_key':model_key,'chunks':len(items),'analysis':plan['analysis'],'progress':vector_detail(len(items))})
    return {'index_id':identifier,'model_key':model_key,'chunks':len(items),'analysis':plan['analysis'],'progress':vector_detail(len(items))}
