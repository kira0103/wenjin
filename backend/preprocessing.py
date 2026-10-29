"""Human-confirmed preprocessing versions and recoverable format-analysis jobs."""
from __future__ import annotations

import json
import io
import re
import secrets
import sqlite3
import threading
import zipfile
from contextlib import closing
from typing import TypedDict

from fastapi import HTTPException
from fastapi.responses import Response
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, ConfigDict, Field

try:
    from backend import research
    from backend.preprocess_engine import Recipe, BUILTINS, ENGINE_VERSION, analyze, digest, formatted, prepared, samples, signature, split_texts
except ModuleNotFoundError:
    import research
    from preprocess_engine import Recipe, BUILTINS, ENGINE_VERSION, analyze, digest, formatted, prepared, samples, signature, split_texts


def unused_superseded_runs(db, project_id='', material_id=''):
    # Row order records successfully saved executions even if the system clock
    # changes. Keep snapshots referenced by work or AI artifacts independently.
    protected = {r[0] for r in db.execute('''
      SELECT json_extract(preprocess_json,'$.run_id') FROM research_units WHERE json_valid(preprocess_json)
      UNION SELECT json_extract(body,'$.source_context.preprocess.run_id') FROM artifacts WHERE json_valid(body)
    ''') if r[0]}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='collation_batches'").fetchone():
        protected.update(r[0] for r in db.execute('SELECT run_id FROM collation_batches'))
    rows = db.execute('''SELECT r.id FROM preprocess_runs r
      WHERE (?='' OR r.project_id=?) AND (?='' OR r.material_id=?)
        AND EXISTS (SELECT 1 FROM preprocess_runs newer
          WHERE newer.project_id=r.project_id AND newer.material_id=r.material_id AND newer.rowid>r.rowid)
    ''', (project_id, project_id, material_id, material_id))
    return [r['id'] for r in rows if r['id'] not in protected]


def prune_superseded_runs(db, project_id='', material_id=''):
    obsolete = unused_superseded_runs(db, project_id, material_id)
    db.executemany('DELETE FROM preprocess_runs WHERE id=?', ((run_id,) for run_id in obsolete))


def migrate(db, root, now, *, recover=True):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='preprocess_runs'").fetchone() and db.execute('SELECT 1 FROM materials LIMIT 1').fetchone():
        # Finish the existing library migration before taking a coherent upgrade backup.
        db.commit()
        folder = root / 'backups'
        folder.mkdir(exist_ok=True)
        destination = folder / 'before-0.3.0.sqlite'
        if not destination.exists():
            with closing(sqlite3.connect(destination)) as backup:
                db.backup(backup)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS preprocess_recipes (
        id TEXT PRIMARY KEY, recipe_json TEXT NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS preprocess_runs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, material_id TEXT NOT NULL,
        source_text TEXT NOT NULL, positions_json TEXT NOT NULL, recipe_json TEXT NOT NULL,
        signature TEXT NOT NULL, result_json TEXT NOT NULL, status TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1, output_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_preprocess_runs ON preprocess_runs(project_id,material_id,created_at);
      CREATE TABLE IF NOT EXISTS preprocess_jobs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, material_id TEXT NOT NULL,
        request_json TEXT NOT NULL, sample_text TEXT NOT NULL, response_text TEXT NOT NULL DEFAULT '',
        recipe_json TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
        model_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
    ''')
    known = {r['name'] for r in db.execute('PRAGMA table_info(research_units)')}
    if 'preprocess_json' not in known:
        db.execute("ALTER TABLE research_units ADD COLUMN preprocess_json TEXT NOT NULL DEFAULT '{}'")
    for recipe in BUILTINS:
        db.execute('INSERT OR IGNORE INTO preprocess_recipes(id,recipe_json,created_at) VALUES(?,?,?)', ('builtin-'+digest(recipe.model_dump())[:16], json.dumps(recipe.model_dump(), ensure_ascii=False), now()))
    if recover:
        db.execute("UPDATE preprocess_jobs SET status='paused',error='上次退出时识别尚未完成，可继续识别。' WHERE status IN ('queued','running')")
    if unused_superseded_runs(db):
        db.commit()
        folder = root / 'backups'
        folder.mkdir(exist_ok=True)
        destination = folder / 'before-0.3.4.sqlite'
        if not destination.exists():
            with closing(sqlite3.connect(destination)) as backup:
                db.backup(backup)
        db.execute('BEGIN IMMEDIATE')
        prune_superseded_runs(db)


FIXED_WORKFLOW = 'fixed-basic-v1'
FIXED_RECIPE = BUILTINS[1].model_copy(update={
    'name': '按卷拆分与基础清洗',
    'description': '按合璧卷题拆分，按词头还原重文号，去掉【】，保留引文间隔，卷内小标题独行。',
    'restore_repeat': True, 'repeat_mode': 'by_headword',
})


class SplitInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    material_id: str


class Input(BaseModel):
    material_id: str
    page_from: int = Field(default=1, ge=1)
    page_to: int = Field(ge=1)


class PreviewInput(Input):
    recipe: Recipe


class RunInput(PreviewInput):
    preview_signature: str
    confirm: bool = False


class AgentInput(Input):
    instructions: str = Field(default='', max_length=2000)


class ReviewInput(BaseModel):
    expected_revision: int = Field(ge=1)
    edit_id: str
    accept: bool
    replacement: str = Field(default='', max_length=20)


class AcceptInput(BaseModel):
    expected_revision: int = Field(ge=1)
    retain_pending: bool = False


class UnitsInput(BaseModel):
    node_ids: list[str] = Field(default=[], max_length=100)
    text_ids: list[str] = Field(default=[], max_length=200)


def register(app, connection, now, generate, settings, service):
    active_jobs = set()
    job_lock = threading.Lock()

    def project_exists(db, project_id):
        if not db.execute('SELECT 1 FROM projects WHERE id=? AND deleted=0', (project_id,)).fetchone():
            raise HTTPException(404, '找不到研究项目')

    def source(db, project_id, payload):
        project_exists(db, project_id)
        if db.execute('SELECT archived FROM projects WHERE id=? AND deleted=0', (project_id,)).fetchone()['archived']:
            raise HTTPException(400, '归档项目请先恢复再处理')
        try:
            from backend.knowledge.execution import activated
            from backend.knowledge.compat import snapshot,freeze,positions as input_positions
        except ModuleNotFoundError:
            from knowledge.execution import activated
            from knowledge.compat import snapshot,freeze,positions as input_positions
        if activated(db) or snapshot(db,payload.material_id):
            frozen=freeze(db,project_id,payload.material_id,payload.page_from,payload.page_to)
            payload.material_id=frozen['material_id']
            positions=input_positions(frozen)
            return frozen['body'],positions,[p['number'] for p in positions if p.get('missing')]
        if not research.attached(db, project_id, payload.material_id):
            raise HTTPException(400, '材料未加入当前项目')
        material = research.material_info(db, payload.material_id, project_id)
        if payload.page_from > payload.page_to or payload.page_to > material['page_count']:
            raise HTTPException(400, '请选择有效的材料范围')
        rows = db.execute('SELECT page_number,text FROM pages WHERE material_id=? AND page_number BETWEEN ? AND ? ORDER BY page_number', (payload.material_id, payload.page_from, payload.page_to)).fetchall()
        text, positions = '', []
        separator = '\n\n' if material['position_kind'] == 'page' else ''
        for row in rows:
            start = len(text)
            if text:
                text += separator
            text += row['text']
            positions.append({'number': row['page_number'], 'start': start, 'end': len(text)})
        if not text.strip():
            raise HTTPException(400, '所选范围没有文字层，请先提供录文；扫描 PDF 尚不能直接预处理')
        if len(text) > 10000000:
            raise HTTPException(400, '单次预处理最多 1,000 万字，请缩小范围')
        missing = [r['page_number'] for r in rows if not r['text'].strip()]
        return text, positions, missing

    def row_run(db, run_id):
        row = db.execute('SELECT * FROM preprocess_runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, '找不到处理版本')
        return row

    def run_info(row):
        result = json.loads(row['result_json'])
        return {'id': row['id'], 'project_id': row['project_id'], 'material_id': row['material_id'], 'status': row['status'],
                'revision': row['revision'], 'created_at': row['created_at'], 'signature': row['signature'], 'source_hash': digest(row['source_text']),
                'output_hash': row['output_hash'], 'recipe': json.loads(row['recipe_json']),
                'summary': {**{key: value for key, value in result.items() if key not in {'nodes', 'edits', 'texts'}}, 'text_count': len(texts_for(row, result))},
                'pending_count': sum(e['status'] == 'pending' for e in result['edits']),
                'applied_count': sum(e['status'] == 'applied' for e in result['edits'])}

    def texts_for(row, result):
        return result.get('texts') or split_texts(row['source_text'], result['nodes'], Recipe.model_validate_json(row['recipe_json']))[0]

    def recipe_value(raw):
        value = json.loads(raw)
        if not any('split' in rule for rule in value.get('rules', [])):
            for rule in value.get('rules', []):
                if rule.get('label') == '卷':
                    rule['split'] = True
        return Recipe.model_validate(value).model_dump()

    def excerpt(row, result, node=None):
        start, end = (node['start'], node['end']) if node else (0, len(row['source_text']))
        end_preview = min(end, start+8000)
        recipe = Recipe.model_validate_json(row['recipe_json'])
        text = prepared(row['source_text'], result['edits'], start, end_preview)
        return {'original': row['source_text'][start:end_preview], 'prepared': text, 'formatted': formatted(text, recipe),
                'start': start, 'end': end, 'truncated': end > end_preview}

    def assert_mutable(row, revision):
        if row['status'] == 'accepted':
            raise HTTPException(409, '已采用版本不可修改，请运行一个新版本')
        if row['revision'] != revision:
            raise HTTPException(409, '处理版本已更新，请重新加载后复核')

    @app.get('/api/preprocess/recipes')
    def recipes():
        with connection() as db:
            return [{'id': r['id'], 'recipe': recipe_value(r['recipe_json']), 'created_at': r['created_at']} for r in db.execute('SELECT * FROM preprocess_recipes ORDER BY created_at DESC,rowid DESC')]

    @app.post('/api/preprocess/recipes')
    def save_recipe(payload: Recipe):
        recipe_id = 'recipe-'+digest(payload.model_dump())[:24]
        with connection() as db:
            db.execute('INSERT OR IGNORE INTO preprocess_recipes(id,recipe_json,created_at) VALUES(?,?,?)', (recipe_id, payload.model_dump_json(), now()))
        return {'id': recipe_id, 'recipe': payload.model_dump()}

    @app.post('/api/projects/{project_id}/preprocess/preview')
    def preview(project_id: str, payload: PreviewInput):
        with connection() as db:
            text, positions, missing = source(db, project_id, payload)
        result = analyze(text, payload.recipe)
        if missing:
            result['warnings'].insert(0, '文字层缺失的页 / 段：' + ', '.join(map(str, missing[:80])) + '；这些范围未完成读取。')
        fake = {'source_text': text, 'recipe_json': payload.recipe.model_dump_json()}
        sample_nodes = result['texts'][:12]
        if len(result['texts']) > 12:
            sample_nodes = [result['texts'][i*(len(result['texts'])-1)//11] for i in range(12)]
        return {'signature': signature(text, payload.recipe, positions), 'summary': {k: v for k, v in result.items() if k not in {'nodes', 'edits', 'texts'}},
                'nodes': sample_nodes, 'samples': [{**node, **excerpt(fake, result, node)} for node in sample_nodes[:4]], 'edits': result['edits'][:30]}

    @app.post('/api/projects/{project_id}/preprocess/runs')
    def create_run(project_id: str, payload: RunInput):
        if not payload.confirm:
            raise HTTPException(400, '请先查看样例并确认方案')
        with connection() as db:
            text, positions, missing = source(db, project_id, payload)
        current_signature = signature(text, payload.recipe, positions)
        if current_signature != payload.preview_signature:
            raise HTTPException(409, '材料范围或方案已变化，请重新预览')
        result = analyze(text, payload.recipe)
        result['missing_positions'] = missing
        if not result['coverage_ok']:
            raise HTTPException(400, '原文覆盖校验失败，已停止处理')
        return save_run(project_id, payload.material_id, text, positions, payload.recipe, result)

    def save_run(project_id, material_id, text, positions, recipe, result, ready=False):
        run_id = secrets.token_hex(12)
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not research.attached(db, project_id, material_id):
                raise HTTPException(409, '材料已从当前项目移除')
            db.execute('INSERT INTO preprocess_runs(id,project_id,material_id,source_text,positions_json,recipe_json,signature,result_json,status,output_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, project_id, material_id, text, json.dumps(positions), recipe.model_dump_json(), signature(text, recipe, positions),
                        json.dumps(result, ensure_ascii=False), 'accepted' if ready else 'review', digest(prepared(text, result['edits'])) if ready else '', now()))
            prune_superseded_runs(db, project_id, material_id)
            return run_info(row_run(db, run_id))

    @app.post('/api/projects/{project_id}/preprocess/split')
    def split_basic(project_id: str, payload: SplitInput):
        with connection() as db:
            project_exists(db, project_id)
            if not research.attached(db, project_id, payload.material_id):
                raise HTTPException(400, '材料未加入当前项目')
            try:
                from backend.knowledge.execution import activated
                from backend.knowledge.compat import freeze,positions as input_positions
            except ModuleNotFoundError:
                from knowledge.execution import activated
                from knowledge.compat import freeze,positions as input_positions
            if activated(db):
                frozen=freeze(db,project_id,payload.material_id)
                payload.material_id=frozen['material_id'];text=frozen['body'];positions=input_positions(frozen)
                missing=[p['number'] for p in positions if p.get('missing')]
            else:
                material = research.material_info(db, payload.material_id, project_id)
                selected=Input(material_id=payload.material_id,page_to=material['page_count'])
                text, positions, missing = source(db, project_id, selected)
                payload.material_id=selected.material_id
        if missing:
            raise HTTPException(400, '第 '+ '、'.join(map(str, missing[:40])) +' 页没有文字层，请补充录文后再拆分；原结果保留。')
        result = analyze(text, FIXED_RECIPE)
        if not result['coverage_ok']:
            raise HTTPException(400, '原文覆盖校验失败，未保存新结果')
        if not any(t['kind'] == 'volume' for t in result['texts']):
            raise HTTPException(400, '没有识别到卷题。当前固定规则支持“卷五”或“古今合璧事类备要别集卷五”等卷首标题；原结果保留。')
        result.update(workflow=FIXED_WORKFLOW, missing_positions=[], restored_repeat_count=sum(e['kind']=='repeat' and e['status']=='applied' for e in result['edits']), repeat_review_count=sum(e.get('needs_review',False) for e in result['edits']))
        return save_run(project_id, payload.material_id, text, positions, FIXED_RECIPE, result, ready=True)

    @app.get('/api/projects/{project_id}/preprocess/runs')
    def runs(project_id: str, material_id: str = ''):
        with connection() as db:
            project_exists(db, project_id)
            try:
                from backend.knowledge.compat import execution_handles
            except ModuleNotFoundError:
                from knowledge.compat import execution_handles
            handles=execution_handles(db,material_id) if material_id else ['']
            placeholders=','.join('?' for _ in handles)
            rows = db.execute('''SELECT r.* FROM preprocess_runs r
              WHERE r.project_id=? AND (?='' OR r.material_id IN ('''+placeholders+'''))
                AND NOT EXISTS (SELECT 1 FROM preprocess_runs newer
                  WHERE newer.project_id=r.project_id AND newer.material_id=r.material_id AND newer.rowid>r.rowid)
              ORDER BY r.created_at DESC,r.rowid DESC''', (project_id, material_id, *handles))
            return [run_info(r) for r in rows]

    @app.get('/api/preprocess/runs/{run_id}')
    def get_run(run_id: str):
        with connection() as db:
            return run_info(row_run(db, run_id))

    @app.get('/api/preprocess/runs/{run_id}/nodes')
    def nodes(run_id: str, query: str = '', offset: int = 0, limit: int = 80):
        with connection() as db:
            result = json.loads(row_run(db, run_id)['result_json'])
        values = [n for n in result['nodes'] if query.strip().lower() in n['path'].lower()]
        offset, limit = max(0, offset), max(1, min(200, limit))
        return {'total': len(values), 'items': values[offset:offset+limit]}

    @app.get('/api/preprocess/runs/{run_id}/texts')
    def texts(run_id: str, query: str = '', offset: int = 0, limit: int = 80):
        with connection() as db:
            row = row_run(db, run_id)
            result = json.loads(row['result_json'])
        values = [text for text in texts_for(row, result) if query.strip().lower() in (text['title']+' '+text['source_title']).lower()]
        offset, limit = max(0, offset), max(1, min(500, limit))
        return {'total': len(values), 'items': values[offset:offset+limit]}

    @app.get('/api/preprocess/runs/{run_id}/text')
    def node_text(run_id: str, node_id: str = '', text_id: str = ''):
        with connection() as db:
            row = row_run(db, run_id)
            result = json.loads(row['result_json'])
        node = next((n for n in texts_for(row, result) if n['id'] == text_id), None) if text_id else next((n for n in result['nodes'] if n['id'] == node_id), None) if node_id else None
        if (node_id or text_id) and not node:
            raise HTTPException(404, '找不到所选原文节点')
        return excerpt(row, result, node)

    @app.get('/api/preprocess/runs/{run_id}/edits')
    def edits(run_id: str, status: str = 'pending', offset: int = 0, limit: int = 30):
        with connection() as db:
            values = json.loads(row_run(db, run_id)['result_json'])['edits']
        values = [e for e in values if status == 'all' or e['status'] == status]
        offset, limit = max(0, offset), max(1, min(100, limit))
        return {'total': len(values), 'items': values[offset:offset+limit]}

    @app.post('/api/preprocess/runs/{run_id}/review')
    def review(run_id: str, payload: ReviewInput):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = row_run(db, run_id)
            assert_mutable(row, payload.expected_revision)
            result = json.loads(row['result_json'])
            edit = next((e for e in result['edits'] if e['id'] == payload.edit_id), None)
            if not edit:
                raise HTTPException(404, '找不到修改记录')
            if payload.accept and not payload.replacement:
                raise HTTPException(400, '替换文字不能为空；保留原字请选“保留”')
            edit['replacement'] = payload.replacement if payload.accept else edit['original']
            edit['status'] = 'applied' if payload.accept else 'retained'
            edit['reviewed_at'] = now()
            prepared(row['source_text'], result['edits'])  # Reject overlap before persistence.
            db.execute('UPDATE preprocess_runs SET result_json=?,revision=revision+1 WHERE id=?', (json.dumps(result, ensure_ascii=False), run_id))
            return run_info(row_run(db, run_id))

    @app.post('/api/preprocess/runs/{run_id}/accept')
    def accept(run_id: str, payload: AcceptInput):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = row_run(db, run_id)
            assert_mutable(row, payload.expected_revision)
            result = json.loads(row['result_json'])
            if any(e['status'] == 'pending' for e in result['edits']) and not payload.retain_pending:
                raise HTTPException(400, '仍有待核项，请复核，或确认待核项保留原文并带入点校校记')
            text = prepared(row['source_text'], result['edits'])
            db.execute("UPDATE preprocess_runs SET status='accepted',output_hash=?,revision=revision+1 WHERE id=?", (digest(text), run_id))
            recipe = Recipe.model_validate_json(row['recipe_json'])
            db.execute('INSERT OR IGNORE INTO preprocess_recipes(id,recipe_json,created_at) VALUES(?,?,?)', ('recipe-'+digest(recipe.model_dump())[:24], recipe.model_dump_json(), now()))
            return run_info(row_run(db, run_id))

    @app.get('/api/preprocess/runs/{run_id}/export')
    def export(run_id: str, format: str = 'txt', text_id: str = ''):
        with connection() as db:
            row = row_run(db, run_id)
            result = json.loads(row['result_json'])
        if row['status'] != 'accepted':
            raise HTTPException(400, '请先采用处理版本再导出')
        recipe = Recipe.model_validate_json(row['recipe_json'])
        split_values = texts_for(row, result)
        def filename(value):
            return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value['title']).strip(' .')[:140] or '拆分文本'
        if format == 'zip':
            output = io.BytesIO()
            used_names = set()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for value in split_values:
                    name = filename(value)+'.txt'
                    if name.lower() in used_names:
                        name = filename(value)+f'-字符{value["start"]}.txt'
                    used_names.add(name.lower())
                    content = formatted(prepared(row['source_text'], result['edits'], value['start'], value['end']), recipe)
                    archive.writestr(name, content.encode('utf-8'))
                archive.writestr('_拆分记录.json', json.dumps({'run_id': run_id, 'source_hash': digest(row['source_text']), 'texts': split_values}, ensure_ascii=False, indent=2))
            return Response(content=output.getvalue(), media_type='application/octet-stream')
        selected = next((value for value in split_values if value['id'] == text_id), None) if text_id else None
        if text_id and not selected:
            raise HTTPException(404, '找不到这份拆分文本')
        text = formatted(prepared(row['source_text'], result['edits'], selected['start'] if selected else 0, selected['end'] if selected else None), recipe)
        if format == 'json':
            return {'filename': f'预处理-{run_id}.json', 'content': json.dumps({**run_info(row), 'formatted_output_hash': digest(text), 'positions': json.loads(row['positions_json']), 'texts': split_values, 'nodes': result['nodes'], 'edits': result['edits']}, ensure_ascii=False, indent=2)}
        return {'filename': filename(selected)+'.txt' if selected else f'预处理-{run_id}.txt', 'content': text}

    @app.post('/api/preprocess/runs/{run_id}/units')
    def create_units(run_id: str, payload: UnitsInput):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = row_run(db, run_id)
            if row['status'] != 'accepted':
                raise HTTPException(400, '请先采用处理版本，再建立点校工作')
            if not research.attached(db, row['project_id'], row['material_id'],historical=True):
                raise HTTPException(400, '来源材料已移出项目，请重新加入')
            result, recipe = json.loads(row['result_json']), Recipe.model_validate_json(row['recipe_json'])
            source_hash = digest(row['source_text'])
            if not payload.node_ids and not payload.text_ids or payload.node_ids and payload.text_ids:
                raise HTTPException(400, '请选择拆分文本或原文节点，两种选择不能混用')
            selected_ids = set(payload.text_ids or payload.node_ids)
            selected = [n for n in (texts_for(row, result) if payload.text_ids else result['nodes']) if n['id'] in selected_ids]
            if len(selected) != len(selected_ids):
                raise HTTPException(400, '包含不存在的原文节点')
            selected.sort(key=lambda n: (n['start'], -n['end']))
            unique = []
            for node in selected:
                if not any(p['start'] <= node['start'] and node['end'] <= p['end'] for p in unique):
                    unique.append(node)
            spans = [(node, node['start'], node['end']) for node in unique]
            if len(spans) > 200:
                raise HTTPException(400, f'所选范围将生成 {len(spans)} 个工作单元，请缩小选择（单次最多 200 个）')
            positions = json.loads(row['positions_json'])
            units = []
            for node, start, end in spans:
                unit_id = digest({'run': run_id, 'start': start, 'end': end})[:24]
                existing = db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone()
                if existing:
                    units.append(research.unit_info(existing, True))
                    continue
                relevant = [p['number'] for p in positions if p['start'] < end and p['end'] > start]
                if not relevant:
                    raise HTTPException(400, '原文位置映射缺失，无法建立工作')
                notes = []
                pending = [e for e in result['edits'] if (e['status'] == 'pending' or e.get('needs_review')) and start <= e['start'] < end]
                for edit in pending[:499]:
                    notes.append({'id': run_id+'-'+edit['id'], 'kind': 'question', 'location': f'原文字符 {edit["start"]}',
                                  'base_reading': edit['original'], 'witness_id': '', 'witness_reading': '', 'decision': '',
                                  'evidence': edit['reason']+'；'+edit['context'], 'status': 'pending'})
                if len(pending) > 499:
                    notes.append({'id': run_id+'-more-'+str(start), 'kind': 'question', 'location': f'原文字符 {start}–{end}', 'base_reading': '', 'witness_id': '', 'witness_reading': '', 'decision': '', 'evidence': f'另有 {len(pending)-499} 项待核，请在预处理版本 {run_id} 的记录中逐项核对。', 'status': 'pending'})
                original = row['source_text'][start:end]
                content = formatted(prepared(row['source_text'], result['edits'], start, end), recipe)
                title = node['title'][:200]
                category = ' / '.join(node['path'].split(' / ')[:-1])[:100]
                provenance = {'run_id': run_id, 'node_id': node['id'], 'source_start': start, 'source_end': end, 'text_id': node['id'] if payload.text_ids else '',
                              'source_hash': source_hash, 'output_hash': row['output_hash'], 'recipe_name': recipe.name, 'engine_version': result.get('engine_version', '1.0')}
                timestamp, notes_json = now(), json.dumps(notes, ensure_ascii=False)
                db.execute('''INSERT INTO research_units(id,project_id,kind,title,volume,category,material_id,page_from,page_to,location_label,original_text,content,notes_json,status,revision,created_at,updated_at,preprocess_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (unit_id, row['project_id'], 'collation', title, '', category, row['material_id'], min(relevant), max(relevant), f'预处理原文字符 {start}–{end}', original, content, notes_json, 'working', 1, timestamp, timestamp, json.dumps(provenance, ensure_ascii=False)))
                db.execute('INSERT INTO unit_versions(id,unit_id,revision,content,notes_json,status,created_at) VALUES(?,?,?,?,?,?,?)', (secrets.token_hex(12), unit_id, 1, content, notes_json, 'working', timestamp))
                db.execute('UPDATE research_units SET baseline_text=? WHERE id=?', (content, unit_id))
                units.append(research.unit_info(db.execute('SELECT * FROM research_units WHERE id=?', (unit_id,)).fetchone(), True))
            return units

    def job_info(row):
        return {'id': row['id'], 'project_id': row['project_id'], 'material_id': row['material_id'], 'status': row['status'], 'error': row['error'],
                'recipe': json.loads(row['recipe_json']) if row['recipe_json'] else None, 'created_at': row['created_at'],
                'request': json.loads(row['request_json']), 'model': json.loads(row['model_json']).get('model', '')}

    class AgentState(TypedDict, total=False):
        response: str
        recipe: dict

    def worker(job_id):
        try:
            with connection() as db:
                row = dict(db.execute('SELECT * FROM preprocess_jobs WHERE id=?', (job_id,)).fetchone())
                db.execute("UPDATE preprocess_jobs SET status='running',error='',updated_at=? WHERE id=?", (now(), job_id))
            def call_model(state):
                if row['response_text']:
                    return {'response': row['response_text']}
                response = generate({'task_type': 'preprocess_analyze', 'instructions': json.loads(row['request_json']).get('instructions', ''), 'target_language': '中文'}, row['sample_text'], json.loads(row['model_json']))
                with connection() as db:
                    db.execute('UPDATE preprocess_jobs SET response_text=?,updated_at=? WHERE id=?', (response, now(), job_id))
                return {'response': response}
            def validate(state):
                response = state['response'].strip()
                if response.startswith('```'):
                    response = '\n'.join(response.splitlines()[1:-1])
                recipe = Recipe.model_validate_json(response)
                if recipe.restore_repeat or recipe.remove_note_brackets or recipe.quote_spacing:
                    raise ValueError('Agent 不得自行启用改字规则，请在样例预览中由用户选择')
                return {'recipe': recipe.model_dump()}
            graph = StateGraph(AgentState)
            graph.add_node('identify_format', call_model)
            graph.add_node('validate_rules', validate)
            graph.add_edge(START, 'identify_format')
            graph.add_edge('identify_format', 'validate_rules')
            graph.add_edge('validate_rules', END)
            output = graph.compile().invoke({})
            with connection() as db:
                db.execute("UPDATE preprocess_jobs SET status='completed',recipe_json=?,updated_at=? WHERE id=?", (json.dumps(output['recipe'], ensure_ascii=False), now(), job_id))
        except Exception as exc:
            with connection() as db:
                db.execute("UPDATE preprocess_jobs SET status='failed',error=?,updated_at=? WHERE id=?", ('格式识别未完成：'+str(exc)[:1600], now(), job_id))
        finally:
            with job_lock:
                active_jobs.discard(job_id)
            service.tasks.discard(threading.current_thread())

    def start_job(job_id):
        if not service.embedded_workers:
            return
        with job_lock:
            if job_id in active_jobs:
                return
            active_jobs.add(job_id)
        thread = threading.Thread(target=worker, args=(job_id,), daemon=True)
        service.tasks.add(thread)
        thread.start()

    @app.post('/api/projects/{project_id}/preprocess/analyze')
    def start_analysis(project_id: str, payload: AgentInput):
        model = settings()
        if not model.get('base_url') or not model.get('model'):
            raise HTTPException(400, '请先在“模型与设置”中配置模型，再识别新格式；已有方案可以直接预览')
        with connection() as db:
            text, _, missing = source(db, project_id, payload)
            job_id, timestamp = secrets.token_hex(12), now()
            request = {**payload.model_dump(), 'source_hash': digest(text), 'missing_positions': missing, 'prompt_version': 'format-v4-paragraphs'}
            db.execute('INSERT INTO preprocess_jobs(id,project_id,material_id,request_json,sample_text,status,model_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (job_id, project_id, payload.material_id, json.dumps(request, ensure_ascii=False), samples(text), 'queued', json.dumps(model), timestamp, timestamp))
        start_job(job_id)
        with connection() as db:
            return job_info(db.execute('SELECT * FROM preprocess_jobs WHERE id=?', (job_id,)).fetchone())

    @app.get('/api/projects/{project_id}/preprocess/jobs')
    def jobs(project_id: str, material_id: str = ''):
        with connection() as db:
            try:
                from backend.knowledge.compat import execution_handles
            except ModuleNotFoundError:
                from knowledge.compat import execution_handles
            handles=execution_handles(db,material_id) if material_id else ['']
            return [job_info(r) for r in db.execute('SELECT * FROM preprocess_jobs WHERE project_id=? AND (?="" OR material_id IN ('+','.join('?' for _ in handles)+')) ORDER BY created_at DESC,rowid DESC LIMIT 20', (project_id, material_id, *handles))]

    @app.get('/api/preprocess/jobs/{job_id}')
    def get_job(job_id: str):
        with connection() as db:
            row = db.execute('SELECT * FROM preprocess_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, '找不到识别任务')
            return job_info(row)

    @app.post('/api/preprocess/jobs/{job_id}/resume')
    def resume_job(job_id: str):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM preprocess_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, '找不到识别任务')
            if row['status'] not in {'paused', 'failed'}:
                raise HTTPException(409, '此任务不需要重试')
            # Invalid responses must get a fresh call; persisted interrupted calls are reused.
            response = '' if row['status'] == 'failed' else row['response_text']
            db.execute("UPDATE preprocess_jobs SET status='queued',response_text=?,model_json=?,error='' WHERE id=?", (response, json.dumps(settings()), job_id))
        start_job(job_id)
        return {'id': job_id, 'status': 'queued'}

    return {'create_units': create_units, 'execute_job': worker}
