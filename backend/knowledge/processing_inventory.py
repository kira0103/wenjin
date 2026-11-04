"""One filter definition for the processing table and cross-page submissions."""
from typing import Literal
from pydantic import Field
from .schemas import Strict
from . import repository as r

class InventoryFilters(Strict):
    source:Literal['all','core','regular']='all'
    text_state:Literal['all','present','missing']='all'
    job_state:Literal['all','none','queued','running','succeeded','failed','cancelled']='all'
    item_type:Literal['all','ancient','article','thesis','book','archive','other']='all'
    chunk_mode:Literal['all','unrecorded','auto','fixed','recursive','structure','semantic','delimiter','parent_child']='all'
    model:str=Field(default='',max_length=200)
    dimensions:int|None=Field(default=None,ge=1,le=65536)

ACTIVE="latest_job.state IN ('queued','running','failed')"
CORE="EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)"
MODEL=f"CASE WHEN {ACTIVE} THEN latest_job.payload->'embedding'->>'model' ELSE i.config->'embedding'->>'model' END"
DIMENSIONS=f"CAST(NULLIF(CASE WHEN {ACTIVE} THEN latest_job.payload->'embedding'->>'dimensions' ELSE COALESCE(i.config->'embedding'->>'dimensions',i.config->>'dimensions') END,'') AS integer)"
MODE=f"CASE WHEN {ACTIVE} THEN COALESCE(latest_job.result->'analysis'->>'selected_mode',latest_job.payload->'config'->>'mode',latest_job.payload->'processing'->'chunking'->>'mode') ELSE COALESCE(i.config->'analysis'->>'selected_mode',i.config->'resolved'->>'mode',i.config->>'mode') END"
UPDATED="latest_job.updated_at"
ORDERS={'title':'w.title ASC,e.id','title_desc':'w.title DESC,e.id','updated_desc':'latest_job.updated_at DESC NULLS LAST,w.title,e.id','updated_asc':'latest_job.updated_at ASC NULLS LAST,w.title,e.id'}

def predicate(value):
    value=InventoryFilters.model_validate(value) if not isinstance(value,InventoryFilters) else value
    clauses=[];params={}
    if value.source!='all':clauses.append(('' if value.source=='core' else 'NOT ')+CORE)
    if value.text_state!='all':clauses.append('e.active_revision IS '+('NOT NULL' if value.text_state=='present' else 'NULL'))
    if value.job_state=='none':clauses.append('latest_job.id IS NULL')
    elif value.job_state!='all':clauses.append('latest_job.state=:inventory_job_state');params['inventory_job_state']=value.job_state
    if value.item_type!='all':clauses.append('w.item_type=:inventory_type');params['inventory_type']=value.item_type
    if value.chunk_mode!='all':
        clauses.append(f"COALESCE(({MODE}),'')=:inventory_mode");params['inventory_mode']='' if value.chunk_mode=='unrecorded' else value.chunk_mode
    if value.model:
        clauses.append(f"COALESCE(({MODEL}),'')=:inventory_model");params['inventory_model']='' if value.model=='__unrecorded__' else value.model
    if value.dimensions is not None:clauses.append(f'({DIMENSIONS})=:inventory_dimensions');params['inventory_dimensions']=value.dimensions
    return ''.join(' AND ('+v+')' for v in clauses),params

def listing(root,query,state,offset,limit,batch_id,filters,sort):
    from . import retrieval_settings
    from .processing_selection import relation
    # The table, batch totals and live queue must describe the same instant.
    # READ COMMITTED could show a completed job in one query and a running
    # job in the next while the worker advances during this request.
    with r.database.transaction(root,isolation_level='REPEATABLE READ') as db:
        policy=retrieval_settings.read(root,db);embedding=policy['options']['embedding']
        base,vector_state,_,parameters=relation(embedding,query)
        # Facets cover the complete title/state match, never only the visible page.
        state_where='' if state=='all' else f' AND ({vector_state})=:inventory_state'
        facets=r.rows(db,f'SELECT {MODEL} model,{DIMENSIONS} dimensions,{MODE} chunk_mode,count(*) total '+base+state_where+' GROUP BY 1,2,3',**parameters,inventory_state=state)
        extra,values=predicate(filters);parameters.update(values)
        summary={key:0 for key in ('pending','ready','mismatch','processing','failed')}
        summary.update({v['vector_state']:v['total'] for v in r.rows(db,f'SELECT {vector_state} vector_state,count(*) total '+base+extra+' GROUP BY 1',**parameters)})
        count=sum(summary.values()) if state=='all' else summary[state]
        items=r.rows(db,f"WITH selected AS MATERIALIZED (SELECT e.id,latest_job.id job_id,{vector_state} vector_state,{MODEL} embedding_model,{DIMENSIONS} embedding_dimensions,{MODE} chunk_mode,{UPDATED} processed_at,row_number() OVER(ORDER BY {ORDERS[sort]}) position "+base+extra+state_where+f' ORDER BY {ORDERS[sort]} LIMIT :limit OFFSET :offset), '+
            "queue_positions AS MATERIALIZED (SELECT id,row_number() OVER(ORDER BY CASE WHEN state='running' THEN 0 ELSE 1 END,created_at,id) position FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running')) "+
            f"SELECT selected.*,w.title,w.item_type,e.label,e.active_revision,e.processing_config,e.processing_version,i.state index_state,i.config,{CORE} core, "+
            "(SELECT count(*) FROM kb_edition_assets ea JOIN kb_assets a ON a.id=ea.asset_id WHERE ea.edition_id=e.id AND a.availability='present') files, "+
            "CASE WHEN i.model_key IS NULL THEN NULL WHEN j.state='succeeded' AND j.result->>'index_id'=i.id::text AND j.result ? 'chunks' THEN (j.result->>'chunks')::bigint ELSE (SELECT count(*) FROM kb_chunks c WHERE c.index_id=i.id) END chunks, "+
            "CASE WHEN j.id IS NULL THEN NULL ELSE jsonb_build_object('id',j.id,'kind',j.kind,'state',j.state,'stage',j.stage,'progress',j.progress,'error',j.error,'detail',j.result->'progress','chunks',j.result->'chunks', "+
            "'document_progress',CASE WHEN j.kind='import' THEN round(j.progress*.2) WHEN j.payload->>'processing_parent_job' IS NOT NULL THEN round(20+j.progress*.8) ELSE j.progress END, "+
            "'updated_at',j.updated_at,'lease_valid',j.lease_until>clock_timestamp(),'queue_position',CASE WHEN j.state='queued' THEN qp.position ELSE NULL END) END job "+
            "FROM selected JOIN kb_editions e ON e.id=selected.id JOIN kb_works w ON w.id=e.work_id LEFT JOIN kb_indexes i ON i.id=e.active_index LEFT JOIN kb_jobs j ON j.id=selected.job_id LEFT JOIN queue_positions qp ON qp.id=j.id ORDER BY selected.position",**parameters,inventory_state=state,offset=offset,limit=limit)
        from .processing_progress import snapshot
        progress=snapshot(db,embedding,batch_id)
    models={};dimensions=set();modes=set()
    for row in facets:
        model=row['model'] or '__unrecorded__';models[model]=models.get(model,0)+row['total']
        if row['dimensions']:dimensions.add(row['dimensions'])
        modes.add(row['chunk_mode'] or 'unrecorded')
    return {'items':items,'total':count,'offset':offset,'limit':limit,'summary':summary,'processing_progress':progress,'policy_version':policy['version'],
            'facets':{'models':[{'model':m,'total':n} for m,n in sorted(models.items())],'dimensions':sorted(dimensions),'chunk_modes':sorted(modes)},
            'embedding':{k:embedding[k] for k in ('model','dimensions','provider')}}
