"""Opt-in CPU reranking. Public weights are downloaded only by prepare()."""
import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock

MODEL='BAAI/bge-reranker-v2-m3-int8'
_lock=RLock()
_models={}


class ModelNotReady(Exception):pass


def cache_directory(root):
    return Path(os.environ.get('SHIXUE_KB_RERANK_CACHE',str(root/'rerank-models'))).resolve()


def status(root):
    cache=cache_directory(root)
    try:
        marker=json.loads((cache/'ready.json').read_text(encoding='utf-8'))
        directory=(cache/marker['directory']).resolve()
        if cache not in directory.parents or marker['model']!=MODEL:raise ValueError()
        if not marker['files'] or any(not (directory/file['name']).is_file() for file in marker['files']):raise ValueError()
        return {'ready':True,'model':MODEL,'model_key':marker['model_key'],'download_megabytes':570}
    except (OSError,ValueError,KeyError,TypeError):
        return {'ready':False,'model':MODEL,'model_key':None,'download_megabytes':570}


def _fingerprint(directory):
    files=[directory/'onnx/model_int8.onnx']+[directory/name for name in
           ('tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json') if (directory/name).is_file()]
    if not all(file.is_file() for file in files):raise ModelNotReady()
    hashes=[]
    for file in files:
        with file.open('rb') as stream:hashes.append({'name':file.relative_to(directory).as_posix(),'sha256':hashlib.file_digest(stream,'sha256').hexdigest()})
    identity=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    return MODEL+'@'+identity,hashes


def _configure(model):
    from tokenizers import Tokenizer
    tokenizer=model.model.tokenizer
    raw=Tokenizer.from_str(tokenizer.to_str());raw.no_truncation();raw.no_padding()
    limit=min(512,(tokenizer.truncation or {}).get('max_length',512))
    tokenizer.enable_truncation(max_length=limit,strategy='only_second')
    return raw,limit


def prepare(root,check=lambda:None,progress=lambda *args:None):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    cache=cache_directory(root);cache.mkdir(parents=True,exist_ok=True)
    check();progress('download_model',5)
    # Preparation is an explicit action; search always uses a specific local directory.
    model=TextCrossEncoder(model_name=MODEL,cache_dir=str(cache),threads=2,providers=['CPUExecutionProvider'],cuda=False)
    check();progress('verify_model',85)
    directory=Path(model.model._model_dir).resolve()
    if cache not in directory.parents:raise ValueError('模型目录不在指定缓存内')
    key,files=_fingerprint(directory)
    raw,limit=_configure(model)
    check()
    marker={'model':MODEL,'model_key':key,'directory':directory.relative_to(cache).as_posix(),'files':files}
    target=cache/'ready.json';temporary=cache/('ready-'+r_uid()+'.tmp')
    try:temporary.write_text(json.dumps(marker,ensure_ascii=False),encoding='utf-8');temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    with _lock:_models[str(cache)]=(model,key,raw,limit)
    return {'model':MODEL,'model_key':key,'ready':True}


def r_uid():
    from uuid import uuid4
    return uuid4().hex


def _load(root):
    cache=cache_directory(root)
    if not status(root)['ready']:raise ModelNotReady()
    marker=json.loads((cache/'ready.json').read_text(encoding='utf-8'))
    key=str(cache)
    if key in _models and _models[key][1]==marker['model_key']:return _models[key]
    directory=(cache/marker['directory']).resolve()
    identity,files=_fingerprint(directory)
    if identity!=marker['model_key'] or files!=marker['files']:raise ModelNotReady()
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    model=TextCrossEncoder(model_name=MODEL,cache_dir=str(cache),specific_model_path=str(directory),
                          local_files_only=True,threads=2,providers=['CPUExecutionProvider'],cuda=False)
    raw,limit=_configure(model)
    _models[key]=(model,identity,raw,limit)
    return _models[key]


def score(root,query,texts):
    with _lock:
        model,key,tokenizer,limit=_load(root)
        if len(tokenizer.encode(query).ids)>limit-32:raise ValueError('query_budget')
        lengths=[len(tokens.ids) for tokens in tokenizer.encode_batch([(query,text) for text in texts])]
        values=[float(value) for value in model.rerank(query,texts,batch_size=2)]
    if len(values)!=len(texts) or not all(math.isfinite(value) for value in values):raise ValueError('invalid_scores')
    return values,{'model':MODEL,'model_key':key,'token_limit':limit,'truncated_pairs':sum(length>limit for length in lengths)}


def apply(root,query,items,options,mode):
    detail={'enabled':options['rerank_enabled'],'candidate_count':len(items),'model':MODEL,
            'min_score':options['rerank_min_score'],'outcome':'disabled','scores':[]}
    if not options['rerank_enabled']:return items,detail
    if mode=='exact':detail['outcome']='exact_bypass';return items,detail
    if not items:detail['outcome']='no_candidates';return items,detail
    candidates=items[:options['rerank_candidates']]
    detail['scored_count']=len(candidates);detail['discarded_by_cap']=len(items)-len(candidates)
    texts=[item.get('title','')[:120]+'\n'+item.get('section_path','')[:120]+'\n'+item['body'] for item in candidates]
    try:
        values,metadata=score(root,query,texts)
        if len(values)!=len(candidates) or not all(math.isfinite(value) for value in values):raise ValueError('invalid_scores')
    except ModelNotReady:
        detail['outcome']='model_not_ready';return items,detail
    except Exception:
        # Keep the complete original order; never disclose provider/file error strings.
        detail['outcome']='model_error';return items,detail
    detail.update(metadata)
    minimum=options['rerank_min_score']
    scored=[{**item,'rerank_score':value,'rrf_rank':item['rank']} for item,value in zip(candidates,values)]
    detail['scores']=[{'block_id':str(item['block_id']),'score':item['rerank_score'],'kept':minimum is None or item['rerank_score']>=minimum} for item in scored]
    ordered=sorted((item for item in scored if minimum is None or item['rerank_score']>=minimum),key=lambda item:(-item['rerank_score'],item['rrf_rank']))
    for rank,item in enumerate(ordered,1):item['rank']=rank
    detail.update(outcome='completed' if ordered else 'below_threshold',returned=len(ordered),discarded_by_threshold=len(scored)-len(ordered))
    return ordered,detail
