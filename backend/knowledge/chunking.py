"""Source-preserving chunk profiles; all offsets are Unicode codepoints."""
import re

from .schemas import ChunkConfig
from .text import chunks

IDENTITY = 'source-spans-v2'


def stored_config(value):
    value=value or {}
    return ChunkConfig(profile=value.get('profile','legacy'),size=value.get('size',value.get('chunk_characters',100)),
                       overlap=value.get('overlap',15)).model_dump()


def configured_spans(value, config=None):
    config=ChunkConfig.model_validate(config or {}).model_dump()
    size,overlap=config['size'],config['overlap']
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


def preview(value,config):
    items=[{'start':start,'end':end,'body':body} for start,end,body in configured_spans(value,config)]
    return {'chunks':items,'config':{**config,'chunker':IDENTITY,'positions':'unicode-codepoints'},
            'warnings':['分块预览仅调整检索单元，不修改原文。字符数不等于模型 token 数；复杂表格、竖排及版面顺序仍需人工核对。']}
