"""Derived index text with a map back to untouched Unicode source positions."""
import re
from .schemas import CleaningOptions


def clean(value,options=None):
    options=CleaningOptions.model_validate(options or {}).model_dump()
    text=value;positions=list(range(len(value)));changes={}
    def replace(pattern,replacement,name):
        nonlocal text,positions
        pieces=[];mapped=[];last=0;count=0
        for match in re.finditer(pattern,text):
            pieces.append(text[last:match.start()]);mapped.extend(positions[last:match.start()])
            pieces.append(replacement)
            mapped.extend([positions[match.start()]]*len(replacement))
            last=match.end();count+=1
        pieces.append(text[last:]);mapped.extend(positions[last:])
        text=''.join(pieces);positions=mapped
        if count:changes[name]=count
    if options['remove_control_characters']:replace(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe]','','control_characters')
    if options['remove_urls']:replace(r'https?://[^\s<>\u3000\u3001\u3002\uff0c\uff1b]+','','urls')
    if options['remove_emails']:replace(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+','','emails')
    if options['deduplicate_lines']:
        parts=[];mapped=[];seen=set();offset=0;count=0
        for line in text.splitlines(keepends=True):
            key=line.strip()
            if key and key in seen:count+=1
            else:parts.append(line);mapped.extend(positions[offset:offset+len(line)])
            if key:seen.add(key)
            offset+=len(line)
        text=''.join(parts);positions=mapped
        if count:changes['duplicate_lines']=count
    if options['normalize_whitespace']:
        replace(r'[ \t\r\u00a0\u3000]{2,}',' ','extra_spaces')
        replace(r'\n{3,}','\n\n','extra_blank_lines')
    return text,positions,changes


def index_text(value,config):
    return clean(value,(config or {}).get('cleaning'))[0]
