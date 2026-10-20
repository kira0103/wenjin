"""Read-only Unicode comparison. Differences are evidence for human review."""
from difflib import SequenceMatcher
from itertools import groupby

PUNCTUATION=set('。，、；：？！“”‘’《》〈〉·—…,.!?;:\"\'（）()')

def compare_text(source, result):
    left,right=[],[]
    counts={'inserted_punctuation':0,'changed_groups':0,'changed_chars':0,'added_chars':0,'removed_chars':0}
    def append(spans,text,kind,peer='',new=False):
        if not text:return
        # Split mixed edits so punctuation does not masquerade as a changed word.
        groups=groupby(text,lambda char:'punctuation' if new and kind in ('added','changed') and char in PUNCTUATION else kind)
        for actual,chars in groups:
            part=''.join(chars)
            if spans and spans[-1]['kind']==actual and spans[-1].get('peer','')==peer:
                spans[-1]['text']+=part
            else:spans.append({'text':part,'kind':actual,'peer':peer[:300]})
    # Fast exact insertion alignment also preserves original punctuation.
    i=0
    for char in result:
        if i<len(source) and char==source[i]:i+=1
        elif char not in PUNCTUATION:break
    else:
        if i==len(source):
            i=0;buffer=[];previous=None
            append(left,source,'unchanged')
            for char in result:
                same=i<len(source) and source[i]==char
                if same:i+=1
                kind='unchanged' if same else 'punctuation'
                if previous is not None and previous!=kind:
                    append(right,''.join(buffer),previous);buffer=[]
                buffer.append(char);previous=kind
                if not same:counts['inserted_punctuation']+=1
            if buffer:append(right,''.join(buffer),previous)
            return {'source_spans':left,'result_spans':right,**counts}
    # SequenceMatcher keeps work bounded on very long repetitive manuscripts;
    # ambiguous regions may be grouped, but neither side drops any characters.
    matcher=SequenceMatcher(None,source,result,autojunk=len(source)+len(result)>8000)
    for tag,a,b,c,d in matcher.get_opcodes():
        old,new=source[a:b],result[c:d]
        if tag=='equal':
            append(left,old,'unchanged');append(right,new,'unchanged');continue
        old_letters=''.join(x for x in old if x not in PUNCTUATION)
        new_letters=''.join(x for x in new if x not in PUNCTUATION)
        if old_letters!=new_letters:
            counts['changed_groups']+=1
            if tag=='replace' and old_letters and new_letters:counts['changed_chars']+=max(len(old_letters),len(new_letters))
            elif tag=='replace' and old_letters:counts['removed_chars']+=len(old_letters)
            elif tag=='replace' and new_letters:counts['added_chars']+=len(new_letters)
            elif tag=='insert':counts['added_chars']+=len(new_letters)
            elif tag=='delete':counts['removed_chars']+=len(old_letters)
        replacement=tag=='replace' and bool(old_letters) and bool(new_letters)
        append(left,old,'changed' if replacement else 'removed',new)
        append(right,new,'changed' if replacement else 'added',old,new=True)
    counts['inserted_punctuation']=sum(len(s['text']) for s in right if s['kind']=='punctuation')
    return {'source_spans':left,'result_spans':right,**counts}
