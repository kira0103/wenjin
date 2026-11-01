"""Versioned document overrides; enqueue-time policy snapshots are immutable."""
from fastapi import HTTPException
from . import repository as r,retrieval_settings
from .schemas import IngestionOptions


def effective(db,identifier,fallback=None):
    document=r.one(db,'SELECT processing_config,processing_version FROM kb_editions WHERE id=:id AND NOT deleted',id=identifier)
    policy=retrieval_settings.read(None,db)
    value=document['processing_config'] or fallback or policy['options']['ingestion']
    return {'processing':IngestionOptions.model_validate(value).model_dump(),'document_version':document['processing_version'],
            'policy_version':policy['version'],'source':'document' if document['processing_config'] else 'batch' if fallback else 'default'}


def save(db,identifier,processing,version):
    row=r.one(db,'SELECT processing_version FROM kb_editions WHERE id=:id AND NOT deleted FOR UPDATE',id=identifier)
    if row['processing_version']!=version:raise HTTPException(409,'文档切分策略已更新，请重新读取后保存')
    value=IngestionOptions.model_validate(processing).model_dump() if processing is not None else None
    r.execute(db,'UPDATE kb_editions SET processing_config=CAST(:config AS jsonb),processing_version=processing_version+1 WHERE id=:id',
              id=identifier,config=r.j(value) if value is not None else None)
    return effective(db,identifier)
