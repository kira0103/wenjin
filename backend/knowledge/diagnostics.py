"""Persist candidate provenance without duplicating authoritative source text."""
from time import perf_counter

from . import repository as r

PIPELINE = 'rrf-rerank-parent-spans-v3'


class Trace:
    def __init__(self,payload):
        self.id=r.uid()
        self.payload=payload
        self.started=perf_counter()
        self.snapshot=[]
        self.data={'pipeline':PIPELINE,'rrf_k':60,'stages':[],
                   'outcome':'running','context':{},'citation_support':'not_evaluated'}

    def stage(self,name,started,**details):
        self.data['stages'].append({'name':name,'elapsed_ms':round((perf_counter()-started)*1000,2),**details})

    def save(self,root,results,outcome='completed'):
        self.data.update(outcome=outcome,elapsed_ms=round((perf_counter()-self.started)*1000,2))
        fields=('id','block_id','edition_id','revision_id','index_id','asset_id','page_index',
                'start_offset','end_offset','score','rerank_score','rrf_rank','source_kind','rank','channels')
        compact=[{key:item[key] if key in {'score','rerank_score','rrf_rank','page_index','start_offset','end_offset','rank','channels'}
                  else str(item[key]) for key in fields if key in item} for item in results]
        with r.database.transaction(root) as connection:
            r.execute(connection,'INSERT INTO kb_retrieval_runs(id,workspace_key,query,mode,payload,snapshot,diagnostics,results) '
                      "VALUES(:id,current_setting('shixue.workspace'),:q,:m,CAST(:p AS jsonb),CAST(:s AS jsonb),CAST(:d AS jsonb),CAST(:r AS jsonb))",
                      id=self.id,q=self.payload['query'],m=self.payload.get('mode','hybrid'),p=r.j(self.payload),
                      s=r.j(self.snapshot),d=r.j(self.data),r=r.j(compact))


def fusion(channels):
    """A source block contributes once per channel, regardless of chunk count."""
    merged={}
    raw=0
    for name,items in channels:
        seen=set()
        for rank,item in enumerate(items,1):
            raw+=1
            key=str(item['block_id'])
            if key in seen:continue
            seen.add(key)
            block_rank=len(seen)
            if key not in merged:merged[key]={**item,'score':0,'source_kind':'document','channels':[]}
            merged[key]['score']+=1/(60+block_rank)
            merged[key]['channels'].append({'name':name,'rank':block_rank,'chunk_rank':rank})
    results=sorted(merged.values(),key=lambda item:(-item['score'],str(item['block_id'])))
    for rank,item in enumerate(results,1):item['rank']=rank
    return results,raw-len(results)


def update(root,found,**details):
    identifier=found.get('retrieval_run_id')
    if not identifier:return
    found['diagnostics'].update(details)
    with r.database.transaction(root) as connection:
        r.execute(connection,"UPDATE kb_retrieval_runs SET diagnostics=diagnostics || CAST(:d AS jsonb) "
                  "WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier,d=r.j(details))
