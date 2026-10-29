"""Confirmed deletion of project work and batch records; library assets remain."""
import hashlib,json,secrets,sqlite3
from pathlib import Path
from contextlib import closing
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from langgraph.checkpoint.sqlite import SqliteSaver

class Confirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmation: str = Field(min_length=1,max_length=100)
    preview_token: str = Field(min_length=64,max_length=64)

def token(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def batch_preview(db,identifier):
    row=db.execute('SELECT * FROM collation_batches WHERE id=?',(identifier,)).fetchone()
    if not row:raise HTTPException(404,'找不到执行批次')
    counts={key:db.execute(query,(identifier,)).fetchone()[0] for key,query in {
        'files':'SELECT count(*) FROM collation_items WHERE batch_id=?',
        'candidates':"SELECT count(*) FROM collation_items WHERE batch_id=? AND merge_status='candidate'",
        'attempts':'SELECT count(*) FROM collation_attempts WHERE batch_id=?',
        'events':'SELECT count(*) FROM collation_events WHERE batch_id=?',
    }.items()}
    busy=row['status'] in ('queued','running','pausing','stopping') or bool(row['owner']) or db.execute("SELECT 1 FROM collation_attempts WHERE batch_id=? AND status='requesting'",(identifier,)).fetchone() is not None
    value={'id':identifier,'name':row['title'],'created_at':row['created_at'],'status':row['status'],'counts':counts,'can_delete':not busy}
    value['preview_token']=token(value)
    return value

def purge_batch(db,identifier):
    # Reused output is already an independent copy. Keep that provenance visible.
    db.execute("UPDATE collation_chunks SET reused_from='deleted:'||reused_from WHERE reused_from IN (SELECT c.id FROM collation_chunks c JOIN collation_items i ON i.id=c.item_id WHERE i.batch_id=?)",(identifier,))
    db.execute('DELETE FROM collation_events WHERE batch_id=?',(identifier,))
    db.execute('DELETE FROM collation_attempts WHERE batch_id=?',(identifier,))
    db.execute('DELETE FROM collation_chunks WHERE item_id IN (SELECT id FROM collation_items WHERE batch_id=?)',(identifier,))
    db.execute('DELETE FROM collation_items WHERE batch_id=?',(identifier,))
    db.execute('DELETE FROM collation_batches WHERE id=?',(identifier,))

def project_preview(db,identifier):
    row=db.execute('SELECT * FROM projects WHERE id=? AND deleted=0',(identifier,)).fetchone()
    if not row:raise HTTPException(404,'找不到研究项目')
    tables={'materials':'project_materials','units':'research_units','tasks':'tasks','artifacts':'artifacts','batches':'collation_batches','runs':'preprocess_runs','debug_runs':'collation_debug_runs'}
    counts={key:db.execute(f'SELECT count(*) FROM {table} WHERE project_id=?',(identifier,)).fetchone()[0] for key,table in tables.items()}
    try:
        from backend.knowledge import compat
    except ModuleNotFoundError:
        from knowledge import compat
    if compat.activated(db):
        try:counts['materials']=len(compat.catalog_materials(db,identifier))
        except Exception:counts['materials']=0
    busy=any(db.execute(f"SELECT 1 FROM {table} WHERE project_id=? AND status IN ({states}) LIMIT 1",(identifier,)).fetchone() for table,states in {
        'collation_batches':"'queued','running','pausing','stopping'",'tasks':"'queued','running'",'preprocess_jobs':"'queued','running'",'collation_debug_runs':"'queued','running'"}.items())
    value={'id':identifier,'name':row['name'],'counts':counts,'can_delete':not busy,
           'revision_sum':db.execute('SELECT coalesce(sum(revision),0) FROM research_units WHERE project_id=?',(identifier,)).fetchone()[0]}
    value['preview_token']=token(value)
    return value

def validate(preview,payload,expected):
    if payload.confirmation.strip()!=expected:raise HTTPException(400,'删除确认内容不匹配')
    if preview['preview_token']!=payload.preview_token:raise HTTPException(409,'数据已变化，请重新查看删除范围并确认')
    if not preview['can_delete']:raise HTTPException(409,'仍有执行中的任务，请先停止批次或等待模型请求结束后删除')

def backup(root,label):
    folder=Path(root)/'backups';folder.mkdir(exist_ok=True)
    destination=folder/(label+'-'+secrets.token_hex(6)+'.sqlite')
    with closing(sqlite3.connect((Path(root)/'library.sqlite').as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(destination)) as target:source.backup(target)
    return str(destination)

def register(app,connection,now,service):
    @app.get('/api/projects/{project_id}/delete-preview')
    def preview_project(project_id:str):
        with connection() as db:return project_preview(db,project_id)
    @app.delete('/api/projects/{project_id}')
    def delete_project(project_id:str,payload:Confirmation):
        root=Path(service.root)
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            preview=project_preview(db,project_id);validate(preview,payload,preview['name'])
            artifact_ids=[r[0] for r in db.execute('SELECT id FROM artifacts WHERE project_id=?',(project_id,))]
            files=[(root/'artifacts'/(identifier+'.json')).resolve() for identifier in artifact_ids]
            if any(path.parent!=(root/'artifacts').resolve() for path in files):raise HTTPException(400,'成果文件路径不合法，已取消删除')
            task_ids=[r[0] for r in db.execute('SELECT id FROM tasks WHERE project_id=?',(project_id,))]
            saved=backup(root,'before-delete-project')
            for row in db.execute('SELECT id FROM collation_batches WHERE project_id=?',(project_id,)).fetchall():purge_batch(db,row['id'])
            db.execute('DELETE FROM unit_versions WHERE unit_id IN (SELECT id FROM research_units WHERE project_id=?)',(project_id,))
            for table in ('research_units','tasks','artifacts','preprocess_runs','preprocess_jobs','collation_debug_runs','collation_configs','project_materials','project_bibliography'):
                try:
                    from backend.knowledge.execution import activated
                except ModuleNotFoundError:
                    from knowledge.execution import activated
                if activated(db) and table in {'project_materials','project_bibliography'}:continue
                db.execute(f'DELETE FROM {table} WHERE project_id=?',(project_id,))
            # A hidden ownership marker keeps global material foreign keys valid.
            db.execute("UPDATE projects SET deleted=1,archived=1,description='',rules='' WHERE id=?",(project_id,))
            next_project=db.execute('SELECT id FROM projects WHERE deleted=0 AND archived=0 ORDER BY created_at LIMIT 1').fetchone()
            if not next_project:
                next_id=secrets.token_hex(12)
                db.execute('INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)',(next_id,'我的研究','',now()))
            else:next_id=next_project[0]
        cleanup_errors=[]
        for path in files:
            try:path.unlink(missing_ok=True)
            except OSError:cleanup_errors.append('部分成果副本未能从磁盘清理')
        checkpoints=root/'checkpoints/runs.sqlite'
        if checkpoints.exists() and task_ids:
            try:
                with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
                    for identifier in task_ids:saver.delete_thread(identifier)
            except sqlite3.Error:cleanup_errors.append('部分执行检查点未能清理')
        return {'deleted':True,'next_project_id':next_id,'library_retained':True,'backup':saved,'cleanup_errors':list(dict.fromkeys(cleanup_errors))}
    @app.get('/api/collation/batches/{batch_id}/delete-preview')
    def preview_batch(batch_id:str):
        with connection() as db:return batch_preview(db,batch_id)
    @app.delete('/api/collation/batches/{batch_id}')
    def delete_batch(batch_id:str,payload:Confirmation):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            preview=batch_preview(db,batch_id);validate(preview,payload,batch_id)
            saved=backup(service.root,'before-delete-batch')
            purge_batch(db,batch_id)
        return {'deleted':True,'drafts_retained':True,'materials_retained':True,'backup':saved}
