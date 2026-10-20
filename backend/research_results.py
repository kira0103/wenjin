"""Save a verified assistant run as a project artifact, without creating a literature record."""
import json
import secrets
from uuid import UUID
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field


class SaveResult(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    kind: Literal['research', 'collaboration']
    run_id: UUID


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS research_result_exports(
      project_id TEXT NOT NULL,kind TEXT NOT NULL,run_id TEXT NOT NULL,artifact_id TEXT NOT NULL,
      PRIMARY KEY(project_id,kind,run_id))''')


def register(app, connection, now, service):
    @app.post('/api/research-results')
    def save(body: SaveResult):
        try:
            from backend.knowledge import research_assistant, collaboration
        except ModuleNotFoundError:
            from knowledge import research_assistant, collaboration
        run_id = str(body.run_id)
        value = (research_assistant if body.kind == 'research' else collaboration).detail(service.root, run_id)
        if value['state'] != 'succeeded' or not value['answer'] or body.kind == 'research' and not value['verified']:
            raise HTTPException(409, '只可保存已完成且通过引用核验的研究结果')
        sources = value.get('evidence') or []
        timestamp, task_id, artifact_id = now(), secrets.token_hex(12), secrets.token_hex(12)
        title = value.get('question') or '文献研究结果'
        citations = '\n\n## 原文依据\n' + '\n\n'.join(
            f"[{s.get('evidence_id','')}] {s.get('locator',{}).get('title','')} · {s.get('locator',{}).get('edition_label','')} · 第 {s.get('locator',{}).get('page_index','')} 页／段\n\n> " + s.get('quote','').replace('\n','\n> ')
            for s in sources) if sources else ''
        content = value['answer'] + citations
        payload = {'content': content, 'research_sources': sources,
                   'research_run': {'kind': body.kind, 'id': run_id, 'outcome': value.get('outcome')},
                   'source_context': {'selection': '研究任务固定来源与引用快照'}}
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0', (body.project_id,)).fetchone():
                raise HTTPException(404, '请选择现有项目')
            existing = db.execute('SELECT artifact_id FROM research_result_exports WHERE project_id=? AND kind=? AND run_id=?',
                                  (body.project_id, body.kind, run_id)).fetchone()
            if existing:
                return {'artifact_id': existing[0], 'duplicate': True}
            encoded = json.dumps(payload, ensure_ascii=False)
            db.execute('''INSERT INTO tasks(id,project_id,task_type,title,instructions,status,input_json,result,created_at,updated_at)
                          VALUES(?,?,'research',?,'','completed',?,?,?,?)''',
                       (task_id, body.project_id, title, json.dumps(body.model_dump(mode='json')), encoded, timestamp, timestamp))
            db.execute('INSERT INTO artifacts(id,project_id,task_id,title,artifact_type,body,created_at) VALUES(?,?,?,?,?,?,?)',
                       (artifact_id, body.project_id, task_id, title[:300], 'research', encoded, timestamp))
            db.execute('INSERT INTO research_result_exports VALUES(?,?,?,?)', (body.project_id, body.kind, run_id, artifact_id))
        return {'artifact_id': artifact_id, 'duplicate': False}
