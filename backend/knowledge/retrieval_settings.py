from fastapi import HTTPException

from . import repository as r
from .schemas import RetrievalOptions


def read(root,connection=None):
    if connection is None:
        with r.database.transaction(root) as connection:return read(root,connection)
    rows=r.rows(connection,"SELECT version,options FROM kb_retrieval_settings WHERE workspace_key=current_setting('shixue.workspace')")
    if not rows:return {'version':0,'options':RetrievalOptions().model_dump()}
    return {'version':rows[0]['version'],'options':RetrievalOptions.model_validate(rows[0]['options']).model_dump()}


def save(root,payload):
    with r.database.transaction(root) as connection:
        # Serialize initial INSERT as well as later updates for this workspace.
        r.execute(connection,"SELECT pg_advisory_xact_lock(hashtextextended(current_setting('shixue.workspace') || '_retrieval',0))")
        current=read(root,connection)
        if current['version']!=payload['version']:raise HTTPException(409,'检索配置已修改，请重新读取后保存')
        r.execute(connection,"INSERT INTO kb_retrieval_settings(workspace_key,version,options) "
                  "VALUES(current_setting('shixue.workspace'),:v,CAST(:o AS jsonb)) ON CONFLICT(workspace_key) DO UPDATE "
                  "SET version=EXCLUDED.version,options=EXCLUDED.options,updated_at=now()",
                  v=current['version']+1,o=r.j(payload['options']))
        return read(root,connection)
