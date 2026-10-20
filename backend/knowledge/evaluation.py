"""Human-labeled source spans, durable runs, and honest retrieval metrics."""
import math
from time import perf_counter

from fastapi import HTTPException

from . import repository as r
from .retrieval import scope_sql,search
from .ingestion import job_gate
from . import retrieval_settings


def suite(connection,identifier):
    return r.one(connection,"SELECT * FROM kb_eval_suites WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)


def add_case(root,suite_id,payload,resolved_search):
    with r.database.transaction(root) as connection:
        # Serialize case limits, including concurrent requests.
        r.one(connection,"SELECT id FROM kb_eval_suites WHERE id=:id AND workspace_key=current_setting('shixue.workspace') FOR UPDATE",id=suite_id)
        if r.execute(connection,'SELECT count(*) FROM kb_eval_cases WHERE suite_id=:id',id=suite_id).scalar_one()>=100:
            raise HTTPException(400,'一个评测集最多保存 100 个问题')
        values={}
        where=scope_sql(resolved_search.get('scope',{}),values)
        if not resolved_search.get('include_commentary',True):where+=" AND b.kind='body'"
        expected=[]
        for span in payload['expected']:
            block=r.rows(connection,'SELECT b.id FROM kb_blocks b JOIN kb_revisions rev ON rev.id=b.revision_id '
                         'JOIN kb_editions e ON e.id=rev.edition_id WHERE '+where+' AND b.id=:block',block=span['block_id'],**values)
            if not block:raise HTTPException(400,'标注原文已更新、已删除或不在所选检索范围内，请重新标注')
            anchor=r.anchor(connection,span['block_id'],span['start'],span['end'])
            expected.append({**span,'anchor_id':str(anchor['id']),'quote_hash':anchor['quote_hash'],
                             'edition_id':str(anchor['edition_id']),'revision_id':anchor['locator']['revision_id'],
                             'asset_id':anchor['locator']['asset_id'],'page_index':anchor['locator']['page_index']})
        identifier=r.uid()
        r.execute(connection,'INSERT INTO kb_eval_cases(id,suite_id,search,expected,expect_empty) '
                  'VALUES(:id,:suite,CAST(:s AS jsonb),CAST(:e AS jsonb),:empty)',id=identifier,suite=suite_id,
                  s=r.j(resolved_search),e=r.j(expected),empty=payload['expect_empty'])
        return r.one(connection,'SELECT * FROM kb_eval_cases WHERE id=:id',id=identifier)


def queue_run(root,identifier):
    with r.database.transaction(root) as connection:
        suite(connection,identifier)
        cases=r.rows(connection,'SELECT id,search,expected,expect_empty FROM kb_eval_cases WHERE suite_id=:id ORDER BY created_at,id',id=identifier)
        if not cases:raise HTTPException(400,'请先标注至少一个评测问题')
        settings=retrieval_settings.read(root,connection)
        frozen=[{**case,'id':str(case['id']),'search':{**case['search'],'retrieval':settings['options']}} for case in cases]
        run_id=r.uid()
        job=r.queue(connection,'evaluation',{'run_id':run_id})
        r.execute(connection,'INSERT INTO kb_eval_runs(id,suite_id,job_id,case_snapshot) VALUES(:id,:s,:j,CAST(:c AS jsonb))',
                  id=run_id,s=identifier,j=job['id'],c=r.j(frozen))
        return {'id':run_id,'job_id':str(job['id'])}


def in_scope(item,scope):
    if scope.get('_empty'):return False
    if scope.get('edition_ids') and str(item['edition_id']) not in scope['edition_ids']:return False
    page=item['page_index']
    if scope.get('page_from') and page<scope['page_from']:return False
    if scope.get('page_to') and page>scope['page_to']:return False
    if '_project_sources' not in scope:return True
    for source in scope['_project_sources']:
        if str(source['edition_id'])!=str(item['edition_id']):continue
        if source['mode']=='edition':return True
        if any(str(span['asset_id'])==str(item['asset_id']) and span['page_from']<=page<=span['page_to'] for span in source['ranges']):return True
    return False


def metrics(results,expected,expect_empty=False,k=10):
    results=results[:k]
    if expect_empty:return {'negative_pass':not results,'recall':None,'precision':None,'mrr':None,'ndcg':None}
    matched=set();assigned=set();gains=[];relevant_results=0;first=None
    for rank,item in enumerate(results,1):
        matches=[i for i,gold in enumerate(expected) if str(item['block_id'])==str(gold['block_id'])
                 and item['start_offset']<gold['end'] and item['end_offset']>gold['start']]
        if matches:
            relevant_results+=1
            if first is None:first=rank
        matched.update(matches)
        fresh=[i for i in matches if i not in assigned]
        if fresh:
            chosen=max(fresh,key=lambda i:expected[i]['relevance'])
            assigned.add(chosen);gains.append(2**expected[chosen]['relevance']-1)
        else:gains.append(0)
    # Ranking returns unique source blocks; multiple labeled spans in one block
    # must not create an impossible ideal with two ranks for that same block.
    block_grades={}
    for item in expected:
        key=str(item['block_id']);block_grades[key]=max(block_grades.get(key,0),item['relevance'])
    ideal=sorted((2**grade-1 for grade in block_grades.values()),reverse=True)[:k]
    dcg=lambda values:sum(value/math.log2(rank+2) for rank,value in enumerate(values))
    return {'negative_pass':None,'recall':len(matched)/len(expected),
            'precision':relevant_results/len(results) if results else 0,
            'mrr':1/first if first else 0,'ndcg':dcg(gains)/dcg(ideal) if ideal else 0}


def process_evaluation(root,payload,check,progress):
    with r.database.transaction(root) as connection:
        run=r.one(connection,'SELECT er.* FROM kb_eval_runs er JOIN kb_eval_suites s ON s.id=er.suite_id '
                  "WHERE er.id=:id AND s.workspace_key=current_setting('shixue.workspace')",id=payload['run_id'])
    details=[]
    for index,case in enumerate(run['case_snapshot']):
        check();progress('evaluate',int(index/len(run['case_snapshot'])*95))
        begin=perf_counter()
        found=search(root,{**case['search'],'limit':10})
        with r.database.transaction(root) as connection:
            record=r.one(connection,'SELECT snapshot FROM kb_retrieval_runs WHERE id=:id',id=found['retrieval_run_id'])
        published={item['edition_id']:item['revision_id'] for item in record['snapshot']}
        stale=[gold['anchor_id'] for gold in case['expected'] if published.get(gold['edition_id'])!=gold['revision_id']]
        leaks=sum(not in_scope(item,case['search']['scope']) for item in found['results'])
        scores=None if stale else metrics(found['results'],case['expected'],case['expect_empty'])
        details.append({'case_id':case['id'],'query':case['search']['query'],'status':'stale' if stale else 'completed',
                        'stale_anchors':stale,'metrics':scores,'scope_leaks':leaks,'retrieval_run_id':found['retrieval_run_id'],
                        'retrieval':found['retrieval'],'warnings':found['warnings'],'elapsed_ms':round((perf_counter()-begin)*1000,2)})
    completed=[item for item in details if item['metrics'] is not None]
    aggregate={}
    for name in ('recall','precision','mrr','ndcg','negative_pass'):
        values=[item['metrics'][name] for item in completed if item['metrics'][name] is not None]
        aggregate[name]=sum(values)/len(values) if values else None
    report={'k':10,'case_count':len(details),'evaluated':len(completed),'stale':len(details)-len(completed),
            'retrieval':run['case_snapshot'][0]['search'].get('retrieval'),
            'scope_leaks':sum(item['scope_leaks'] for item in details),'metrics':aggregate,'cases':details,
            'limitations':['仅评测检索排序；论断支持、直接引文与拒答质量需要另外审核。',
                           '范围使用标注时保存的选择；原文修订变化的标注不计入平均分。',
                           '不相关材料未穷尽标注时，precision 仅反映当前标注集。']}
    check()
    with r.database.transaction(root) as connection:
        job_gate(connection,payload)
        r.execute(connection,'UPDATE kb_eval_runs SET report=CAST(:r AS jsonb) WHERE id=:id',id=payload['run_id'],r=r.j(report))
    return {'evaluation_run_id':payload['run_id'],'evaluated':len(completed),'stale':report['stale']}
