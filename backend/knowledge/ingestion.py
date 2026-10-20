from __future__ import annotations

import io
import hashlib
import re
from pathlib import Path

import pymupdf as fitz
from PIL import Image
from fastapi import HTTPException

try:
    from backend.importers import parse_document
except ModuleNotFoundError:
    from importers import parse_document
from . import repository as r
from .models import recognize,embed
from .text import normalize,tokenize,IDENTITY
from .chunking import configured_spans,stored_config,IDENTITY as CHUNKER_IDENTITY


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


def parse(filename,data,check=lambda:None):
    suffix=Path(filename).suffix.lower()
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
                if signals['ocr_reasons']:
                    zoom=min(2,4000/max(page.rect.width,page.rect.height))
                    image=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False).tobytes('png')
                    try:
                        candidate,ocr_quality=recognize(image)
                        damaged='damaged_text_layer' in signals['ocr_reasons']
                        use=bool(candidate.strip()) and (not value.strip() or damaged or len(candidate.strip())>len(value.strip())+5)
                        if use:value,quality=candidate,{**ocr_quality,'page_signals':signals}
                        else:quality['ocr_candidate_used']=False
                    except Exception:
                        if not value.strip():raise
                        quality['ocr_warning']='OCR 未完成，暂保留提取文本，请人工核对'
                pages.append((number,value,quality))
            return pages,'page'
    if suffix in {'.png','.jpg','.jpeg','.tif','.tiff'}:
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


def publish(connection,edition_id,pages,source_kind='extracted',expected_revision=_ANY):
    current=r.edition(connection,edition_id)
    if expected_revision is not _ANY and str(current['active_revision'])!=str(expected_revision):
        raise HTTPException(409,'资料文本已更新，请重新读取后发布')
    revision,index=r.uid(),r.uid()
    previous_config=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
    config=stored_config(previous_config)
    r.execute(connection,'INSERT INTO kb_revisions(id,edition_id,source_kind,parent_id,config) '
              'VALUES(:id,:e,:kind,:parent,CAST(:config AS jsonb))',id=revision,e=edition_id,kind=source_kind,
              parent=current['active_revision'],config=r.j({'parser':'structure-v1','positions':'unicode-codepoints'}))
    r.execute(connection,'INSERT INTO kb_indexes(id,revision_id,config) VALUES(:id,:r,CAST(:c AS jsonb))',
              id=index,r=revision,c=r.j({**config,'tokenizer':IDENTITY,'chunker':CHUNKER_IDENTITY}))
    ordinal=0
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
            for start,end,part in configured_spans(paragraph,config):
                r.execute(connection,'INSERT INTO kb_chunks(id,index_id,block_id,start_offset,end_offset,body,normalized,tokens) '
                          "VALUES(:id,:idx,:b,:s,:e,:body,:normalized,to_tsvector('simple',:tokens))",id=r.uid(),idx=index,b=block,
                          s=start,e=end,body=part,normalized=normalize(part),tokens=tokenize(current['title']+' '+page.get('section_path','')+' '+part))
    r.execute(connection,'UPDATE kb_editions SET active_revision=:r,active_index=:i WHERE id=:e AND NOT deleted',r=revision,i=index,e=edition_id)
    return {'revision_id':revision,'index_id':index,'blocks':ordinal}


def job_gate(connection,payload,result=None):
    if not payload.get('_job_id'):return
    job=r.one(connection,'SELECT state,owner FROM kb_jobs WHERE id=:id FOR UPDATE',id=payload['_job_id'])
    if job['state']!='running' or job['owner']!=payload['_job_owner']:
        raise HTTPException(409,'任务已取消或被其他执行器接管，停止发布')
    if result is not None:
        r.execute(connection,"UPDATE kb_jobs SET state='succeeded',stage='complete',progress=100,result=CAST(:result AS jsonb),lease_until=NULL WHERE id=:id",id=payload['_job_id'],result=r.j(result))


def process_import(root,payload,check,progress):
    with r.database.transaction(root) as connection:
        edition=r.edition(connection,payload['edition_id'])
        files=r.rows(connection,'SELECT a.*,ea.filename FROM kb_assets a JOIN kb_edition_assets ea ON ea.asset_id=a.id '
                     'WHERE ea.edition_id=:id ORDER BY ea.ordinal,ea.asset_id',id=payload['edition_id'])
    pages=[]
    candidates=[]
    for index,file in enumerate(files):
        check();progress('parse',int(index/max(1,len(files))*75))
        if payload.get('asset_id') and str(file['id'])!=str(payload['asset_id']):
            with r.database.transaction(root) as connection:
                previous=r.rows(connection,'SELECT asset_id,page_index,body,kind,section_path,quality FROM kb_blocks '
                                'WHERE revision_id=:revision AND asset_id=:asset ORDER BY ordinal',
                                revision=edition['active_revision'],asset=file['id'])
            pages.extend({**item,'asset_id':str(item['asset_id'])} for item in previous)
            continue
        data=r.asset_path(root,file['storage_key']).read_bytes()
        parsed,position=parse(file['filename'],data,check)
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
        r.one(connection,'SELECT id FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=payload['edition_id'])
        job_gate(connection,payload)
        result=publish(connection,payload['edition_id'],pages,expected_revision=edition['active_revision'])
        for candidate in candidates:
            r.execute(connection,'INSERT INTO kb_reference_candidates(id,source_edition_id,source_asset_id,page_index,ordinal,raw_text,title,authors_raw,year_label) '
                      'VALUES(:id,:e,:a,:p,:o,:raw,:title,:authors,:year) ON CONFLICT(id) DO NOTHING',
                      id=candidate['id'],e=payload['edition_id'],a=candidate['asset'],p=candidate['page_number'],o=candidate['ordinal'],
                      raw=candidate['raw_text'],title=candidate['title'],authors=candidate['authors'],year=candidate['year'])
        r.queue(connection,'index',{'edition_id':payload['edition_id'],'revision_id':result['revision_id']})
        job_gate(connection,payload,result)
    return result


def process_index(root,payload,check,progress):
    with r.database.transaction(root) as connection:
        current=r.edition(connection,payload['edition_id'])
        revision=payload.get('revision_id') or str(current['active_revision'])
        if revision!=str(current['active_revision']):
            raise HTTPException(409,'旧修订已被替换，停止发布旧索引')
        blocks=r.rows(connection,'SELECT * FROM kb_blocks WHERE revision_id=:r ORDER BY ordinal',r=revision)
        previous_config=r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=current['active_index']).scalar()
    config=stored_config(payload.get('config') or previous_config)
    items=[(block,start,end,part) for block in blocks for start,end,part in configured_spans(block['body'],config)]
    if not items:return {'empty':True}
    vectors=[];model_key=''
    for start in range(0,len(items),32):
        check();progress('embedding',int(start/len(items)*90))
        batch=[current['title']+' '+item[0]['section_path']+' '+item[3] for item in items[start:start+32]]
        values,model_key=embed(root,batch);vectors.extend(values)
    check()
    with r.database.transaction(root) as connection:
        active=r.one(connection,'SELECT active_revision FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=payload['edition_id'])
        if str(active['active_revision'])!=revision:raise HTTPException(409,'文本已更改，索引未发布')
        job_gate(connection,payload)
        identifier=r.uid()
        r.execute(connection,'INSERT INTO kb_indexes(id,revision_id,model_key,state,config) VALUES(:i,:r,:key,\'ready\',CAST(:c AS jsonb))',
                  i=identifier,r=revision,key=model_key,c=r.j({**config,'tokenizer':IDENTITY,'chunker':CHUNKER_IDENTITY,'dimensions':384}))
        values=[]
        for (block,start,end,part),vector in zip(items,vectors):
            values.append(dict(id=r.uid(),idx=identifier,b=block['id'],s=start,e=end,body=part,normalized=normalize(part),
                               tokens=tokenize(current['title']+' '+block['section_path']+' '+part),v=r.j(vector)))
        from sqlalchemy import text
        connection.execute(text('INSERT INTO kb_chunks(id,index_id,block_id,start_offset,end_offset,body,normalized,tokens,embedding) '
                           "VALUES(:id,:idx,:b,:s,:e,:body,:normalized,to_tsvector('simple',:tokens),CAST(:v AS vector))"),values)
        r.execute(connection,'UPDATE kb_editions SET active_index=:i WHERE id=:e',i=identifier,e=payload['edition_id'])
        job_gate(connection,payload,{'index_id':identifier,'model_key':model_key,'chunks':len(items)})
    return {'index_id':identifier,'model_key':model_key,'chunks':len(items)}
