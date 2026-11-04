"""GPU / CPU inference adapters. Download public models; document inference stays local."""
import hashlib
import json
import threading
import os
import logging
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from .embedding_config import EmbeddingOptions,LEGACY_MODEL,space,current

MODEL = LEGACY_MODEL
logger=logging.getLogger(__name__)
_lock=threading.RLock()
# ONNX sessions are thread-safe and release the GIL while the GPU runs. Two
# concurrent calls let one thread tokenize the next batch while another batch is
# already executing on the GPU, without duplicating the model in VRAM.
_inference_slots=threading.Semaphore(max(1,int(os.environ.get('SHIXUE_EMBED_INFLIGHT','2'))))
_models={}
_accelerated={}
_ocr=None
_embedding_context=ContextVar('embedding_context',default=None)
MODEL_FILES={'tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json'}


@contextmanager
def embedding_context(config,purpose='document'):
    token=_embedding_context.set((EmbeddingOptions.model_validate(config).model_dump(mode='json'),purpose))
    try:yield
    finally:_embedding_context.reset(token)


def configuration(root):
    return _embedding_context.get() or (current(root),'document')


def accelerated_model(root,model):
    """Path of a validated half-precision copy prepared by scripts/optimize_embedding_fp16.py.

    The copy is an execution detail, not a different vector space: it carries the
    identity of the original weights so existing indexes stay searchable. Any
    missing, unvalidated or unreadable copy is ignored and the original model is used.
    """
    if os.environ.get('SHIXUE_EMBEDDING_FP16','1')=='0':return None
    from .embedding_cache import cache_directory
    folder=cache_directory(root)/'fp16'/model.replace('/','--')
    resolved=str(folder)
    if resolved in _accelerated:return _accelerated[resolved]
    found=None
    try:
        info=json.loads((folder/'meta.json').read_text(encoding='utf-8'))
        if info.get('model')==model and info.get('validated') and (folder/'model.onnx').is_file():found=folder
    except (OSError,ValueError,TypeError):
        found=None
    _accelerated[resolved]=found
    return found


def model_identity(model,directory):
    """Stable vector-model fingerprint; accelerated copies keep the original one."""
    directory=Path(directory).resolve()
    marker=directory/'identity.txt'
    if marker.is_file():
        value=marker.read_text(encoding='utf-8').strip()
        if value.startswith(model+'@') and len(value)>len(model)+1:return value
    files=sorted(file for file in directory.rglob('*') if file.is_file() and (file.suffix=='.onnx' or file.name in MODEL_FILES))
    if not any(file.suffix=='.onnx' for file in files):
        raise RuntimeError('嵌入模型文件未就绪')
    digest=hashlib.sha256();fingerprints=set()
    for file in files:
        with file.open('rb') as stream:
            fingerprints.add(hashlib.file_digest(stream,'sha256').digest())
    for fingerprint in sorted(fingerprints):digest.update(fingerprint)
    return model+'@'+digest.hexdigest()


def embedding_model(root,*,offline=False,config=None):
    with _lock:
        # Preparing weights can run before a library exists. Inference supplies
        # its saved profile explicitly; preparation can also use the job context.
        config=config or (_embedding_context.get() or (EmbeddingOptions().model_dump(mode='json'),))[0]
        if config['provider']!='local':raise RuntimeError('外部接口没有本地模型缓存')
        from .embedding_cache import cache_directory
        cache=str(cache_directory(root))
        accelerated=accelerated_model(root,config['model'])
        cache_key=(cache,config['model'],str(accelerated) if accelerated else '')
        if cache_key not in _models:
            # Resolve the inference runtime before fastembed imports onnxruntime,
            # otherwise the CPU-only wheel can win and pin the whole process.
            from .inference_runtime import options,record
            runtime,requested=options()
            from fastembed import TextEmbedding
            fallback=''
            def load(path=None):
                return TextEmbedding(model_name=config['model'],cache_dir=cache,local_files_only=offline,
                                     **({'specific_model_path':str(path)} if path else {}),**runtime)
            def gpu_ready(model):
                return requested!='CUDAExecutionProvider' or 'CUDAExecutionProvider' in model.model.model.get_providers()
            model=None
            for candidate in ([accelerated,None] if accelerated else [None]):
                try:
                    model=load(candidate)
                    if not gpu_ready(model):raise RuntimeError('CUDA session unavailable')
                    break
                except Exception:
                    model=None
                    if candidate is not None:
                        logger.warning('加速模型文件不可用，改用原始 %s 权重',config['model'])
                        continue
                    if requested!='CUDAExecutionProvider' or os.environ.get('SHIXUE_EMBEDDING_DEVICE')=='cuda':raise
                    fallback='GPU 初始化失败，已使用 CPU；请检查 GPU 运行时。'
                    model=TextEmbedding(model_name=config['model'],cache_dir=cache,threads=runtime['threads'],providers=['CPUExecutionProvider'],local_files_only=offline)
                    break
            session=getattr(model.model,'model',None)
            if session is not None:
                record(root,config['model'],session,runtime['threads'],fallback,fp16=bool(accelerated and Path(model.model._model_dir).resolve()==Path(accelerated).resolve()))
            identity=model_identity(config['model'],Path(model.model._model_dir))
            _models[cache_key]=(model,identity)
            if not offline:(Path(root)/'embedding-model.json').write_text(json.dumps({'model':config['model'],'key':identity,'dimensions':config['dimensions'],
                                                                                    'fp16':bool(accelerated and Path(model.model._model_dir).resolve()==Path(accelerated).resolve())}),encoding='utf-8')
        return _models[cache_key]


def embed(root, values):
    config,purpose=configuration(root)
    prefix=config['query_prefix'] if purpose=='query' else config['document_prefix']
    values=[prefix+v for v in values]
    if config['provider']=='openai_compatible':
        from .embedding_service import remote_vectors
        return remote_vectors(root,values,config),'api:'+space(config)
    model,key=embedding_model(root,config=config)
    with _inference_slots:vectors=[vector.tolist() for vector in model.embed(values,batch_size=config['batch_size'])]
    from .embedding_service import validate_vectors
    vectors=validate_vectors(vectors,len(values),config['dimensions'])
    legacy=config['model']==LEGACY_MODEL and not config['document_prefix'] and not config['query_prefix'] and not config['revision']
    return vectors,key if legacy else key+':'+space(config)


def embed_cached(root,values):
    """Previews may use cached weights but must never start a model download."""
    config,_purpose=configuration(root)
    if config['provider']!='local':raise RuntimeError('预览不调用外部嵌入接口；请显式测试接口或提交加工任务')
    model,_key=embedding_model(root,offline=True,config=config)
    from .embedding_service import validate_vectors
    with _lock:return validate_vectors([vector.tolist() for vector in model.embed([config['document_prefix']+value for value in values],batch_size=config['batch_size'])],len(values),config['dimensions'])


def recognize(image):
    global _ocr
    with _lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)
        result,_=_ocr(image)
    if not result:
        return '',{'source':'ocr','confidence':0,'review_required':True,'lines':[]}
    lines=[{'box':box,'text':str(text),'confidence':float(score)} for box,text,score in result]
    confidence=sum(line['confidence'] for line in lines)/len(lines)
    return '\n'.join(line['text'] for line in lines),{'source':'ocr','confidence':confidence,'review_required':True,'lines':lines}
