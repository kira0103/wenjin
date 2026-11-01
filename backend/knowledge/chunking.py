"""Source-preserving chunk profiles; all offsets are Unicode codepoints."""
import re

from .schemas import ChunkConfig
from .text import chunks
from .cleaning import clean

IDENTITY = 'document-routing-source-spans-v4'


def stored_config(value):
    value=value or {}
    fields=ChunkConfig.model_fields
    selected={key:item for key,item in value.items() if key in fields}
    selected.setdefault('size',value.get('chunk_characters',100))
    return ChunkConfig.model_validate(selected).model_dump()


def configured_spans(value, config=None):
    for start,end,source,_index in index_spans(value,config):yield start,end,source


def index_spans(value,config=None):
    config=ChunkConfig.model_validate(config or {}).model_dump()
    if not any(config['cleaning'].values()):
        for start,end,part in split_text(value,config):yield start,end,part,part
        return
    text,positions,_=clean(value,config['cleaning'])
    for start,end,part in split_text(text,config):
        first,last=positions[start],positions[end-1]+1
        yield first,last,value[first:last],part


def split_text(value,config):
    if config['mode'] in {'auto','fixed','structure','semantic'} or config['mode']=='parent_child' and (config.get('parent_mode','recursive')!='recursive' or config.get('child_mode','recursive')!='recursive'):
        from .adaptive_chunking import plan_blocks
        plan=plan_blocks([{'id':'preview','asset_id':'preview','page_index':1,'body':value}],config)
        for item in plan['items']:yield item['start'],item['end'],item['body']
        return
    size,overlap=config['size'],config['overlap']
    if config['mode']=='parent_child':
        parent={**config,'mode':'recursive','size':config['parent_size'],'overlap':0}
        child={**config,'mode':'recursive'}
        for first,_last,body in split_text(value,parent):
            for start,end,part in split_text(body,child):yield first+start,first+end,part
        return
    if config['mode']=='delimiter':
        separator=config['separator'];boundaries={m.end() for m in re.finditer(re.escape(separator),value)}
        start=0
        while start<len(value):
            target=min(len(value),start+size)
            candidates=[point for point in boundaries if start<point<=target]
            end=max(candidates) if candidates and target<len(value) else target
            if value[start:end].strip():yield start,end,value[start:end]
            if end==len(value):break
            start=max(start+1,end-overlap)
        return
    if config['profile']=='legacy':
        yield from chunks(value,size,overlap)
        return
    # Prefer sentence/line ends; keep headings with subsequent text when possible.
    pattern=r'\n+|[。！？；](?:[”」』])?|(?<=[.!?])\s+'
    boundaries={match.end() for match in re.finditer(pattern,value)}
    if config['profile']=='ancient':
        headings=r'(?m)^(?:第[〇零一二三四五六七八九十百千万\d]+[卷篇章节]|卷[〇一二三四五六七八九十百\d]+)'
    elif config['profile']=='paper':
        headings=r'(?m)^(?:#{1,6}\s+|\d+(?:\.\d+)*[.、\s]|摘要|参考文献|Abstract\b|References\b)'
    else:
        headings=r'(?m)^#{1,6}\s+'
    boundaries.update(match.start() for match in re.finditer(headings,value))
    start=0
    while start<len(value):
        target=min(len(value),start+size)
        end=target
        if target<len(value):
            options=[point for point in boundaries if start+size//2<=point<=target]
            if options:end=max(options)
        if value[start:end].strip():yield start,end,value[start:end]
        if end==len(value):break
        start=max(start+1,end-overlap)


def preview(value,config,root=None):
    config=ChunkConfig.model_validate(config).model_dump()
    text,_positions,changes=clean(value,config['cleaning'])
    from .adaptive_chunking import plan_blocks
    from .models import embed_cached
    plan=plan_blocks([{'id':'preview','asset_id':'preview','page_index':1,'body':value}],config,embedder=(lambda texts:embed_cached(root,texts)) if root else None)
    items=plan['chunks']
    return {'chunks':items,'config':{**config,'chunker':IDENTITY,'positions':'unicode-codepoints'},
            'analysis':plan['analysis'],'resolved':plan['resolved'],
            'cleaned_text':text,'changes':changes,'source_characters':len(value),'index_characters':len(text),
            'warnings':plan['analysis']['warnings']+['分块预览仅调整检索单元，不修改原文。字符数不等于模型 token 数；复杂表格、竖排及版面顺序仍需人工核对。']}
