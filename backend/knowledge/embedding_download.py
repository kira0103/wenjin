"""Supervised local model preparation; downloads stop when a job is cancelled."""
import json,os,subprocess,sys,tempfile,threading,time
from pathlib import Path


def _write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8');os.replace(temporary,path)


def _command(request,result):
    if getattr(sys,'frozen',False):return [sys.executable,'--embedding-download-request',str(request),'--embedding-download-result',str(result)]
    return [sys.executable,'-m','backend.knowledge.embedding_download',str(request),str(result)]


def _populate_cache(root,config):
    from fastembed import TextEmbedding
    from .embedding_cache import cache_directory,inventory
    cached=inventory(root,[config])[config['model']]
    if cached['state']=='available':return
    description=next(m for m in TextEmbedding.list_supported_models() if m['model']==config['model'])
    required=[description['model_file'],*description.get('additional_files',[]),'tokenizer.json','tokenizer_config.json']
    from huggingface_hub import snapshot_download
    cache=cache_directory(root)
    snapshot=Path(snapshot_download(repo_id=description['sources']['hf'],cache_dir=str(cache),allow_patterns=[*required,'config.json','special_tokens_map.json','preprocessor_config.json'],force_download=cached.get('repair_required',cached['state']=='unknown')))
    if cache not in snapshot.resolve().parents:raise ValueError('Model cache is outside the configured directory')
    for name in required:
        if not (snapshot/name).is_file() or (snapshot/name).stat().st_size==0:raise ValueError('Required model files are missing')
    # FastEmbed's offline probe also reads this size manifest. A fresh manifest
    # repairs missing tokenizer files and stale/truncated-cache metadata.
    repo=snapshot.parent.parent
    metadata={file.relative_to(repo).as_posix():{'size':file.stat().st_size} for file in snapshot.rglob('*') if file.is_file()}
    _write(repo/'files_metadata.json',metadata)


def prepare(root,config,check,progress):
    from fastapi import HTTPException
    from .embedding_config import EmbeddingOptions
    from .embedding_cache import cache_directory
    from .managed import _protect,_current_sid
    config=EmbeddingOptions.model_validate(config).model_dump(mode='json')
    if config['provider']!='local':raise HTTPException(400,'接口模型由外部服务部署')
    folder=Path(root)/'private'/'model-preparation';folder.mkdir(parents=True,exist_ok=True)
    _protect(folder,_current_sid() if os.name=='nt' else '')
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        directory=Path(temporary);request=directory/'request.json';result=directory/'result.json';heartbeat=directory/'heartbeat'
        _write(request,{'root':str(Path(root).resolve()),'cache':str(cache_directory(root)),'config':config});heartbeat.touch()
        environment={k:v for k,v in os.environ.items() if not k.startswith('SHIXUE_') and k not in {'PGPASSWORD','OPENAI_API_KEY','DASHSCOPE_API_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN'}}
        environment['SHIXUE_KB_MODEL_CACHE']=str(cache_directory(root))
        options={'env':environment,'cwd':str(Path(__file__).resolve().parents[2]),'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        check();progress('download_embedding',5)
        child=subprocess.Popen(_command(request,result),**options)
        try:
            last_stage='download_embedding'
            while child.poll() is None:
                check();heartbeat.touch()
                try:
                    state=json.loads((directory/'stage.json').read_text(encoding='utf-8'))['stage']
                    if state=='verify_embedding' and state!=last_stage:progress(state,90);last_stage=state
                except (OSError,ValueError,KeyError):pass
                time.sleep(.4)
            check()
            try:
                response=json.loads(result.read_text(encoding='utf-8'))
                if child.returncode!=0 or not response.get('ok') or response['result']['dimensions']!=config['dimensions']:raise ValueError()
            except (OSError,ValueError,KeyError,TypeError):
                raise HTTPException(503,'模型下载或校验失败，请检查后台网络、磁盘空间和模型文件后重试') from None
            # A fresh load uses the newly verified files. Other cached models stay intact.
            from . import models
            with models._lock:models._models.pop((str(cache_directory(root)),config['model']),None)
            progress('verify_embedding',95)
            return response['result']
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


def child_main(request,result):
    request=Path(request);result=Path(result);heartbeat=request.parent/'heartbeat';finished=threading.Event()
    def watch():
        while not finished.wait(2):
            try:
                if time.time()-heartbeat.stat().st_mtime>30:os._exit(2)
            except OSError:os._exit(2)
    threading.Thread(target=watch,daemon=True).start()
    try:
        data=json.loads(request.read_text(encoding='utf-8'))
        from .embedding_config import EmbeddingOptions
        config=EmbeddingOptions.model_validate(data['config']).model_dump(mode='json')
        if config['provider']!='local':raise ValueError('local models only')
        os.environ['SHIXUE_KB_MODEL_CACHE']=data['cache']
        _populate_cache(Path(data['root']),config)
        from .models import embedding_context,embedding_model,embed
        with embedding_context(config):
            embedding_model(Path(data['root']))
            _write(request.parent/'stage.json',{'stage':'verify_embedding'})
            vectors,key=embed(Path(data['root']),['文献向量模型校验'])
        if len(vectors)!=1 or len(vectors[0])!=config['dimensions']:raise ValueError('invalid model output')
        _write(result,{'ok':True,'result':{'model_key':key,'dimensions':len(vectors[0])}})
        return 0
    except Exception:
        _write(result,{'ok':False});return 1
    finally:finished.set()


if __name__=='__main__':raise SystemExit(child_main(*sys.argv[1:]))
