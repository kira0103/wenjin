"""Bounded ingestion diagnostics. Never serialize SQL parameters or document bodies."""
import errno
import logging
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException

logger=logging.getLogger('wenjin.ingestion')
GENERIC='处理失败，请检查原件、模型文件或数据库状态后重试'
EMPTY='清洗和切分后没有可索引文本，原有索引保持不变'
CATEGORIES={'unknown':'原因未记录','empty_text':'没有可索引文本','resource':'资源不足','model':'模型输入异常','file':'文件不可用','database':'数据库操作异常','timeout':'处理超时','other':'其他已记录错误'}
STAGES={'starting':'准备录文与模型','reading_source':'读取原文','parse':'解析原件','publish':'保存录文','chunking':'清洗与切分','semantic_chunking':'语义切分','embedding':'生成向量','writing_index':'写入索引','queued':'等待执行','complete':'加工完成'}
ADVICE={'unknown':'旧任务没有保存具体异常。请先核对原文及任务配置，再决定是否小范围重试。','empty_text':'查看原文和清洗预览，确认有可读取文字；调整清洗或切分策略后再处理。','resource':'检查执行设备的内存或显存使用，降低单批计算规模或释放资源后再重试。','model':'核对任务冻结的向量模型、维度和输入约束；不要仅修改全局默认值。','file':'检查原件或模型文件是否存在、完整且可读取。','database':'根据诊断编号检查数据库连接、容量与相关约束，再重试。','timeout':'检查相应服务的响应和负载，确认恢复后再重试。','other':'结合已记录原因、失败阶段和诊断编号检查处理服务。'}

def redact(value):
    value=str(value)
    value=re.sub(r'(?i)(?:postgres(?:ql)?|https?)://[^\s]+','[连接地址已隐藏]',value)
    value=re.sub(r'(?i)(bearer\s+)[\w.\-]+',r'\1[已隐藏]',value)
    value=re.sub(r'''(?ix)((?:["']?(?:api[_-]?key|password|token|secret)["']?)\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',r'\1[已隐藏]',value)
    value=re.sub(r'[A-Za-z]:[\\/][^\s]+','[本机路径已隐藏]',value)
    return value[:500]

def legacy(error,stage=''):
    category='empty_text' if error==EMPTY else 'unknown' if not error or error==GENERIC else 'other'
    return {'category':category,'code':'EMPTY_INDEX_TEXT' if category=='empty_text' else 'LEGACY_UNKNOWN' if category=='unknown' else 'LEGACY_RECORDED',
            'summary':'具体原因未记录' if category=='unknown' else redact(error),'stage':stage,'diagnostic_id':None,'recorded':category!='unknown','advice':ADVICE[category]}

def capture(error,stage='',attempt=None):
    chain=[];current=error;seen=set()
    while current is not None and id(current) not in seen and len(chain)<6:
        seen.add(id(current));chain.append(current);current=current.__cause__ or current.__context__
    sqlstate=next((getattr(getattr(e,'orig',e),'sqlstate',None) for e in chain if getattr(getattr(e,'orig',e),'sqlstate',None)),None)
    names=[type(e).__name__ for e in chain]
    primary=' '.join(str(getattr(getattr(getattr(e,'orig',e),'diag',None),'message_primary','') or '') for e in chain).lower()
    category,code,summary='unknown','UNEXPECTED_ERROR','具体原因未确定（'+type(error).__name__+'）'
    if isinstance(error,HTTPException):
        public=error.detail if isinstance(error.detail,str) else '处理请求未完成'
        known=legacy(public,stage);category=known['category'];summary=known['summary'];code='HTTP_'+str(error.status_code)
    if any(isinstance(e,MemoryError) or 'OutOfMemory' in type(e).__name__ for e in chain) or any(re.search(r'out of memory|bad_alloc|failed to allocate memory',str(e),re.I) for e in chain):
        category,code,summary='resource','MEMORY_EXHAUSTED','计算所需内存或显存不足'
    elif any(isinstance(e,OSError) and e.errno==errno.ENOSPC for e in chain):category,code,summary='resource','DISK_FULL','可用磁盘空间不足'
    elif sqlstate=='57014' and 'statement timeout' in primary:category,code,summary='timeout','DATABASE_STATEMENT_TIMEOUT','数据库写入执行超时' if stage=='writing_index' else '数据库语句执行超时'
    elif sqlstate=='55P03' and 'lock timeout' in primary:category,code,summary='timeout','DATABASE_LOCK_TIMEOUT','等待数据库锁超时'
    elif sqlstate=='57014' and 'user request' in primary:category,code,summary='database','DATABASE_QUERY_CANCELLED','数据库执行被取消'
    elif sqlstate or any(type(e).__module__.startswith(('sqlalchemy','psycopg')) for e in chain):category,code,summary='database','DATABASE_'+str(sqlstate or 'ERROR'),'数据库操作未完成'+('（SQLSTATE '+sqlstate+'）' if sqlstate else '')
    elif any(isinstance(e,FileNotFoundError) for e in chain):category,code,summary='file','FILE_NOT_FOUND','处理所需的原件或模型文件不存在'
    elif any(isinstance(e,PermissionError) for e in chain):category,code,summary='file','FILE_ACCESS_DENIED','处理文件时访问被拒绝'
    elif any(isinstance(e,TimeoutError) or type(e).__name__ in {'ReadTimeout','ConnectTimeout','PoolTimeout'} for e in chain):category,code,summary='timeout','PROCESSING_TIMEOUT','处理或服务请求超时'
    elif any(re.search(r'Got invalid dimensions|invalid input shape',str(e),re.I) for e in chain):category,code,summary='model','MODEL_INPUT_SHAPE','模型输入形状或维度不符合要求'
    identifier=uuid4().hex[:16]
    frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)[-8:]]
    result={'diagnostic_id':identifier,'category':category,'code':code,'summary':summary,'stage':stage,'recorded':category!='unknown',
            'exception_types':names,'sqlstate':sqlstate,'frames':frames,'attempt':attempt,'failed_at':datetime.now(timezone.utc).isoformat(),'advice':ADVICE[category] if category!='unknown' else '已保存异常类型与发生位置，请用诊断编号核对服务日志后再重试。'}
    if code=='DATABASE_STATEMENT_TIMEOUT':result['advice']='数据库语句超过本阶段的执行时限。检查写入规模、索引维护与数据库负载；不要仅更换向量模型。'
    elif code=='DATABASE_LOCK_TIMEOUT':result['advice']='数据库等待其他事务释放锁超时。核对并发写入、维护任务与持锁时间后再重试。'
    logger.error('diagnostic_id=%s stage=%s code=%s types=%s sqlstate=%s frames=%s',identifier,stage,code,','.join(names),sqlstate,frames)
    return result

def public_config(payload):
    """Whitelist display fields, never include provider credentials or input text."""
    embedding=payload.get('embedding') or {}
    config=payload.get('config') or (payload.get('processing') or {}).get('chunking') or {}
    return {'embedding':{k:embedding.get(k) for k in ('provider','model','dimensions','revision','batch_size','max_length','device','document_prefix','query_prefix','send_dimensions')},
            'chunking':{k:config.get(k) for k in ('mode','profile','size','overlap','parent_size','semantic_threshold','separator','cleaning')},
            'ocr_mode':(payload.get('processing') or {}).get('ocr_mode'),
            'policy_version':payload.get('policy_version'),'revision_id':payload.get('revision_id') or payload.get('expected_revision')}

def diagnostic(job):
    stored=(job.get('result') or {}).get('diagnostic')
    if not stored:
        history=(job.get('result') or {}).get('diagnostic_history') or []
        if history:stored=history[-1]
    return stored if isinstance(stored,dict) else legacy(job.get('error',''),job.get('stage',''))
