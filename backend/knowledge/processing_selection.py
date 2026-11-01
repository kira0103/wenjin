"""Shared administrator selection predicates for pages and whole-library batches."""
from .embedding_config import LEGACY_MODEL,space


def relation(embedding,query=''):
    legacy=embedding['provider']=='local' and embedding['model']==LEGACY_MODEL and not embedding['document_prefix'] and not embedding['query_prefix'] and not embedding['revision']
    match="(i.config->>'embedding_space'=:space OR (i.config->>'embedding_space' IS NULL AND :legacy))"
    vector="(i.state='ready' AND i.model_key IS NOT NULL AND i.revision_id=e.active_revision)"
    ready=f'({vector} AND {match})'
    state=f"CASE WHEN latest_job.state IN ('queued','running') THEN 'processing' WHEN latest_job.state='failed' THEN 'failed' WHEN {ready} THEN 'ready' WHEN {vector} THEN 'mismatch' ELSE 'pending' END"
    base="FROM kb_editions e JOIN kb_works w ON w.id=e.work_id LEFT JOIN kb_indexes i ON i.id=e.active_index LEFT JOIN (SELECT DISTINCT ON (payload->>'edition_id') CAST(payload->>'edition_id' AS uuid) edition_id,state FROM kb_jobs WHERE workspace_key=current_setting('shixue.workspace') AND kind IN ('import','index') ORDER BY payload->>'edition_id',CASE WHEN state IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC,id DESC) latest_job ON latest_job.edition_id=e.id WHERE NOT e.deleted AND e.owner_role IS NULL AND w.title ILIKE :q"
    return base,state,ready,{'q':'%'+query+'%','space':space(embedding),'legacy':legacy}
