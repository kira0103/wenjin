"""CPU inference adapters. Download public models; document inference stays local."""
import hashlib
import json
import threading
import os
from pathlib import Path

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
_lock=threading.RLock()
_models={}
_ocr=None


def embedding_model(root):
    with _lock:
        cache=str(Path(os.environ.get('SHIXUE_KB_MODEL_CACHE',str(Path(root)/'models'))).resolve())
        if cache not in _models:
            from fastembed import TextEmbedding
            model=TextEmbedding(model_name=MODEL,cache_dir=cache,threads=2)
            # Other model families can share a cache. Only fingerprint the model
            # actually used for these vectors, never neighboring reranker weights.
            directory=Path(model.model._model_dir).resolve()
            files=sorted(file for file in directory.rglob('*') if file.is_file() and (file.suffix=='.onnx' or file.name in {'tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json'}))
            if not any(file.suffix=='.onnx' for file in files):
                raise RuntimeError('嵌入模型文件未就绪')
            digest=hashlib.sha256()
            fingerprints=set()
            for file in files:
                with file.open('rb') as stream:
                    fingerprints.add(hashlib.file_digest(stream,'sha256').digest())
            for fingerprint in sorted(fingerprints):digest.update(fingerprint)
            identity=MODEL+'@'+digest.hexdigest()
            _models[cache]=(model,identity)
            (Path(root)/'embedding-model.json').write_text(json.dumps({'model':MODEL,'key':identity,'dimensions':384}),encoding='utf-8')
        return _models[cache]


def embed(root, values):
    model,key=embedding_model(root)
    with _lock:
        vectors=[vector.tolist() for vector in model.embed(values,batch_size=16)]
    if any(len(v)!=384 for v in vectors):
        raise RuntimeError('向量维度与当前索引不匹配')
    return vectors,key


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
