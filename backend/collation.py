"""Durable sequential collation with immutable inputs and per-request usage."""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, ConfigDict, Field

try:
    from backend.text_diff import compare_text
    from backend import research
    from backend.preprocess_engine import Recipe, digest, formatted, prepared, punctuation_error, split_texts
    from backend.collation_prompt import DEFAULT_SYSTEM, CONTRACT, LEGACY_CONTRACT, read_profile, request_options, OutputLimitError, NoPunctuationError, SourceFidelityError
except ModuleNotFoundError:
    from text_diff import compare_text
    import research
    from preprocess_engine import Recipe, digest, formatted, prepared, punctuation_error, split_texts
    from collation_prompt import DEFAULT_SYSTEM, CONTRACT, LEGACY_CONTRACT, read_profile, request_options, OutputLimitError, NoPunctuationError, SourceFidelityError

PROMPT_VERSION = 'collation-v1'
MAX_ATTEMPTS = 3
LEASE_SECONDS = 45


@contextmanager
def database(root):
    db = sqlite3.connect(Path(root)/'library.sqlite', timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def alive(pid):
    if not pid:
        return False
    if pid == os.getpid():
        return True
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def recover(db, now):
    lease = db.execute('SELECT * FROM collation_control WHERE id=1').fetchone()
    if lease['owner'] and lease['expires'] > time.time() and alive(lease['pid']):
        return
    db.execute("UPDATE collation_attempts SET status='unknown',error='上次调用中断，是否产生用量未知',finished_at=? WHERE status='requesting'", (now(),))
    for batch in db.execute("SELECT * FROM collation_batches WHERE status IN ('running','pausing','stopping')").fetchall():
        status = 'stopped' if batch['status'] == 'stopping' else 'interrupted'
        if status == 'stopped':
            db.execute("UPDATE collation_items SET status='stopped' WHERE batch_id=? AND status IN ('queued','running')", (batch['id'],))
        else:
            db.execute("UPDATE collation_items SET status='queued' WHERE batch_id=? AND status='running'", (batch['id'],))
        elapsed = max(0, time.time()-batch['active_since']) if batch['active_since'] else 0
        db.execute("UPDATE collation_batches SET status=?,owner='',active_seconds=active_seconds+?,active_since=NULL,error='上次退出时执行中断，可继续已保存片段' WHERE id=?", (status, elapsed, batch['id']))
    db.execute("UPDATE collation_control SET owner='',pid=0,expires=0 WHERE id=1")


def migrate(db, root, now):
    known = {r['name'] for r in db.execute('PRAGMA table_info(research_units)')}
    if 'baseline_text' not in known:
        db.commit()
        if db.execute('SELECT 1 FROM materials LIMIT 1').fetchone():
            folder = Path(root)/'backups'
            folder.mkdir(exist_ok=True)
            backup = folder/'before-0.3.7.sqlite'
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as destination:
                    db.backup(destination)
        db.execute('ALTER TABLE research_units ADD COLUMN baseline_text TEXT')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS collation_batches (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, title TEXT NOT NULL,
        model_json TEXT NOT NULL, rules TEXT NOT NULL, prompt_version TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT NOT NULL DEFAULT '', active_seconds REAL NOT NULL DEFAULT 0, active_since REAL,
        error TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS collation_items (
        id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,seq INTEGER NOT NULL,text_id TEXT NOT NULL,title TEXT NOT NULL,
        material_id TEXT NOT NULL,baseline_text TEXT NOT NULL,baseline_hash TEXT NOT NULL,original_text TEXT NOT NULL,
        source_start INTEGER NOT NULL,source_end INTEGER NOT NULL,unit_id TEXT NOT NULL DEFAULT '',input_revision INTEGER NOT NULL DEFAULT 0,
        input_content_hash TEXT NOT NULL DEFAULT '',protect_manual INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'queued',merge_status TEXT NOT NULL DEFAULT '',
        result_text TEXT NOT NULL DEFAULT '',questions_json TEXT NOT NULL DEFAULT '[]',error TEXT NOT NULL DEFAULT '',finished_at TEXT NOT NULL DEFAULT '',
        UNIQUE(batch_id,text_id)
      );
      CREATE TABLE IF NOT EXISTS collation_chunks (
        id TEXT PRIMARY KEY,item_id TEXT NOT NULL,root_id TEXT NOT NULL,parent_id TEXT NOT NULL DEFAULT '',
        start INTEGER NOT NULL,end INTEGER NOT NULL,input_text TEXT NOT NULL,lead TEXT NOT NULL,cache_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',output_text TEXT NOT NULL DEFAULT '',questions_json TEXT NOT NULL DEFAULT '[]',
        reused_from TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS collation_attempts (
        id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,item_id TEXT NOT NULL,chunk_id TEXT NOT NULL,budget_id TEXT NOT NULL,status TEXT NOT NULL,
        response_json TEXT NOT NULL DEFAULT '',usage_json TEXT,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,
        response_id TEXT NOT NULL DEFAULT '',finish_reason TEXT NOT NULL DEFAULT '',latency_ms INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',request_json TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS collation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT NOT NULL,item_id TEXT NOT NULL DEFAULT '',chunk_id TEXT NOT NULL DEFAULT '',
        attempt_id TEXT NOT NULL DEFAULT '',level TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS collation_control (id INTEGER PRIMARY KEY,owner TEXT NOT NULL,pid INTEGER NOT NULL,expires REAL NOT NULL);
      INSERT OR IGNORE INTO collation_control(id,owner,pid,expires) VALUES(1,'',0,0);
      CREATE INDEX IF NOT EXISTS idx_collation_batch_project ON collation_batches(project_id,created_at);
      CREATE INDEX IF NOT EXISTS idx_collation_item_batch ON collation_items(batch_id,seq);
      CREATE INDEX IF NOT EXISTS idx_collation_chunk_item ON collation_chunks(item_id,start);
      CREATE INDEX IF NOT EXISTS idx_collation_cache ON collation_chunks(cache_key,status);
      CREATE INDEX IF NOT EXISTS idx_collation_attempt_chunk ON collation_attempts(chunk_id);
      CREATE INDEX IF NOT EXISTS idx_collation_attempt_batch ON collation_attempts(batch_id);
      CREATE INDEX IF NOT EXISTS idx_collation_event_batch ON collation_events(batch_id,id);
    ''')
    if 'request_json' not in {r['name'] for r in db.execute('PRAGMA table_info(collation_attempts)')}:
        db.commit()
        if db.execute('SELECT 1 FROM materials LIMIT 1').fetchone():
            folder=Path(root)/'backups';folder.mkdir(exist_ok=True);backup=folder/'before-0.3.9.sqlite'
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as destination: db.backup(destination)
        db.execute("ALTER TABLE collation_attempts ADD COLUMN request_json TEXT NOT NULL DEFAULT ''")
    recover(db, now)


def token_usage(value):
    usage = value if isinstance(value, dict) else {}
    def number(*keys):
        for key in keys:
            v = usage.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                return v
        return None
    source, output, total = number('prompt_tokens','input_tokens'), number('completion_tokens','output_tokens'), number('total_tokens')
    if total is None and source is not None and output is not None:
        total = source+output
    return source, output, total


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(min_length=1,max_length=3000)
    reason: str = Field(min_length=1,max_length=3000)
    suggestion: str = Field(default='',max_length=3000)


class EditorialResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str
    questions: list[Question] = Field(default=[],max_length=100)


def punctuation_review(source,output):
    differences=compare_text(source,output)
    inserted=differences['inserted_punctuation']
    changed=differences['changed_groups']
    body_lines=[line for line in source.splitlines() if sum(c.isalpha() for c in line)>=24 and not re.search(r'[。，、；：？！,.!?;:]',line)]
    return {'inserted_punctuation':inserted,'unchanged':source==output,'unpunctuated_body_lines':len(body_lines),
            'text_changes':changed,'characters_preserved':not bool(punctuation_error(source,output)),
            'requires_review':inserted==0 and changed==0,'notice':f'有 {changed} 处文字差异，已高亮，待人工复核；不会因此中止批量执行' if changed else '未新增标点；格式与字序通过，尚不能据此确认已完成点校，请人工核对或调整提示词' if inserted==0 else '已新增标点，断句是否正确仍需人工复核'}

def edge_parts(source):
    core=source.strip()
    if not core: return source,'',''
    left=len(source)-len(source.lstrip())
    right=len(source.rstrip())
    return source[:left],core,source[right:]

def protected_text(source):
    prefix='[[PUA_'
    while prefix in source: prefix+='_'
    mapping={}
    for char in dict.fromkeys(source):
        code=ord(char)
        if 0xe000<=code<=0xf8ff or 0xf0000<=code<=0xffffd or 0x100000<=code<=0x10fffd:
            mapping[prefix+f'{code:X}'+']]']=char
    encoded=source
    for token,char in mapping.items(): encoded=encoded.replace(char,token)
    return encoded,mapping

def model_input_text(source,input_frame=None,glyph_mode=None):
    source=source.strip() if input_frame=='edge-whitespace-v1' else source
    return protected_text(source)[0] if glyph_mode=='pua-placeholder-v1' else source

def validate_reply(source, reply, require_progress=False, input_frame=None,glyph_mode=None,allow_changes=True):
    if reply.get('finish_reason') in {'length','max_tokens'}:
        raise OutputLimitError('达到输出 token 上限，点校正文被截断或推理尚未结束')
    if reply.get('finish_reason') == 'content_filter':
        raise ValueError('模型输出截断或被过滤，不能作为完整片段')
    raw = reply['text'].strip()
    if raw.startswith('```'):
        raw = '\n'.join(raw.splitlines()[1:-1])
    result = EditorialResult.model_validate_json(raw)
    target=source
    if input_frame=='edge-whitespace-v1':
        prefix,target,suffix=edge_parts(source)
        result.text=result.text.strip()
    original_target=target
    mapping={}
    if glyph_mode=='pua-placeholder-v1': target,mapping=protected_text(target)
    error = punctuation_error(target,result.text)
    if error and not allow_changes:
        raise SourceFidelityError(error)
    for question in result.questions:
        if question.quote not in target and (not allow_changes or question.quote not in result.text):
            raise ValueError('校记引用不在本次输入中，无法定位：'+question.quote[:40])
    for token,char in mapping.items():
        if not allow_changes and result.text.count(token)!=target.count(token):
            raise SourceFidelityError('标点结果未通过原字校验：私用区字占位符被修改或插入内部标点，必须整串原样保留')
        result.text=result.text.replace(token,char)
        for q in result.questions:
            for field in ('quote','reason','suggestion'): setattr(q,field,getattr(q,field).replace(token,char))
    error=punctuation_error(original_target,result.text)
    if error and not allow_changes: raise SourceFidelityError(error)
    if input_frame=='edge-whitespace-v1':
        result.text=prefix+result.text+suffix
        error=punctuation_error(source,result.text)
        if error and not allow_changes: raise ValueError(error)
    if source.strip() and not result.text.strip(): raise ValueError('模型未返回有效正文')
    quality=punctuation_review(source,result.text)
    if require_progress and quality['inserted_punctuation']==0 and quality['unpunctuated_body_lines'] and not (allow_changes and quality['text_changes']):
        raise NoPunctuationError('模型仅返回原正文，没有给未标点长正文添加标点；疑字建议不能代替点校')
    return result


def plan(text, limit):
    if not text:
        raise ValueError('本卷文本为空')
    start = 0
    while start < len(text):
        end = min(len(text),start+limit)
        if end < len(text):
            boundary = text.rfind('\n',start,end)
            if boundary >= start+max(20,limit//3):
                end = boundary+1
        yield start,end,text[start:end],text[max(0,start-200):start]
        start = end


def output_budget(model):
    if model.get('collation_config'):
        return model['collation_config']['max_tokens']
    return min(int(model.get('max_output_tokens') or 8192),max(256,(int(model.get('context_tokens') or 16384)-2400)//2))


def output_options(budget):
    return {} if budget is None else {'max_tokens':budget}


def chunk_limit(model, rules=''):
    context = int(model.get('context_tokens') or 16384)
    output = output_budget(model)
    overhead = 2400+3*len(rules)
    if model.get('collation_config'):
        context = int(model.get('context_tokens') or max(16384,(output or 8192)+8192))
        overhead += 3*max(0,len(model['collation_config']['system_prompt'])-len(DEFAULT_SYSTEM))
        if output is None:
            # Only estimate input room; do not turn this into a request output cap.
            room=context-overhead
            if room<1024:
                raise HTTPException(400,'项目规则超过已填写模型容量，请精简规则或填写正确模型容量')
            return max(80,min(2000,room//6))
        room=context-overhead-output
        if room<240:
            raise HTTPException(400,'输出上限与项目规则超过本模型容量，请在点校调试中降低输出上限或填写正确模型容量')
        return max(80,min(2000,output//3,room//3))
    if context-overhead < 1024:
        raise HTTPException(400,'项目规则超过本模型的保守容量预算，请精简规则或在模型设置填写正确容量')
    return max(80,min(2000,output//3,(context-overhead)//6))


def messages_for(item, chunk, rules, config=None, contract=None, input_frame=None, feedback='',glyph_mode=None):
    system = config['system_prompt']+'\n\n'+(contract or LEGACY_CONTRACT) if config else DEFAULT_SYSTEM
    text=model_input_text(chunk['input_text'],input_frame,glyph_mode)
    payload={'file':item['title'],'project_rules':rules or '保留原字；判断待人工复核',
             'readonly_lead_context':chunk['lead'],'source_to_punctuate':text}
    if feedback: payload['previous_validation_feedback']=feedback
    user = json.dumps(payload,ensure_ascii=False)
    return [{'role':'system','content':system},{'role':'user','content':user}]

def fidelity_feedback(source,error):
    match=re.search(r'位置 (\d+)',error)
    position=min(len(source),int(match.group(1))) if match else 0
    context=source[max(0,position-35):position+65]
    char=source[position:position+1]
    return (f'上一轮被原字校验拒绝：{error} 原文字符位置 {position} 必须保留 {char!r}，原文附近片段：{context!r}。'
            '本轮重新对整个 source_to_punctuate 断句，只插入标点。即使觉得底本是错字、异体字或 OCR 误识，也不能替换、补删或转换字形；候选修订仅可放在 questions 中。')


def cache_key(item, chunk, model, rules):
    key={'messages':messages_for(item,chunk,rules,model.get('collation_config'),model.get('collation_contract'),model.get('collation_input_frame'),glyph_mode=model.get('collation_glyph_mode')),'model':{k:model.get(k) for k in ('base_url','model')},'prompt':PROMPT_VERSION,'planner':'natural-lines-v1'}
    if model.get('collation_config'):
        key['request_options']={**request_options(model),**output_options(output_budget(model))}
    if model.get('collation_validation_version'):
        key['validation_version']=model['collation_validation_version']
    if model.get('collation_input_frame'):
        key['input_frame']=model['collation_input_frame']
        key['complete_input_hash']=digest(chunk['input_text'])
    if model.get('collation_glyph_mode'): key['glyph_mode']=model['collation_glyph_mode']
    return digest(key)


class ComparisonInput(BaseModel):
    baseline: str = Field(max_length=2500000)
    content: str = Field(max_length=3000000)


class BatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str
    text_ids: list[str] | None = Field(default=None,max_length=5000)
    request_id: str = Field(min_length=8,max_length=80)


class BatchAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['pause','resume','stop','retry']


class AdoptInput(BaseModel):
    expected_revision: int = Field(ge=1)


def event(db, now, batch_id, message, item_id='',chunk_id='',attempt_id='',level='info'):
    db.execute('INSERT INTO collation_events(batch_id,item_id,chunk_id,attempt_id,level,message,created_at) VALUES(?,?,?,?,?,?,?)',
               (batch_id,item_id,chunk_id,attempt_id,level,message[:3000],now()))


def add_notes(existing, additions, marker):
    # Preserve human records. An overflow is visible and links to the full report.
    combined = list(existing)+list(additions)
    if len(combined) <= 500:
        return combined
    return combined[:499]+[{'id':marker+'-overflow','kind':'question','location':'完整复核报告','base_reading':'','witness_id':'','witness_reading':'',
                            'decision':'','evidence':f'另有 {len(combined)-499} 项；请查看本文件批量点校完整报告及底本问题后复核。','status':'pending'}]

class Hold(Exception):
    pass


class Runner:
    def __init__(self, service, now, call, create_units):
        self.service,self.now,self.call,self.create_units = service,now,call,create_units
        self.lock = threading.Lock()
        self.workers = {}
        self.inflight = {}
        self.shutdown_roots = set()
        self.retry_delay = 1.0

    def cancel_batch(self,root,batch_id):
        with self.lock:
            pending=self.inflight.get((Path(root),batch_id))
            if pending is not None:pending.set()

    def kick(self):
        root = Path(self.service.root)
        with self.lock:
            if root in self.shutdown_roots or root in self.workers:
                return
            worker = threading.Thread(target=self._worker,args=(root,),daemon=True,name='collation-serial')
            self.workers[root] = worker
            self.service.tasks.add(worker)
            worker.start()

    def _worker(self, root):
        owner = secrets.token_hex(16)
        owned = False
        heartbeat_stop = threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(8):
                try:
                    with database(root) as db:
                        db.execute('UPDATE collation_control SET expires=? WHERE id=1 AND owner=?', (time.time()+LEASE_SECONDS,owner))
                except sqlite3.Error:
                    pass
        beat = None
        try:
            with database(root) as db:
                db.execute('BEGIN IMMEDIATE')
                recover(db,self.now)
                lease = db.execute('SELECT * FROM collation_control WHERE id=1').fetchone()
                if lease['owner']:
                    return
                db.execute('UPDATE collation_control SET owner=?,pid=?,expires=? WHERE id=1', (owner,os.getpid(),time.time()+LEASE_SECONDS))
                owned = True
            beat = threading.Thread(target=heartbeat,daemon=True,name='collation-heartbeat')
            beat.start()
            while root == self.service.root and root not in self.shutdown_roots:
                with database(root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    batch = db.execute("SELECT * FROM collation_batches WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                    if not batch:
                        break
                    db.execute("UPDATE collation_batches SET status='running',owner=?,started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,active_since=?,error='' WHERE id=?",(owner,self.now(),time.time(),batch['id']))
                    event(db,self.now,batch['id'],'开始按文件顺序执行批次')
                    batch = dict(db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone())
                self._batch(root,batch,owner)
        except Exception as error:
            try:
                with database(root) as db:
                    for b in db.execute('SELECT * FROM collation_batches WHERE owner=? AND status IN (\'running\',\'pausing\',\'stopping\')',(owner,)).fetchall():
                        self._end(db,b,'interrupted',str(error)[:1500])
                        event(db,self.now,b['id'],'执行中断，已保存结果可恢复：'+str(error)[:700],level='error')
            except sqlite3.Error:
                pass
        finally:
            heartbeat_stop.set()
            if beat:
                beat.join(2)
            if owned:
                with database(root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    for b in db.execute('SELECT * FROM collation_batches WHERE owner=? AND status IN (\'running\',\'pausing\',\'stopping\')',(owner,)).fetchall():
                        self._end(db,b,'stopped' if b['status']=='stopping' else 'interrupted','执行已中断，可继续已保存片段')
                    db.execute("UPDATE collation_control SET owner='',pid=0,expires=0 WHERE owner=?",(owner,))
            with self.lock:
                self.workers.pop(root,None)
            self.service.tasks.discard(threading.current_thread())
            if owned and root == self.service.root and root not in self.shutdown_roots:
                with database(root) as db:
                    queued = db.execute("SELECT 1 FROM collation_batches WHERE status='queued' LIMIT 1").fetchone()
                if queued:
                    self.kick()

    def _end(self, db, batch, status, error=''):
        elapsed = max(0,time.time()-batch['active_since']) if batch['active_since'] else 0
        terminal = status in {'completed','completed_with_errors','stopped'}
        db.execute('UPDATE collation_batches SET status=?,owner=\'\',active_seconds=active_seconds+?,active_since=NULL,error=?,finished_at=? WHERE id=?',
                   (status,elapsed,error,self.now() if terminal else '',batch['id']))
        if status=='stopped':
            db.execute("UPDATE collation_items SET status='stopped' WHERE batch_id=? AND status IN ('queued','running')",(batch['id'],))
        else:
            db.execute("UPDATE collation_items SET status='queued' WHERE batch_id=? AND status='running'",(batch['id'],))

    def _batch(self, root, batch, owner):
        while True:
            with database(root) as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                if current['owner'] != owner:
                    return
                if current['status'] in {'pausing','stopping'} or root != self.service.root or root in self.shutdown_roots:
                    status = 'stopped' if current['status']=='stopping' else 'paused'
                    self._end(db,current,status)
                    event(db,self.now,batch['id'],'已停止，在途请求已结束或取消；已完成片段保留' if status=='stopped' else '当前响应与片段已保存；已暂停')
                    return
                item = db.execute("SELECT * FROM collation_items WHERE batch_id=? AND status IN ('queued','running') ORDER BY seq LIMIT 1",(batch['id'],)).fetchone()
                if not item:
                    failed = db.execute("SELECT 1 FROM collation_items WHERE batch_id=? AND status='failed' LIMIT 1",(batch['id'],)).fetchone()
                    self._end(db,current,'completed_with_errors' if failed else 'completed')
                    event(db,self.now,batch['id'],'机器处理结束，'+('存在失败文件；成功稿仍需人工复核' if failed else '成功稿等待人工复核'))
                    return
                item = dict(item)
                db.execute("UPDATE collation_items SET status='running',error='' WHERE id=?",(item['id'],))
                event(db,self.now,batch['id'],'开始文件：'+item['title'],item['id'])
            try:
                self._item(root,batch,item,owner)
            except Hold:
                continue
            except Exception as error:
                with database(root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                    if getattr(error,'fatal',False):
                        db.execute("UPDATE collation_items SET status='queued',error=? WHERE id=?",(str(error)[:1500],item['id']))
                        self._end(db,current,'paused',str(error)[:1500])
                        event(db,self.now,batch['id'],'配置或服务权限错误，暂停批次：'+str(error)[:900],item['id'],level='error')
                        return
                    db.execute("UPDATE collation_items SET status='failed',error=?,finished_at=? WHERE id=?",(str(error)[:1500],self.now(),item['id']))
                    event(db,self.now,batch['id'],'文件失败，保留片段并继续下一文件：'+str(error)[:900],item['id'],level='error')

    def _item(self, root, batch, item, owner):
        while True:
            with database(root) as db:
                chunk = db.execute("SELECT * FROM collation_chunks WHERE item_id=? AND status='queued' ORDER BY start LIMIT 1",(item['id'],)).fetchone()
                current = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                if chunk is None:
                    break
                if current['status']!='running' or current['owner']!=owner:
                    raise Hold()
            self._chunk(root,batch,item,dict(chunk),owner)
        with database(root) as db:
            chunks = db.execute("SELECT * FROM collation_chunks WHERE item_id=? AND status!='split' ORDER BY start",(item['id'],)).fetchall()
            cursor = 0
            output,questions = [],[]
            for c in chunks:
                if c['start']!=cursor or c['end']<=c['start'] or c['status']!='done' or item['baseline_text'][c['start']:c['end']]!=c['input_text']:
                    raise ValueError('内部片段覆盖或完成状态校验失败')
                output.append(c['output_text'])
                for q in json.loads(c['questions_json']):
                    offset = c['input_text'].find(q['quote'])
                    q['location'] = f'本卷底文字符 {c["start"]+offset}–{c["start"]+offset+len(q["quote"])}' if c['input_text'].count(q['quote'])==1 else ('点校稿引用，底文未定位，需人工复核' if offset<0 else f'本卷底文字符 {c["start"]}–{c["end"]}（多处引用，需定位）')
                    questions.append(q)
                cursor = c['end']
            combined = ''.join(output)
            if cursor!=len(item['baseline_text']):
                raise ValueError('内部片段未覆盖全文')
            error = punctuation_error(item['baseline_text'],combined)
            has_changes=bool(error)
        if root != self.service.root:
            raise Hold()
        # The immutable run remains protected by the batch, including failed items.
        if item['unit_id']:
            unit_id = item['unit_id']
        else:
            units = self.create_units(batch['run_id'],{'text_ids':[item['text_id']]})
            unit_id = units[0]['id']
        with database(root) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM collation_items WHERE id=?',(item['id'],)).fetchone()
            if current['status']=='done':
                return
            unit = db.execute('SELECT * FROM research_units WHERE id=?',(unit_id,)).fetchone()
            batch_state = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
            additions = self.result_notes(item['id'],questions)
            additions.append({'id':item['id']+'-ai','kind':'ai','location':unit['location_label'],'base_reading':'','witness_id':'','witness_reading':'',
                              'decision':'批量点校结果，待人工复核','evidence':'批次 '+batch['id']+('；片段覆盖通过，含文字差异待人工复核' if has_changes else '；原字和片段覆盖校验通过'),'status':'pending'})
            notes = json.loads(unit['notes_json'])+additions
            expected = item['input_revision'] or 1
            untouched = unit['revision']==expected and digest(unit['content'])==item['input_content_hash'] and unit['status']!='approved'
            apply = untouched and not item['protect_manual'] and batch_state['status'] not in {'stopping','stopped'} and len(notes)<=20000
            if apply:
                self.write_unit(db,unit,combined,notes)
            db.execute("UPDATE collation_items SET status='done',merge_status=?,unit_id=?,result_text=?,questions_json=?,error='',finished_at=? WHERE id=?",
                       ('applied' if apply else 'candidate',unit_id,combined,json.dumps(questions,ensure_ascii=False),self.now(),item['id']))
            event(db,self.now,batch['id'],('文件处理完成，含文字差异待复核，' if has_changes else '文件校验通过，')+('已保存待复核稿' if apply else '已有稿件改变，结果另存候选，未覆盖'),item['id'])

    def result_notes(self, item_id, questions):
        return [{'id':item_id+'-q-'+str(i),'kind':'question','location':q['location'],
                 'base_reading':'' if q['location'].startswith('点校稿引用') else q['quote'],'witness_id':'','witness_reading':q.get('suggestion',''),
                 'decision':'','evidence':q['reason']+('；点校稿引用：'+q['quote'] if q['location'].startswith('点校稿引用') else ''),'status':'pending'} for i,q in enumerate(questions)]

    def write_unit(self, db, unit, content, notes):
        revision = unit['revision']+1
        notes_json,timestamp = json.dumps(notes,ensure_ascii=False),self.now()
        db.execute("UPDATE research_units SET content=?,notes_json=?,status='review',revision=?,updated_at=? WHERE id=? AND revision=?",
                   (content,notes_json,revision,timestamp,unit['id'],unit['revision']))
        db.execute('INSERT INTO unit_versions(id,unit_id,revision,content,notes_json,status,created_at) VALUES(?,?,?,?,?,?,?)',(secrets.token_hex(12),unit['id'],revision,content,notes_json,'review',timestamp))

    def _chunk(self, root, batch, item, chunk, owner):
        model = json.loads(batch['model_json'])
        # Complete responses rejected by the former strict character rule can be
        # reviewed without another paid call. Keep their original error receipt.
        with database(root) as db:
            previous=db.execute("SELECT * FROM collation_attempts WHERE chunk_id=? AND status='invalid' AND error LIKE '%原字校验%' ORDER BY rowid DESC",(chunk['id'],)).fetchall()
        for attempt in previous:
            try:
                result=validate_reply(chunk['input_text'],json.loads(attempt['response_json']),bool(model.get('collation_validation_version')),model.get('collation_input_frame'),model.get('collation_glyph_mode'))
            except (ValueError,KeyError,TypeError):
                continue
            with database(root) as db:
                db.execute("UPDATE collation_chunks SET status='done',output_text=?,questions_json=?,error='' WHERE id=?",(result.text,json.dumps([q.model_dump() for q in result.questions],ensure_ascii=False),chunk['id']))
                event(db,self.now,batch['id'],'复用此前完整响应，文字差异转待复核，无新增模型调用',item['id'],chunk['id'],attempt['id'])
            return
        if model.get('collation_input_frame')=='edge-whitespace-v1' and not chunk['input_text'].strip():
            with database(root) as db:
                db.execute("UPDATE collation_chunks SET status='done',output_text=?,questions_json='[]' WHERE id=?",(chunk['input_text'],chunk['id']))
                event(db,self.now,batch['id'],'纯空白片段原样保留，不调用模型',item['id'],chunk['id'])
            return
        while True:
            with database(root) as db:
                count = db.execute('SELECT count(*) FROM collation_attempts WHERE budget_id=?',(chunk['root_id'],)).fetchone()[0]
                received = db.execute("SELECT * FROM collation_attempts WHERE chunk_id=? AND status='received' ORDER BY rowid DESC LIMIT 1",(chunk['id'],)).fetchone()
                cached = db.execute("SELECT * FROM collation_chunks WHERE cache_key=? AND status='done' AND id!=? ORDER BY rowid DESC LIMIT 1",(chunk['cache_key'],chunk['id'])).fetchone()
                if cached:
                    db.execute("UPDATE collation_chunks SET status='done',output_text=?,questions_json=?,reused_from=?,error='' WHERE id=?",(cached['output_text'],cached['questions_json'],cached['id'],chunk['id']))
                    event(db,self.now,batch['id'],'复用已验证片段，本批无新增模型用量',item['id'],chunk['id'])
                    return
            if not received and count >= MAX_ATTEMPTS:
                raise ValueError('本片段达到 3 次调用上限；已完成片段保留，失败片段可重试')
            holder = {'attempt':dict(received) if received else None}
            def request_node(state):
                if holder['attempt']:
                    return {'reply':json.loads(holder['attempt']['response_json'])}
                with database(root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                    if current['status']!='running' or current['owner']!=owner or root in self.shutdown_roots or root != self.service.root:
                        raise Hold()
                    attempt_id = secrets.token_hex(12)
                    budget=output_budget(model) if model.get('collation_config') else min(output_budget(model),max(256,(int(model.get('context_tokens') or 16384)-2400-3*len(batch['rules']))//2))
                    feedback=''
                    previous=db.execute("SELECT error FROM collation_attempts WHERE chunk_id=? AND status='invalid' ORDER BY rowid DESC LIMIT 3",(chunk['id'],)).fetchall()
                    if model.get('collation_feedback') and previous:
                        source=model_input_text(chunk['input_text'],model.get('collation_input_frame'),model.get('collation_glyph_mode'))
                        feedback='\n'.join(dict.fromkeys(fidelity_feedback(source,p['error']) for p in reversed(previous) if '原字校验' in p['error']))
                    messages=messages_for(item,chunk,batch['rules'],model.get('collation_config'),model.get('collation_contract'),model.get('collation_input_frame'),feedback,model.get('collation_glyph_mode'))
                    request={'model':model['model'],'messages':messages,**request_options(model),**output_options(budget)}
                    db.execute('INSERT INTO collation_attempts(id,batch_id,item_id,chunk_id,budget_id,status,started_at,request_json) VALUES(?,?,?,?,?,?,?,?)',(attempt_id,batch['id'],item['id'],chunk['id'],chunk['root_id'],'requesting',self.now(),json.dumps(request,ensure_ascii=False)))
                    cancel_event=threading.Event()
                    with self.lock:self.inflight[(Path(root),batch['id'])]=cancel_event
                    event(db,self.now,batch['id'],f'请求模型，片段字符 {chunk["start"]}–{chunk["end"]}',item['id'],chunk['id'],attempt_id)
                    if feedback: event(db,self.now,batch['id'],'重试携带具体原字错误反馈，保持原字，修订只记校记',item['id'],chunk['id'],attempt_id)
                holder['attempt']={'id':attempt_id}
                started=time.monotonic()
                try:
                    reply = self.call(messages,{**model,'_cancel_event':cancel_event},budget)
                except Exception as error:
                    reply = getattr(error,'reply',None)
                    if not getattr(error,'sent',True):
                        reply = {'text':'','usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0},'response_id':'','finish_reason':'not_sent'}
                    self._receipt(root,attempt_id,reply,round((time.monotonic()-started)*1000),'not_sent' if not getattr(error,'sent',True) else 'cancelled' if getattr(error,'cancelled',False) else 'error' if reply else 'unknown',str(error))
                    if getattr(error,'cancelled',False):raise Hold() from error
                    raise
                finally:
                    with self.lock:self.inflight.pop((Path(root),batch['id']),None)
                self._receipt(root,attempt_id,reply,round((time.monotonic()-started)*1000),'received')
                return {'reply':reply}
            def validation_node(state):
                with database(root) as db:
                    current=db.execute('SELECT status,owner FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                    if not current or current['status'] not in ('running','pausing','stopping') or current['owner']!=owner:raise Hold()
                result = validate_reply(chunk['input_text'],state['reply'],bool(model.get('collation_validation_version')),model.get('collation_input_frame'),model.get('collation_glyph_mode'))
                with database(root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE collation_chunks SET status='done',output_text=?,questions_json=?,error='' WHERE id=?",(result.text,json.dumps([q.model_dump() for q in result.questions],ensure_ascii=False),chunk['id']))
                    changed=bool(punctuation_error(chunk['input_text'],result.text))
                    db.execute("UPDATE collation_attempts SET status=? WHERE id=?",('review' if changed else 'valid',holder['attempt']['id']))
                    event(db,self.now,batch['id'],'片段文字差异待复核' if changed else '片段原字校验通过',item['id'],chunk['id'],holder['attempt']['id'])
                return {'validated':True}
            graph = StateGraph(dict)
            graph.add_node('request',request_node)
            graph.add_node('validate',validation_node)
            graph.add_edge(START,'request')
            graph.add_edge('request','validate')
            graph.add_edge('validate',END)
            try:
                graph.compile().invoke({})
                return
            except Hold:
                raise
            except Exception as error:
                if getattr(error,'fatal',False):
                    raise
                with database(root) as db:
                    if holder['attempt']:
                        db.execute("UPDATE collation_attempts SET status=CASE WHEN status='received' THEN 'invalid' ELSE status END,error=? WHERE id=?",(str(error)[:1500],holder['attempt']['id']))
                    db.execute('UPDATE collation_chunks SET error=? WHERE id=?',(str(error)[:1500],chunk['id']))
                    event(db,self.now,batch['id'],'片段调用或验证失败：'+str(error)[:800],item['id'],chunk['id'],level='warning')
                    used = db.execute('SELECT count(*) FROM collation_attempts WHERE budget_id=?',(chunk['root_id'],)).fetchone()[0]
                    # One bounded subdivision shares the original three-call budget.
                    informed_retry=model.get('collation_feedback') and getattr(error,'fidelity_error',False)
                    validation_error = not (getattr(error,'output_limit',False) or getattr(error,'quality_hold',False) or informed_retry) and (isinstance(error,(ValueError,TypeError)) or getattr(error,'context_limit',False))
                    if validation_error and not chunk['parent_id'] and used==1 and len(chunk['input_text'])>=160:
                        middle = len(chunk['input_text'])//2
                        boundary = chunk['input_text'].rfind('\n',40,middle+1)
                        if boundary>=40:
                            middle=boundary+1
                        db.execute("UPDATE collation_chunks SET status='split' WHERE id=?",(chunk['id'],))
                        for left,right in ((0,middle),(middle,len(chunk['input_text']))):
                            child={'id':secrets.token_hex(12),'start':chunk['start']+left,'end':chunk['start']+right,
                                   'input_text':chunk['input_text'][left:right],'lead':item['baseline_text'][max(0,chunk['start']+left-200):chunk['start']+left]}
                            db.execute('INSERT INTO collation_chunks(id,item_id,root_id,parent_id,start,end,input_text,lead,cache_key) VALUES(?,?,?,?,?,?,?,?,?)',
                                       (child['id'],item['id'],chunk['root_id'],chunk['id'],child['start'],child['end'],child['input_text'],child['lead'],cache_key(item,child,model,batch['rules'])))
                        event(db,self.now,batch['id'],'自动缩小为两片段，共享剩余两次调用预算',item['id'],chunk['id'])
                        return
                if getattr(error,'output_limit',False):
                    raise ValueError(str(error)+'；已停止相同参数自动重试，请先点校调试，再建立新批次') from error
                if getattr(error,'quality_hold',False):
                    raise ValueError(str(error)+'；已停止同参数自动重试，请在点校调试中调整要求后建立新批次') from error
                for _ in range(int(self.retry_delay*10)):
                    time.sleep(.1)
                    with database(root) as db:
                        current=db.execute('SELECT status FROM collation_batches WHERE id=?',(batch['id'],)).fetchone()
                    if current['status']!='running':
                        raise Hold()

    def _receipt(self, root, attempt_id, reply, elapsed, status, error=''):
        usage = reply.get('usage') if reply else None
        source,output,total = token_usage(usage)
        with database(root) as db:
            db.execute('''UPDATE collation_attempts SET status=?,response_json=?,usage_json=?,input_tokens=?,output_tokens=?,total_tokens=?,
              response_id=?,finish_reason=?,latency_ms=?,finished_at=?,error=? WHERE id=?''',
              (status,json.dumps(reply,ensure_ascii=False) if reply else '',json.dumps(usage,ensure_ascii=False) if usage is not None else None,
               source,output,total,reply.get('response_id','') if reply else '',reply.get('finish_reason','') if reply else '',elapsed,self.now(),error[:1500],attempt_id))

def register(app, connection, now, call, model_settings, public_settings, service, unit_creator):
    @app.post('/api/collation/compare')
    def compare(payload: ComparisonInput):
        return compare_text(payload.baseline,payload.content)

    def create_one(run_id, payload):
        try:
            from backend.preprocessing import UnitsInput
        except ModuleNotFoundError:
            from preprocessing import UnitsInput
        return unit_creator(run_id,UnitsInput(**payload))
    runner = Runner(service,now,call,create_one)

    def batch_row(db, batch_id):
        row = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch_id,)).fetchone()
        if not row:
            raise HTTPException(404,'找不到批量点校记录')
        return row

    def item_row(db,item_id):
        row = db.execute('SELECT * FROM collation_items WHERE id=?',(item_id,)).fetchone()
        if not row:
            raise HTTPException(404,'找不到文件点校记录')
        return row

    def summary(db,row):
        value = dict(row)
        value['model'] = json.loads(value.pop('model_json'))
        value.pop('owner',None)
        statuses = {r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM collation_items WHERE batch_id=? GROUP BY status',(row['id'],))}
        value['counts'] = {k:statuses.get(k,0) for k in ('queued','running','done','failed','stopped')}
        value['total'] = sum(statuses.values())
        value['ended'] = sum(statuses.get(k,0) for k in ('done','failed','stopped'))
        usage = dict(db.execute('''SELECT count(*) requests,sum(input_tokens) input_tokens,sum(output_tokens) output_tokens,sum(total_tokens) total_tokens,
          count(input_tokens) input_reported,count(output_tokens) output_reported,count(total_tokens) total_reported,
          sum(CASE WHEN status!='requesting' AND (input_tokens IS NULL OR output_tokens IS NULL OR total_tokens IS NULL) THEN 1 ELSE 0 END) unknown_requests,
          sum(CASE WHEN status='requesting' THEN 1 ELSE 0 END) in_flight,sum(latency_ms) latency_ms
          FROM collation_attempts WHERE batch_id=?''',(row['id'],)).fetchone())
        usage['unknown_requests'] = usage['unknown_requests'] or 0
        usage['in_flight'] = usage['in_flight'] or 0
        value['usage'] = usage
        value['reused_chunks'] = db.execute("SELECT count(*) FROM collation_chunks c JOIN collation_items i ON i.id=c.item_id WHERE i.batch_id=? AND c.reused_from!=''",(row['id'],)).fetchone()[0]
        value['needs_merge'] = db.execute("SELECT count(*) FROM collation_items WHERE batch_id=? AND merge_status='candidate'",(row['id'],)).fetchone()[0]
        value['approved'] = db.execute("SELECT count(*) FROM collation_items i JOIN research_units u ON u.id=i.unit_id WHERE i.batch_id=? AND i.merge_status='applied' AND u.status='approved'",(row['id'],)).fetchone()[0]
        value['pending_review'] = max(0,statuses.get('done',0)-value['needs_merge']-value['approved'])
        active = max(0,time.time()-row['active_since']) if row['active_since'] else 0
        value['elapsed_seconds'] = round(row['active_seconds']+active)
        value['estimated_remaining_seconds'] = round(value['elapsed_seconds']*(value['total']-value['ended'])/value['ended']) if value['ended'] and row['status']=='running' else None
        current = db.execute("SELECT id,title FROM collation_items WHERE batch_id=? AND status='running' ORDER BY seq LIMIT 1",(row['id'],)).fetchone()
        value['current'] = dict(current) if current else None
        if current:
            progress = dict(db.execute("SELECT count(*) chunks,sum(CASE WHEN status='done' THEN 1 ELSE 0 END) completed_chunks FROM collation_chunks WHERE item_id=? AND status!='split'",(current['id'],)).fetchone())
            request = db.execute("SELECT started_at FROM collation_attempts WHERE item_id=? AND status='requesting' ORDER BY rowid DESC LIMIT 1",(current['id'],)).fetchone()
            received = db.execute("SELECT 1 FROM collation_attempts WHERE item_id=? AND status='received' LIMIT 1",(current['id'],)).fetchone()
            value['current'].update(progress)
            value['current']['stage'] = '等待模型响应' if request else '验证与保存响应' if received else '片段衔接或整卷合并'
            value['current']['waiting_seconds'] = max(0,round((datetime.now(timezone.utc)-datetime.fromisoformat(request['started_at'])).total_seconds())) if request else 0
        price = value['model']
        value['estimated_cost'] = None
        value['cost_note'] = '未配置本模型单价'
        if price.get('price_input_per_million') is not None and price.get('price_output_per_million') is not None and price.get('currency') and price.get('price_date'):
            cache_missing = False
            cost = 0.0
            for attempt in db.execute("SELECT * FROM collation_attempts WHERE batch_id=? AND status!='requesting'",(row['id'],)):
                if attempt['input_tokens'] is None or attempt['output_tokens'] is None:
                    continue
                raw = json.loads(attempt['usage_json'] or '{}')
                details = raw.get('prompt_tokens_details') or raw.get('input_tokens_details') or {}
                cached = details.get('cached_tokens',0) if isinstance(details,dict) else 0
                cached = cached if isinstance(cached,int) and 0<=cached<=attempt['input_tokens'] else 0
                if cached and price.get('price_cached_per_million') is None:
                    cache_missing = True
                    continue
                cost += ((attempt['input_tokens']-cached)*price['price_input_per_million']+cached*(price.get('price_cached_per_million') or 0)+attempt['output_tokens']*price['price_output_per_million'])/1000000
            if not cache_missing:
                value['estimated_cost'] = round(cost,6)
                value['cost_note'] = '按用户设置单价估算；非账单'+('，不含未计量请求' if usage['unknown_requests'] else '')
            else:
                value['cost_note'] = '存在缓存用量，缓存单价未配置'
        return value

    def scopes(db, project_id, run_id):
        project = db.execute('SELECT * FROM projects WHERE id=? AND deleted=0',(project_id,)).fetchone()
        if not project or project['project_type']!='collation' or project['archived']:
            raise HTTPException(400,'请选择未归档的古籍点校项目')
        run = db.execute('SELECT * FROM preprocess_runs WHERE id=? AND project_id=?',(run_id,project_id)).fetchone()
        if not run or run['status']!='accepted' or not research.attached(db,project_id,run['material_id']):
            raise HTTPException(400,'请先完成当前项目的底本拆分清洗')
        result = json.loads(run['result_json'])
        recipe = Recipe.model_validate_json(run['recipe_json'])
        texts = result.get('texts') or split_texts(run['source_text'],result['nodes'],recipe)[0]
        units = db.execute('SELECT * FROM research_units WHERE project_id=? AND material_id=? ORDER BY updated_at DESC,rowid DESC',(project_id,run['material_id'])).fetchall()
        related = {}
        source_hash = digest(run['source_text'])
        for u in units:
            provenance = json.loads(u['preprocess_json'])
            if provenance.get('source_hash')==source_hash:
                key = (provenance.get('source_start'),provenance.get('source_end'),digest(research.baseline_for(db,u)))
                related.setdefault(key,u)
        rows=[]
        for text in texts:
            baseline = formatted(prepared(run['source_text'],result['edits'],text['start'],text['end']),recipe)
            base_hash = digest(baseline)
            unit = related.get((text['start'],text['end'],base_hash))
            processed = db.execute("SELECT 1 FROM collation_items i JOIN collation_batches b ON b.id=i.batch_id WHERE b.project_id=? AND i.material_id=? AND i.baseline_hash=? AND i.source_start=? AND i.source_end=? AND i.status='done' LIMIT 1",(project_id,run['material_id'],base_hash,text['start'],text['end'])).fetchone() is not None
            manual = bool(unit and (unit['revision']>1 or unit['content']!=baseline or unit['status']=='approved'))
            rows.append({'id':text['id'],'title':text['title'],'kind':text.get('kind','volume'),'chars':len(baseline),'processed':processed,'manual_modified':manual,
                         'unit_id':unit['id'] if unit else '', 'input_revision':unit['revision'] if unit else 0,'input_content_hash':digest(unit['content']) if unit else base_hash,
                         'baseline_text':baseline,'baseline_hash':base_hash,'original_text':run['source_text'][text['start']:text['end']], 'start':text['start'],'end':text['end']})
        return run,project,rows

    @app.get('/api/units/{unit_id}/baseline')
    def baseline(unit_id: str):
        with connection() as db:
            unit = db.execute('SELECT * FROM research_units WHERE id=?',(unit_id,)).fetchone()
            if not unit:
                raise HTTPException(404,'找不到本卷稿件')
            return {'title':unit['title'],'baseline':research.baseline_for(db,unit),'original':unit['original_text'],'source':json.loads(unit['preprocess_json'])}

    @app.get('/api/units/{unit_id}/source-issues')
    def source_issues(unit_id: str, offset: int=0):
        with connection() as db:
            unit = db.execute('SELECT * FROM research_units WHERE id=?',(unit_id,)).fetchone()
            if not unit:
                raise HTTPException(404,'找不到本卷稿件')
            provenance = json.loads(unit['preprocess_json'])
            run = db.execute('SELECT result_json FROM preprocess_runs WHERE id=?',(provenance.get('run_id',''),)).fetchone()
            issues = [e for e in json.loads(run['result_json'])['edits'] if (e['status']=='pending' or e.get('needs_review')) and provenance['source_start']<=e['start']<provenance['source_end']] if run else []
            offset=max(0,offset)
            return {'total':len(issues),'items':issues[offset:offset+80]}

    @app.get('/api/projects/{project_id}/collation/scope')
    def scope(project_id: str, run_id: str):
        with connection() as db:
            _,_,rows = scopes(db,project_id,run_id)
            return [{k:r[k] for k in ('id','title','kind','chars','processed','manual_modified','unit_id')} for r in rows]

    @app.post('/api/projects/{project_id}/collation/batches')
    def create_batch(project_id: str, payload: BatchInput):
        if not public_settings()['configured']:
            raise HTTPException(400,'请先配置有效的模型连接')
        batch_id = digest({'project':project_id,'request_id':payload.request_id})[:24]
        with connection() as db:
            previous = db.execute('SELECT * FROM collation_batches WHERE id=?',(batch_id,)).fetchone()
            if previous:
                if previous['run_id'] != payload.run_id:
                    raise HTTPException(409,'重复请求编号对应了不同拆分，请重新创建批次')
                return summary(db,previous)
            run,project,rows = scopes(db,project_id,payload.run_id)
        chosen = set(payload.text_ids) if payload.text_ids is not None else {r['id'] for r in rows if not r['processed'] and not r['manual_modified']}
        if not chosen:
            raise HTTPException(400,'没有选中未点校文件；可在文件列表中选择需要重跑的文件')
        if not chosen.issubset({r['id'] for r in rows}):
            raise HTTPException(400,'包含不属于本次拆分的文件')
        model = model_settings()
        with connection() as db:
            profile=read_profile(db,project_id)
        model['collation_config']=profile['config']
        model['collation_config_revision']=profile['revision']
        model['collation_validation_version']='punctuation-progress-v1'
        model['collation_contract']=CONTRACT
        model['collation_input_frame']='edge-whitespace-v1'
        model['collation_feedback']='fidelity-v1'
        model['collation_glyph_mode']='pua-placeholder-v1'
        selected = [r for r in rows if r['id'] in chosen]
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM collation_batches WHERE id=?',(batch_id,)).fetchone()
            if previous:
                if previous['run_id'] != payload.run_id:
                    raise HTTPException(409,'重复请求编号对应了不同拆分，请重新创建批次')
                return summary(db,previous)
            if not db.execute('SELECT 1 FROM preprocess_runs WHERE id=? AND status=\'accepted\'',(payload.run_id,)).fetchone() or not research.attached(db,project_id,run['material_id']):
                raise HTTPException(409,'拆分来源已变化，请重新打开卷文本')
            existing = db.execute("SELECT id FROM collation_batches WHERE project_id=? AND run_id=? AND status IN ('queued','running','pausing','stopping') LIMIT 1",(project_id,payload.run_id)).fetchone()
            if existing:
                raise HTTPException(409,'本次拆分已有活动批次，请先查看该批次')
            if db.execute("SELECT 1 FROM collation_debug_runs WHERE status='running' LIMIT 1").fetchone():
                raise HTTPException(409,'请等待当前调试请求完成，再开始批量点校')
            if Path(service.root) in runner.shutdown_roots:
                raise HTTPException(409,'软件正在退出，请重新打开后执行')
            db.execute('INSERT INTO collation_batches(id,project_id,run_id,title,model_json,rules,prompt_version,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (batch_id,project_id,payload.run_id,run['material_id']+' · '+str(len(selected))+' 份文件',json.dumps(model,ensure_ascii=False),project['rules'],PROMPT_VERSION,'queued',now()))
            for seq,row in enumerate(selected,1):
                item_id=digest({'batch':batch_id,'text':row['id']})[:24]
                db.execute('''INSERT INTO collation_items(id,batch_id,seq,text_id,title,material_id,baseline_text,baseline_hash,original_text,source_start,source_end,unit_id,input_revision,input_content_hash,protect_manual)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(item_id,batch_id,seq,row['id'],row['title'],run['material_id'],row['baseline_text'],row['baseline_hash'],row['original_text'],row['start'],row['end'],row['unit_id'],row['input_revision'],row['input_content_hash'],int(row['manual_modified'])))
                for start,end,source,lead in plan(row['baseline_text'],chunk_limit(model,project['rules'])):
                    chunk={'id':secrets.token_hex(12),'start':start,'end':end,'input_text':source,'lead':lead}
                    db.execute('INSERT INTO collation_chunks(id,item_id,root_id,start,end,input_text,lead,cache_key) VALUES(?,?,?,?,?,?,?,?)',
                               (chunk['id'],item_id,chunk['id'],start,end,source,lead,cache_key(row,chunk,model,project['rules'])))
            event(db,now,batch_id,f'已冻结 {len(selected)} 份文件，排队逐文件执行')
            answer=summary(db,batch_row(db,batch_id))
        runner.kick()
        return answer

    @app.get('/api/projects/{project_id}/collation/batches')
    def list_batches(project_id: str):
        with connection() as db:
            recover(db,now)
            return [summary(db,r) for r in db.execute('SELECT * FROM collation_batches WHERE project_id=? ORDER BY rowid DESC LIMIT 100',(project_id,)).fetchall()]

    @app.get('/api/collation/batches/{batch_id}')
    def get_batch(batch_id: str):
        with connection() as db:
            recover(db,now)
            return summary(db,batch_row(db,batch_id))

    @app.get('/api/collation/batches/{batch_id}/items')
    def items(batch_id: str,offset: int=0,status: str=''):
        with connection() as db:
            batch_row(db,batch_id)
            values=[]
            for item in db.execute('SELECT * FROM collation_items WHERE batch_id=? AND (?=\'\' OR status=?) ORDER BY seq',(batch_id,status,status)).fetchall():
                value={k:item[k] for k in ('id','seq','title','status','merge_status','unit_id','error','finished_at')}
                value['chars']=len(item['baseline_text'])
                value['has_text_changes']=item['status']=='done' and bool(punctuation_error(item['baseline_text'],item['result_text']))
                value.update(dict(db.execute("SELECT count(*) chunks,sum(CASE WHEN status='done' THEN 1 ELSE 0 END) completed_chunks,sum(CASE WHEN status='done' THEN end-start ELSE 0 END) completed_chars FROM collation_chunks WHERE item_id=? AND status!='split'",(item['id'],)).fetchone()))
                value.update(dict(db.execute('SELECT count(*) requests,sum(input_tokens) input_tokens,sum(output_tokens) output_tokens,sum(total_tokens) total_tokens,sum(latency_ms) latency_ms FROM collation_attempts WHERE item_id=?',(item['id'],)).fetchone()))
                value['pending_questions']=len(json.loads(item['questions_json']))
                values.append(value)
            start=max(0,offset)
            return {'total':len(values),'items':values[start:start+80]}

    @app.get('/api/collation/batches/{batch_id}/events')
    def events(batch_id: str, after: int=0):
        with connection() as db:
            batch_row(db,batch_id)
            if after:
                rows=db.execute('SELECT * FROM collation_events WHERE batch_id=? AND id>? ORDER BY id LIMIT 300',(batch_id,after)).fetchall()
            else:
                rows=list(reversed(db.execute('SELECT * FROM collation_events WHERE batch_id=? ORDER BY id DESC LIMIT 100',(batch_id,)).fetchall()))
            return [dict(r) for r in rows]

    @app.get('/api/collation/items/{item_id}/result')
    def item_result(item_id: str):
        with connection() as db:
            item=item_row(db,item_id)
            current_unit=db.execute('SELECT content,revision FROM research_units WHERE id=?',(item['unit_id'],)).fetchone()
            return {'current_content':current_unit['content'] if current_unit else '', 'current_revision':current_unit['revision'] if current_unit else 0, 'id':item_id,'title':item['title'],'status':item['status'],'merge_status':item['merge_status'],'unit_id':item['unit_id'],
                    'baseline':item['baseline_text'],'original':item['original_text'],'content':item['result_text'],'questions':json.loads(item['questions_json'])}

    @app.get('/api/collation/items/{item_id}/attempts')
    def attempts(item_id: str,offset: int=0):
        with connection() as db:
            item_row(db,item_id)
            rows=db.execute('SELECT * FROM collation_attempts WHERE item_id=? ORDER BY rowid',(item_id,)).fetchall()
            return {'total':len(rows),'items':[{'id':r['id'],'chunk_id':r['chunk_id'],'status':r['status'],'reviewed_from_history':bool(db.execute("SELECT 1 FROM collation_events WHERE attempt_id=? AND message LIKE '复用此前完整响应%' LIMIT 1",(r['id'],)).fetchone()),'started_at':r['started_at'],'finished_at':r['finished_at'],'error':r['error'],
                    'input_tokens':r['input_tokens'],'output_tokens':r['output_tokens'],'total_tokens':r['total_tokens'],'latency_ms':r['latency_ms'],'response_id':r['response_id'],
                    'finish_reason':r['finish_reason'],'request':json.loads(r['request_json']) if r['request_json'] else None,'usage':json.loads(r['usage_json']) if r['usage_json'] else None} for r in rows[max(0,offset):max(0,offset)+80]]}

    @app.post('/api/collation/batches/{batch_id}/action')
    def action(batch_id: str,payload: BatchAction):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            recover(db,now)
            row=batch_row(db,batch_id)
            if payload.action in {'resume','retry'} and db.execute("SELECT 1 FROM collation_debug_runs WHERE status='running' LIMIT 1").fetchone():
                raise HTTPException(409,'请等待调试请求完成，再继续批次')
            if payload.action=='pause':
                if row['status']=='queued':
                    db.execute("UPDATE collation_batches SET status='paused' WHERE id=?",(batch_id,))
                elif row['status']=='running':
                    db.execute("UPDATE collation_batches SET status='pausing' WHERE id=?",(batch_id,))
                else:
                    raise HTTPException(409,'本批次当前不能暂停')
            elif payload.action=='stop':
                if row['status'] in {'running','pausing'}:
                    db.execute("UPDATE collation_batches SET status='stopping' WHERE id=?",(batch_id,))
                elif row['status'] in {'queued','paused','interrupted'}:
                    runner._end(db,row,'stopped')
                else:
                    raise HTTPException(409,'本批次当前不能停止')
            elif payload.action=='resume':
                if row['status'] not in {'paused','interrupted'}:
                    raise HTTPException(409,'只有暂停或中断批次可以继续')
                db.execute("UPDATE collation_batches SET status='queued',error='',finished_at='' WHERE id=?",(batch_id,))
            else:
                if row['status'] not in {'completed_with_errors','completed','paused','interrupted'}:
                    raise HTTPException(409,'请等待批次结束或暂停后重试失败文件')
                failed=db.execute("SELECT id FROM collation_items WHERE batch_id=? AND status='failed'",(batch_id,)).fetchall()
                if not failed:
                    raise HTTPException(400,'没有失败文件')
                # A new retry budget retains every historical request and its usage.
                for item in failed:
                    budgets = {}
                    for c in db.execute("SELECT * FROM collation_chunks WHERE item_id=? AND status='queued'",(item['id'],)).fetchall():
                        db.execute("UPDATE collation_chunks SET root_id=?,parent_id='',error='' WHERE id=?",(budgets.setdefault(c['root_id'],secrets.token_hex(12)),c['id']))
                    db.execute("UPDATE collation_items SET status='queued',error='',finished_at='' WHERE id=?",(item['id'],))
                db.execute("UPDATE collation_batches SET status='queued',error='',finished_at='' WHERE id=?",(batch_id,))
            event(db,now,batch_id,'用户操作：'+payload.action)
            answer=summary(db,batch_row(db,batch_id))
        if payload.action in {'resume','retry'}:
            runner.kick()
        elif payload.action=='stop':
            runner.cancel_batch(service.root,batch_id)
        return answer

    @app.post('/api/collation/items/{item_id}/adopt')
    def adopt(item_id: str,payload: AdoptInput):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            item=item_row(db,item_id)
            unit=db.execute('SELECT * FROM research_units WHERE id=?',(item['unit_id'],)).fetchone()
            if not unit or item['status']!='done' or item['merge_status']!='candidate':
                raise HTTPException(400,'没有待采用的完整候选稿')
            if item['protect_manual'] or unit['revision']!=payload.expected_revision or unit['revision']!=(item['input_revision'] or 1) or digest(unit['content'])!=item['input_content_hash']:
                raise HTTPException(409,'稿件已经改变，请查看候选内容后手工合并；当前稿件已保留')
            notes=json.loads(unit['notes_json'])+runner.result_notes(item_id,json.loads(item['questions_json']))
            notes.append({'id':item_id+'-ai','kind':'ai','location':unit['location_label'],'base_reading':'','witness_id':'','witness_reading':'',
                          'decision':'采用批量候选结果','evidence':'待人工对照底文复核','status':'pending'})
            if len(notes)>20000:
                raise HTTPException(400,'校记数量超过上限，请导出完整报告后整理')
            runner.write_unit(db,unit,item['result_text'],notes)
            db.execute("UPDATE collation_items SET merge_status='applied' WHERE id=?",(item_id,))
            return research.unit_info(db.execute('SELECT * FROM research_units WHERE id=?',(unit['id'],)).fetchone(),True)

    @app.get('/api/collation/batches/{batch_id}/export')
    def export(batch_id: str,format: str='zip'):
        with connection() as db:
            row=batch_row(db,batch_id)
            report=summary(db,row)
            report['files']=[{'id':i['id'],'title':i['title'],'status':i['status'],'merge_status':i['merge_status'],'error':i['error'],'unit_id':i['unit_id'],
                              'source_start':i['source_start'],'source_end':i['source_end'],'baseline_hash':i['baseline_hash'],'questions':json.loads(i['questions_json'])} for i in db.execute('SELECT * FROM collation_items WHERE batch_id=? ORDER BY seq',(batch_id,))]
            report['events']=[dict(e) for e in db.execute('SELECT * FROM collation_events WHERE batch_id=? ORDER BY id',(batch_id,))]
            report['attempts']=[{k:a[k] for k in ('id','item_id','chunk_id','status','input_tokens','output_tokens','total_tokens','latency_ms','started_at','finished_at','error','response_id','usage_json','request_json')} for a in db.execute('SELECT * FROM collation_attempts WHERE batch_id=? ORDER BY rowid',(batch_id,))]
            content=json.dumps(report,ensure_ascii=False,indent=2)
            if format=='json':
                return {'filename':'批量点校-'+batch_id+'.json','content':content}
            output=io.BytesIO()
            used=set()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('_批次清单.json',content)
                archive.writestr('_失败与未完成文件.txt','\n'.join(i['title']+'：'+i['status']+' '+i['error'] for i in report['files'] if i['status']!='done'))
                for item in db.execute('SELECT * FROM collation_items WHERE batch_id=? ORDER BY seq',(batch_id,)):
                    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',item['title']).strip(' .')[:120] or '卷文本'
                    if name.lower() in used:
                        name+= '-'+str(item['seq'])
                    used.add(name.lower())
                    if item['status']=='done':
                        archive.writestr(('候选待合并/' if item['merge_status']=='candidate' else '待复核稿/')+name+'.txt',item['result_text'])
                    elif db.execute("SELECT 1 FROM collation_chunks WHERE item_id=? AND status='done' LIMIT 1",(item['id'],)).fetchone():
                        chunks=[{'start':c['start'],'end':c['end'],'status':c['status'],'text':c['output_text'] if c['status']=='done' else ''} for c in db.execute("SELECT * FROM collation_chunks WHERE item_id=? AND status!='split' ORDER BY start",(item['id'],))]
                        archive.writestr('不完整片段/'+name+'.json',json.dumps(chunks,ensure_ascii=False,indent=2))
            return Response(output.getvalue(),media_type='application/octet-stream')

    @app.post('/api/collation/shutdown')
    def shutdown():
        root=Path(service.root)
        runner.shutdown_roots.add(root)
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE collation_batches SET status='pausing' WHERE status='running'")
            db.execute("UPDATE collation_batches SET status='paused' WHERE status='queued'")
            draining=db.execute("SELECT 1 FROM collation_batches WHERE status IN ('pausing','stopping') LIMIT 1").fetchone() is not None or db.execute("SELECT 1 FROM collation_debug_runs WHERE status='running' LIMIT 1").fetchone() is not None
        debugger=getattr(service,'debug_controller',None)
        return {'draining':draining or bool(debugger and debugger.active(root))}

    @app.on_event('startup')
    async def start_queue():
        runner.kick()

    return runner


def activity(db, project_id):
    row = db.execute("SELECT id,status FROM collation_batches WHERE project_id=? AND status IN ('queued','running','pausing','paused','interrupted','stopping') ORDER BY rowid DESC LIMIT 1",(project_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    value['total'] = db.execute('SELECT count(*) FROM collation_items WHERE batch_id=?',(row['id'],)).fetchone()[0]
    value['ended'] = db.execute("SELECT count(*) FROM collation_items WHERE batch_id=? AND status IN ('done','failed','stopped')",(row['id'],)).fetchone()[0]
    return value
