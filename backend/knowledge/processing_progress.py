"""Administrator progress across pages, filters, and parse/index child jobs."""
from . import repository as r

BATCH="COALESCE(j.payload->>'processing_batch','legacy:'||j.created_at::text)"
LATEST=f"""WITH controllers AS MATERIALIZED (
 SELECT c.id,c.state,c.created_at,c.updated_at,
   COALESCE((c.result->>'cursor')::int,0) cursor,
   c.payload->'embedding'->>'model' embedding_model,c.payload->'targets' targets
 FROM kb_jobs c WHERE c.workspace_key=current_setting('shixue.workspace') AND c.kind='rag_batch'
), source AS (
 SELECT j.id,j.kind,j.payload->'embedding'->>'model' embedding_model,
   j.payload->>'processing_parent_job' IS NOT NULL has_parent,
   j.state,j.progress,j.created_at,j.updated_at,e.id edition_id,{BATCH} batch_id,false placeholder
 FROM kb_jobs j JOIN kb_editions e ON e.id=CAST(j.payload->>'edition_id' AS uuid)
 WHERE j.workspace_key=current_setting('shixue.workspace') AND j.kind IN ('import','index') AND NOT e.deleted AND e.owner_role IS NULL
 UNION ALL
 SELECT c.id,'index',c.embedding_model,false,
   CASE WHEN t.ordinality<=c.cursor THEN 'skipped'
     WHEN c.state='cancelled' THEN 'cancelled' WHEN c.state='failed' THEN 'dispatch_failed'
     ELSE 'pending_dispatch' END,
   0,c.created_at,c.updated_at,e.id,c.id::text,true
 FROM controllers c CROSS JOIN LATERAL jsonb_array_elements(c.targets) WITH ORDINALITY t(item,ordinality)
 JOIN kb_editions e ON e.id=CAST(t.item->>'id' AS uuid)
 WHERE NOT e.deleted AND e.owner_role IS NULL
), latest AS (
 SELECT DISTINCT ON (batch_id,edition_id) *,min(created_at) OVER(PARTITION BY batch_id) batch_submitted_at FROM source
 ORDER BY batch_id,edition_id,created_at DESC,placeholder,CASE WHEN kind='index' THEN 0 ELSE 1 END,id DESC
) """
TOTALS="""count(*) total,count(*) FILTER(WHERE state='succeeded') succeeded,
 count(*) FILTER(WHERE state='failed') failed,count(*) FILTER(WHERE state='cancelled') cancelled,
 count(*) FILTER(WHERE state='queued') queued,count(*) FILTER(WHERE state='running') running,
 count(*) FILTER(WHERE state='skipped') skipped,count(*) FILTER(WHERE state='pending_dispatch') pending_dispatch,
 count(*) FILTER(WHERE state='dispatch_failed') dispatch_failed,
 COALESCE(round(avg(CASE WHEN state IN ('succeeded','failed','cancelled','skipped','dispatch_failed') THEN 100
   WHEN kind='import' THEN progress*.2 WHEN has_parent THEN 20+progress*.8 ELSE progress END),1),0)::float percent"""


def snapshot(db,embedding,batch_id=''):
    overall=r.one(db,LATEST+'SELECT '+TOTALS+" FROM (SELECT DISTINCT ON (edition_id) * FROM source ORDER BY edition_id,created_at DESC,placeholder,CASE WHEN kind='index' THEN 0 ELSE 1 END,id DESC) submitted")
    overall['finished']=sum(overall[key] for key in ('succeeded','failed','cancelled','skipped','dispatch_failed'))
    batches=r.rows(db,LATEST+"""SELECT batch_id,min(batch_submitted_at) submitted_at,max(updated_at) updated_at,
      """+TOTALS+""",
      array_agg(DISTINCT embedding_model) models
      FROM latest GROUP BY batch_id ORDER BY bool_or(state IN ('queued','running','pending_dispatch')) DESC,min(created_at) DESC LIMIT 10""")
    selected=next((b for b in batches if b['batch_id']==batch_id),None) if batch_id else None
    if not selected and batch_id:
        # An older submitted batch remains inspectable after newer submissions.
        selected=r.rows(db,LATEST+"""SELECT batch_id,min(batch_submitted_at) submitted_at,max(updated_at) updated_at,
          """+TOTALS+""",
          array_agg(DISTINCT embedding_model) models FROM latest WHERE batch_id=:batch GROUP BY batch_id""",batch=batch_id)
        selected=selected[0] if selected else None
    selected=selected or (batches[0] if batches else None)
    if selected:
        selected['finished']=sum(selected[key] for key in ('succeeded','failed','cancelled','skipped','dispatch_failed'))
        if not any(b['batch_id']==selected['batch_id'] for b in batches):batches.append(selected)
    active=r.rows(db,"""SELECT j.id,j.kind,j.state,j.stage,j.progress,j.updated_at,j.error,
      j.lease_until>clock_timestamp() lease_valid,w.title,j.result->'progress' detail
      FROM kb_jobs j LEFT JOIN kb_editions e ON e.id=CAST(j.payload->>'edition_id' AS uuid) AND e.owner_role IS NULL
      LEFT JOIN kb_works w ON w.id=e.work_id
      WHERE j.workspace_key=current_setting('shixue.workspace') AND j.state='running' AND j.kind IN ('import','index')
      ORDER BY j.created_at,j.id LIMIT 3""")
    queue=r.one(db,"SELECT count(*) FILTER(WHERE state='queued') queued,count(*) FILTER(WHERE state='running') running FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND state IN ('queued','running')")
    ingestion_queue=r.one(db,"SELECT count(*) FILTER(WHERE state='queued') queued,count(*) FILTER(WHERE state='running') running FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') AND state IN ('queued','running')")
    dispatches=r.rows(db,"SELECT id,state,progress,error,created_at,result FROM kb_jobs WHERE kind='rag_batch' AND workspace_key=current_setting('shixue.workspace') ORDER BY created_at DESC LIMIT 10")
    from .embedding_config import LEGACY_MODEL,space
    legacy=embedding['provider']=='local' and embedding['model']==LEGACY_MODEL and not embedding['document_prefix'] and not embedding['query_prefix'] and not embedding['revision']
    library=r.one(db,"""SELECT count(*) total,count(*) FILTER(WHERE i.state='ready' AND i.model_key IS NOT NULL AND i.revision_id=e.active_revision
      AND (i.config->>'embedding_space'=:space OR (i.config->>'embedding_space' IS NULL AND :legacy))) ready
      FROM kb_editions e LEFT JOIN kb_indexes i ON i.id=e.active_index WHERE NOT e.deleted AND e.owner_role IS NULL""",space=space(embedding),legacy=legacy)
    sampled_at=r.one(db,'SELECT transaction_timestamp() sampled_at')['sampled_at']
    return {'sampled_at':sampled_at,'overall':overall,'batch':selected,'batches':batches,'queue':queue,'ingestion_queue':ingestion_queue,'active_jobs':active,'library':library,'dispatches':dispatches}
