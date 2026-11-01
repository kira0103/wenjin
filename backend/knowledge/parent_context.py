"""Virtual parent contexts: source spans, never another authoritative text copy."""
import hashlib
import re

from . import repository as r

HEADING=re.compile(r'(?m)^(?:#{1,6}[ \t]+|第[〇零一二三四五六七八九十百千万\d]+[卷篇章節节]|'
                   r'卷[〇一二三四五六七八九十百\d]+(?:[ \t]|$)|(?:\d+\.){0,3}\d+[ \t]+|[一二三四五六七八九十]+、)[^\n]{0,100}(?:\n|$)')


def section_bounds(body,hit):
    headings=[match.start() for match in HEADING.finditer(body)]
    before=[point for point in headings if point<=hit]
    after=[point for point in headings if point>hit]
    return max(before) if before else 0,min(after) if after else len(body)


def window(body,start,end,budget):
    margin=max(0,budget-(end-start))//4
    desired=start-margin
    minimum=end-budget if end-start<=budget else 0
    start=max(0,min(max(minimum,desired),max(0,len(body)-budget)))
    finish=min(len(body),start+budget)
    return start,finish,body[start:finish]


def attach(connection,items,scope,options,scope_builder):
    plans=[]
    for item in items:
        if options['parent_enabled'] and item.get('parent_spans'):
            saved=stored_parent(connection,item,scope,options,scope_builder)
            if saved:
                item['parent_context']=saved
                plans.append({'block_id':str(item['block_id']),'parent_id':saved['id'],'boundaries':saved['boundaries'],
                              'spans':[{k:v for k,v in part.items() if k not in {'body','section_path','truncated'}} for part in saved['parts']]})
                continue
        budget=options['parent_characters']
        radius=options['neighbor_blocks'] if options['parent_enabled'] else 0
        lower,upper=section_bounds(item['full_body'],item['start_offset']) if options['parent_enabled'] else (0,len(item['full_body']))
        if options['parent_enabled'] and item.get('index_id'):
            from .chunking import stored_config,configured_spans
            config=stored_config(r.execute(connection,'SELECT config FROM kb_indexes WHERE id=:id',id=item['index_id']).scalar())
            if config['mode']=='parent_child':
                parent={**config,'mode':'recursive','size':config['parent_size'],'overlap':0}
                bounds=next(((s,e) for s,e,_ in configured_spans(item['full_body'],parent) if s<=item['start_offset']<e),None)
                if bounds:
                    lower,upper=max(lower,bounds[0]),min(upper,bounds[1])
                    radius=0
        start,end,body=window(item['full_body'][lower:upper],item['start_offset']-lower,min(item['end_offset'],upper)-lower,budget)
        start+=lower;end+=lower
        primary={'block_id':str(item['block_id']),'revision_id':str(item['revision_id']),'asset_id':str(item['asset_id']),
                 'page_index':item['page_index'],'kind':item['kind'],'section_path':item['section_path'],
                 'start':start,'end':end,'body':body,'role':'primary','truncated':len(body)<len(item['full_body'])}
        parts=[primary];boundaries=[]
        if radius:
            values={};allowed=scope_builder(scope,values)
            candidates=r.rows(connection,'SELECT b.id,b.ordinal,b.asset_id,b.page_index,b.kind,b.section_path,'
                              'CASE WHEN '+allowed+' THEN b.body ELSE NULL END body '
                              'FROM kb_blocks b JOIN kb_revisions rev ON rev.id=b.revision_id JOIN kb_editions e ON e.id=rev.edition_id '
                              'WHERE b.revision_id=:revision AND b.ordinal BETWEEN :lo AND :hi ORDER BY b.ordinal',
                              revision=item['revision_id'],lo=item['ordinal']-radius,hi=item['ordinal']+radius,**values)
            by_ordinal={block['ordinal']:block for block in candidates}
            for direction in (-1,1):
                remaining=(budget-len(body))//2
                if (direction<0 and (start>0 or HEADING.match(item['full_body']))) or (direction>0 and end<len(item['full_body'])):
                    boundaries.append({'direction':direction,'step':1,'reason':'section_boundary' if lower>0 or upper<len(item['full_body']) or HEADING.match(item['full_body']) else 'primary_window_budget'})
                    continue
                for step in range(1,radius+1):
                    block=by_ordinal.get(item['ordinal']+direction*step)
                    reason=None
                    if not block:reason='missing_neighbor'
                    elif str(block['asset_id'])!=str(item['asset_id']):reason='asset_boundary'
                    elif block['kind']!=item['kind']:reason='kind_boundary'
                    elif block['section_path']!=item['section_path']:reason='section_boundary'
                    elif abs(block['page_index']-item['page_index'])>1:reason='page_gap'
                    elif block['body'] is None:reason='scope_boundary'
                    if reason:boundaries.append({'direction':direction,'step':step,'reason':reason});break
                    if remaining<=0:boundaries.append({'direction':direction,'step':step,'reason':'parent_budget'});break
                    headings=[match.start() for match in HEADING.finditer(block['body'])]
                    if direction>0 and headings and headings[0]==0:
                        boundaries.append({'direction':direction,'step':step,'reason':'section_boundary'});break
                    available_start=headings[-1] if direction<0 and headings else 0
                    available_end=headings[0] if direction>0 and headings else len(block['body'])
                    size=min(remaining,available_end-available_start)
                    offset=available_end-size if direction<0 else available_start
                    parts.append({'block_id':str(block['id']),'revision_id':str(item['revision_id']),
                                  'asset_id':str(block['asset_id']),'page_index':block['page_index'],'kind':block['kind'],
                                  'section_path':block['section_path'],'start':offset,'end':offset+size,
                                  'body':block['body'][offset:offset+size],'role':'before' if direction<0 else 'after',
                                  'truncated':size<len(block['body'])})
                    remaining-=size
                    if headings:boundaries.append({'direction':direction,'step':step,'reason':'section_boundary'});break
                    if size<len(block['body']):boundaries.append({'direction':direction,'step':step,'reason':'parent_budget'});break
        identity=r.j([(part['block_id'],part['start'],part['end']) for part in parts])
        parent_id=hashlib.sha256(identity.encode()).hexdigest()
        ordered=sorted(parts,key=lambda part:(part['page_index'],next((b['ordinal'] for b in candidates if str(b['id'])==part['block_id']),item['ordinal']) if radius else item['ordinal'],part['start']))
        plan={'id':parent_id,'parts':ordered,'boundaries':boundaries,'characters':sum(len(part['body']) for part in parts)}
        item['parent_context']=plan
        plans.append({'block_id':str(item['block_id']),'parent_id':parent_id,'boundaries':boundaries,
                      'spans':[{key:part[key] for key in ('block_id','revision_id','asset_id','page_index','kind','start','end','role')} for part in ordered]})
    return plans


def stored_parent(connection,item,scope,options,scope_builder):
    """Recheck every persisted parent span against RLS and this query's scope."""
    spans=item['parent_spans']
    if not isinstance(spans,list) or not 0<len(spans)<=256:return None
    try:
        selected=[(str(__import__('uuid').UUID(s['block_id'])),int(s['start']),int(s['end']),s.get('role','context')) for s in spans]
    except (KeyError,ValueError,TypeError):return None
    values={};allowed=scope_builder(scope,values)
    blocks=r.rows(connection,'SELECT b.id,b.ordinal,b.asset_id,b.page_index,b.kind,b.section_path,b.body FROM kb_blocks b '
                  'JOIN kb_revisions rev ON rev.id=b.revision_id JOIN kb_editions e ON e.id=rev.edition_id '
                  'WHERE b.id=ANY(CAST(:parent_ids AS uuid[])) AND b.revision_id=:parent_revision AND b.asset_id=:parent_asset AND '+allowed+' ORDER BY b.ordinal',
                  parent_ids=list({s[0] for s in selected}),parent_revision=item['revision_id'],parent_asset=item['asset_id'],**values)
    primary=next((b for b in blocks if str(b['id'])==str(item['block_id'])),None)
    if not primary:return None
    primary_span=next((s for s in selected if s[0]==str(primary['id']) and 0<=s[1]<=item['start_offset']<item['end_offset']<=s[2]<=len(primary['body'])),None)
    if not primary_span:return None
    _id,lo,hi,_role=primary_span
    budget=options['parent_characters'];start,end,body=window(primary['body'][lo:hi],item['start_offset']-lo,item['end_offset']-lo,budget)
    start+=lo;end+=lo
    def part(block,start,end,role):
        return {'block_id':str(block['id']),'revision_id':str(item['revision_id']),'asset_id':str(block['asset_id']),
                'page_index':block['page_index'],'kind':block['kind'],'section_path':block['section_path'],'start':start,'end':end,
                'body':block['body'][start:end],'role':role,'truncated':start>0 or end<len(block['body'])}
    parts=[part(primary,start,end,'primary')];remaining=budget-len(body)
    by_id={str(b['id']):b for b in blocks}
    ordered=sorted(selected,key=lambda s:(s[3]!='header',abs(by_id[s[0]]['ordinal']-primary['ordinal']) if s[0] in by_id else 999999,s[1]))
    for selected_span in ordered:
        block=by_id.get(selected_span[0])
        if block is None or selected_span==primary_span or remaining<=0:continue
        if block['kind']!=primary['kind'] or block['section_path']!=primary['section_path'] or block['page_index']!=primary['page_index']:continue
        _id,lo,hi,_role=selected_span
        if not 0<=lo<hi<=len(block['body']):continue
        role='before' if block['ordinal']<primary['ordinal'] or block is primary and hi<=start else 'after';size=min(remaining,hi-lo)
        first=hi-size if role=='before' else lo
        parts.append(part(block,first,first+size,role));remaining-=size
    parts.sort(key=lambda p:(by_id[p['block_id']]['ordinal'],p['start']))
    return {'id':hashlib.sha256(r.j([(p['block_id'],p['start'],p['end']) for p in parts]).encode()).hexdigest(),
            'parts':parts,'boundaries':[{'reason':'stored_parent_scope_and_budget'}],
            'characters':sum(len(p['body']) for p in parts)}


def evidence(connection,items,budget,per_parent):
    """Keep each primary hit first, then expand with independently anchored context."""
    sources=[];seen=set();truncated=0;skipped=0
    # Reserve each group's budget for its primary and then for nearby context.
    for item in items:
        plan=item['parent_context']
        remaining=min(per_parent,budget)
        parts=sorted(plan['parts'],key=lambda part:({'primary':0,'before':1,'after':2}[part['role']]))
        for part in parts:
            if remaining<=0 or budget<=0:skipped+=1;continue
            length=min(len(part['body']),remaining,budget)
            if length<=0:continue
            if part['role']=='primary':
                start,end,_=window(item['full_body'][part['start']:part['end']],max(0,item['start_offset']-part['start']),
                                   min(item['end_offset'],part['end'])-part['start'],length)
                start+=part['start'];end+=part['start']
            elif part['role']=='before':start,end=part['end']-length,part['end']
            else:start,end=part['start'],part['start']+length
            key=(part['block_id'],start,end)
            if key in seen:continue
            seen.add(key)
            anchor=r.anchor(connection,part['block_id'],start,end)
            sources.append({'evidence_id':'E'+str(len(sources)+1),'anchor_id':str(anchor['id']),
                            'quote':anchor['quote'],'locator':{**anchor['locator'],'kind':part['kind']},
                            'context_role':part['role'],'parent_id':plan['id']})
            remaining-=len(anchor['quote']);budget-=len(anchor['quote'])
            if part.get('truncated') or length<len(part['body']):truncated+=1
    original_sizes={item['parent_context']['id']:len(item['full_body']) for item in items}
    return sources,{'truncated_parts':truncated,'skipped_by_character_budget':skipped,
                    'parent_count':len(items),'expanded_parts':sum(item['context_role']!='primary' for item in sources),
                    'truncated_primary_parts':sum(item['context_role']=='primary' and len(item['quote'])<original_sizes[item['parent_id']] for item in sources)}
