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
        budget=options['parent_characters']
        radius=options['neighbor_blocks'] if options['parent_enabled'] else 0
        lower,upper=section_bounds(item['full_body'],item['start_offset']) if options['parent_enabled'] else (0,len(item['full_body']))
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
