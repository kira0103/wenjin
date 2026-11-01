"""Per-document strategy selection; every derived span maps to untouched source."""
import math
import re
from statistics import median

from .schemas import ChunkConfig
from .cleaning import clean
from .chunking import split_text

VERSION='document-routing-spans-v2'
HEADINGS=re.compile(r'(?m)^(?:#{1,6}[ \t]+[^\n]{1,150}|第[〇零一二三四五六七八九十百千万\d]+[卷篇章節节][^\n]{0,100}|卷[〇零一二三四五六七八九十百千万\d]+[^\n]{0,80}|\d+(?:\.\d+){0,4}[.、 \t]+[^\n]{1,100}|摘要|参考文献|Abstract|References)[ \t]*(?:\n|$)')
SENTENCES=re.compile(r'.+?(?:[。！？!?；;](?:[”」』])?|(?<=[.])\s+|\n+|$)',re.S)


def inspect_document(blocks,config,metadata=None):
    config=ChunkConfig.model_validate(config).model_dump();metadata=metadata or {}
    # Spread bounded samples across the document, not just its cover page.
    step=max(1,len(blocks)//40)
    sample='\n\n'.join(b['body'][:5000] for b in blocks[::step][:40])
    headings=len(list(HEADINGS.finditer(sample)))
    paragraphs=[p for p in re.split(r'\n\s*\n',sample) if p.strip()]
    sentences=len(list(SENTENCES.finditer(sample)))
    tabular=sum(bool(re.search(r'\t|\|.+\|',line)) for line in sample.splitlines())
    ancient=bool(re.search(r'(?m)^(?:第[一二三四五六七八九十百千万\d]+[卷篇]|卷[一二三四五六七八九十百千万\d]+)',sample))
    damaged=any(b.get('quality',{}).get('ocr_warning') or b.get('quality',{}).get('page_signals',{}).get('possible_multicolumn') for b in blocks)
    features={'sample_characters':len(sample),'source_blocks':len(blocks),'headings':headings,'sentences':sentences,
              'median_paragraph_characters':int(median(map(len,paragraphs))) if paragraphs else 0,'table_lines':tabular,'layout_warning':damaged}
    reasons=[];warnings=[];selected=dict(config)
    if config['mode']=='auto':
        selected['profile']='ancient' if ancient else 'paper' if headings and metadata.get('item_type') in {'article','thesis'} else 'general'
        if damaged:
            selected.update(mode='recursive');reasons.append('检测到 OCR 或多栏质量提示，先采用保守句段切分')
            warnings.append('阅读顺序或 OCR 仍需核对；自动切分不能修复错误录文。')
        elif tabular>=3:
            selected.update(mode='structure');reasons.append('检测到表格行，优先按完整行组织分块并关联表头')
        elif headings:
            if len(sample)>config['size']*2 and config['parent_size']>config['size']:
                selected.update(mode='parent_child',parent_mode='structure',child_mode='recursive')
                reasons.append('检测到章节或卷篇标题，使用结构父块与句段子块')
            else:selected.update(mode='structure');reasons.append('检测到标题边界，优先按结构切分')
        elif config['auto_semantic'] and len(sample)>max(1500,config['size']*3) and sentences>=6:
            if len(sample)>config['parent_size'] and config['parent_size']>config['size']:
                selected.update(mode='parent_child',parent_mode='semantic',child_mode='recursive')
            else:selected.update(mode='semantic')
            reasons.append('长文缺少明确标题，按相邻句段的语义变化判断边界')
        elif len(sample)>config['size']*2 and config['parent_size']>config['size']:
            selected.update(mode='parent_child',parent_mode='recursive',child_mode='recursive');reasons.append('缺少明确标题，采用递归父子切分；自动语义计算未启用')
        else:selected.update(mode='recursive');reasons.append('短文或缺少可靠结构，采用段落优先的递归切分')
    else:reasons.append('使用此文档指定的切分策略')
    return selected,{'version':VERSION,'requested_mode':config['mode'],'selected_mode':selected['mode'],
                     'profile':selected['profile'],'parent_mode':selected['parent_mode'],'child_mode':selected['child_mode'],
                     'features':features,'reasons':reasons,'warnings':warnings,'semantic_vectors':0,'semantic_fallbacks':0,'fallback_reasons':[]}


def bounded_segments(text,size,overlap=0,boundaries=()):
    points=sorted(set(boundaries));start=0
    while start<len(text):
        target=min(len(text),start+size)
        eligible=[p for p in points if start+max(1,size//3)<=p<=target]
        end=max(eligible) if target<len(text) and eligible else target
        if text[start:end].strip():yield start,end
        if end==len(text):break
        start=max(start+1,end-overlap)


def semantic_segments(text,config,embedder,state,check):
    sentences=[]
    for match in SENTENCES.finditer(text):
        # Bound each embedding input too; unpunctuated text uses fixed windows.
        for start in range(match.start(),match.end(),240):
            end=min(match.end(),start+240)
            if text[start:end].strip():sentences.append((start,end))
    if len(sentences)<2:return list(bounded_segments(text,config['size'],config['overlap']))
    window=config['semantic_window'];inputs=[text[sentences[max(0,n-window+1)][0]:end] for n,(_start,end) in enumerate(sentences)]
    if embedder is None or state.get('semantic_model_unavailable') or state['semantic_vectors']+len(inputs)>config['semantic_budget']:
        reason='lexical_stage' if embedder is None else 'model_unavailable' if state.get('semantic_model_unavailable') else 'semantic_budget'
        if reason not in state['fallback_reasons']:state['fallback_reasons'].append(reason)
        state['semantic_fallbacks']+=1
        return [(s,e) for s,e,_body in split_text(text,{**config,'mode':'recursive','profile':'general'})]
    vectors=[]
    try:
        for start in range(0,len(inputs),32):
            check();batch=embedder(inputs[start:start+32])
            if len(batch)!=len(inputs[start:start+32]) or any(not v or any(not math.isfinite(float(x)) for x in v) or not any(v) for v in batch) or len({len(v) for v in batch})!=1 or vectors and len(batch[0])!=len(vectors[0]):raise ValueError('invalid embedding')
            vectors.extend(batch);state['semantic_vectors']+=len(batch)
    except Exception as error:
        # Cancellation must never be turned into a successful fallback job.
        check()
        from fastapi import HTTPException
        if isinstance(error,HTTPException) and error.status_code==409:raise
        state['semantic_model_unavailable']=True
        if 'model_unavailable' not in state['fallback_reasons']:state['fallback_reasons'].append('model_unavailable')
        state['semantic_fallbacks']+=1
        return [(s,e) for s,e,_body in split_text(text,{**config,'mode':'recursive','profile':'general'})]
    breaks=[0];last=0
    for n in range(1,len(sentences)):
        a,b=vectors[n-1],vectors[n]
        if len(a)!=len(b):raise ValueError('semantic vector dimensions mismatch')
        norm=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
        similarity=sum(x*y for x,y in zip(a,b))/norm if norm else 1.0
        point=sentences[n][0]
        if similarity<config['semantic_threshold'] and point-last>=min(config['semantic_min_characters'],config['size']):breaks.append(point);last=point
    breaks.append(len(text));result=[]
    # Topic boundaries have no overlap; oversized topics still obey the hard cap.
    for first,last in zip(breaks,breaks[1:]):
        result.extend((first+s,first+e) for s,e in bounded_segments(text[first:last],config['size'],config['overlap'],[s-first for s,_e in sentences if first<s<last]))
    return result


def spans(text,config,embedder,state,check):
    mode=config['mode']
    if mode=='semantic':return semantic_segments(text,config,embedder,state,check)
    if mode=='structure':
        bounds=sorted({0,len(text),*(m.start() for m in HEADINGS.finditer(text))});result=[]
        for first,last in zip(bounds,bounds[1:]):
            part=text[first:last]
            lines=list(re.finditer(r'(?m)^.*(?:\n|$)',part));tabular=[m for m in lines if re.search(r'\t|\|.+\|',m.group())]
            if len(tabular)>=3:
                # An oversized row is split, but each child keeps the table parent.
                result.extend((first+s,first+e) for s,e in bounded_segments(part,config['size'],config['overlap'],[m.end() for m in lines]))
            else:result.extend((first+s,first+e) for s,e,_b in split_text(part,{**config,'mode':'recursive','profile':'general'}))
        return result
    if mode=='fixed':return list(bounded_segments(text,config['size'],config['overlap']))
    return [(s,e) for s,e,_body in split_text(text,config)]


def units(blocks,merge):
    group=[];length=0;key=None
    for block in blocks:
        current=(str(block['asset_id']),block['page_index'],block.get('kind','body'),block.get('section_path',''))
        if group and (not merge or current!=key or length+len(block['body'])>48000 or len(group)>=255):yield group;group=[];length=0
        group.append(block);length+=len(block['body'])+2;key=current
    if group:yield group


def plan_blocks(blocks,config,embedder=None,metadata=None,check=lambda:None,progress=None):
    requested=ChunkConfig.model_validate(config).model_dump()
    resolved,analysis=inspect_document(blocks,requested,metadata)
    advanced=requested['mode'] in {'auto','structure','semantic','fixed'} or requested['mode']=='parent_child' and (requested['parent_mode']!='recursive' or requested['child_mode']!='recursive')
    items=[];logical=[];parent_count=0;processed=0
    if progress:progress(0,len(blocks))
    for group in units(blocks,advanced):
        check();raw='';ranges=[]
        for block in group:
            if raw:raw+='\n\n'
            first=len(raw);raw+=block['body'];ranges.append((first,len(raw),block))
        text,positions,_changes=clean(raw,resolved['cleaning'])
        if not text.strip():
            processed+=len(group)
            if progress:progress(processed,len(blocks))
            continue
        table_header=next((m for m in re.finditer(r'(?m)^.*(?:\t|\|).*(?:\n|$)',text)),None) if resolved['mode']=='structure' and analysis['features']['table_lines']>=3 else None
        if resolved['mode']=='parent_child':
            pc={**resolved,'mode':resolved['parent_mode'],'size':resolved['parent_size'],'overlap':0}
            parents=spans(text,pc,embedder,analysis,check)
        elif table_header:parents=spans(text,{**resolved,'size':resolved['parent_size'],'overlap':0},embedder,analysis,check)
        else:parents=[(0,len(text))]
        for pstart,pend in parents:
            check();parent_count+=1
            child={**resolved,'mode':resolved['child_mode']} if resolved['mode']=='parent_child' else resolved
            children=spans(text[pstart:pend],child,embedder,analysis,check)
            raw_parent_start,raw_parent_end=positions[pstart],positions[pend-1]+1
            parent_spans=[{'block_id':str(b['id']),'start':max(raw_parent_start,first)-first,'end':min(raw_parent_end,last)-first}
                          for first,last,b in ranges if max(raw_parent_start,first)<min(raw_parent_end,last)] if resolved['mode']=='parent_child' or resolved['mode']=='structure' and analysis['features']['table_lines']>=3 else []
            if table_header and table_header.end()<=pstart:
                header_start,header_end=positions[table_header.start()],positions[table_header.end()-1]+1
                parent_spans=[{'block_id':str(b['id']),'start':max(header_start,first)-first,'end':min(header_end,last)-first,'role':'header'}
                              for first,last,b in ranges if max(header_start,first)<min(header_end,last)]+parent_spans
            for start,end in children:
                start+=pstart;end+=pstart
                raw_start,raw_end=positions[start],positions[end-1]+1
                logical.append({'start':raw_start,'end':raw_end,'body':raw[raw_start:raw_end],'index_text':text[start:end],
                                'page_index':group[0]['page_index'],'asset_id':str(group[0]['asset_id']),'parent_characters':raw_parent_end-raw_parent_start if parent_spans else 0})
                # Source rows remain individually addressable, including legacy
                # revisions. Virtual groups never manufacture a cross-block quote.
                for first,last,block in ranges:
                    lo,hi=max(first,raw_start),min(last,raw_end)
                    if lo>=hi:continue
                    indexes=[n for n in range(start,end) if lo<=positions[n]<hi]
                    if not indexes:continue
                    indexed=''.join(text[n] for n in indexes)
                    if indexed.strip():items.append({'block':block,'start':lo-first,'end':hi-first,'body':block['body'][lo-first:hi-first],
                                                     'index_text':indexed,'parent_spans':parent_spans})
        processed+=len(group)
        if progress:progress(processed,len(blocks))
    analysis['checks']={'logical_chunks':len(logical),'source_chunks':len(items),'parent_groups':parent_count,
                        'maximum_chunk_characters':max((len(c['index_text']) for c in logical),default=0),
                        'small_chunks':sum(len(c['index_text'])<min(40,resolved['size']//4) for c in logical),
                        'source_positions_valid':all(c['body']==c['block']['body'][c['start']:c['end']] for c in items)}
    messages={'lexical_stage':'当前阶段先建立关键词索引；语义边界将在后台向量加工时计算。',
              'model_unavailable':'后台嵌入模型未就绪或推理失败，语义切分已回退为递归句段切分。',
              'semantic_budget':'达到本文档语义计算上限，剩余内容使用递归句段切分。'}
    analysis['warnings'].extend(messages[reason] for reason in analysis['fallback_reasons'])
    if not items:analysis['warnings'].append('清洗后没有可索引内容。')
    return {'items':items,'chunks':logical,'analysis':analysis,'resolved':resolved,'requested':requested}
