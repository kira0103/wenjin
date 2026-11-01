"""Read-only cache inventory. Never downloads weights or starts inference."""
import json,os,re
from pathlib import Path


def cache_directory(root):
    return Path(os.environ.get('SHIXUE_KB_MODEL_CACHE') or Path(root)/'models').resolve()


def inspect_model(cache,description):
    required=[description['model_file'],*description.get('additional_files',[]),'tokenizer.json','tokenizer_config.json']
    candidates=[];found=False;repair=False
    source=description.get('sources',{}).get('hf')
    if source and cache.is_dir():
        expected='models--'+source.replace('/','--')
        # Older FastEmbed versions used differently cased repository names.
        repositories=sorted((p for p in cache.iterdir() if p.name.lower()==expected.lower() and p.is_dir()),key=lambda p:p.name!=expected)
        for repo in repositories:
            found=True
            ref=repo/'refs'/'main'
            revision=ref.read_text(encoding='utf-8').strip() if ref.is_file() else ''
            if re.fullmatch(r'[a-fA-F0-9]{40,64}',revision):
                metadata={}
                file=repo/'files_metadata.json'
                if file.is_file():
                    metadata={key.replace('\\','/'):value for key,value in json.loads(file.read_text(encoding='utf-8')).items()}
                candidates.append((repo/'snapshots'/revision,metadata,'snapshots/'+revision+'/'))
    legacy=cache/(('fast-' if description.get('sources',{}).get('_deprecated_tar_struct') else '')+description['model'].split('/')[-1])
    if legacy.is_dir():found=True;candidates.append((legacy,{},''))
    for folder,metadata,prefix in candidates:
        size=0;complete=True
        for name in required:
            file=folder/name
            if not file.is_file() or file.stat().st_size<=0:complete=False;break
            count=file.stat().st_size
            recorded=metadata.get(prefix+name)
            if recorded and recorded.get('size')!=count:complete=False;repair=True;break
            size+=count
        if complete:return {'state':'available','bytes':size,'message':'模型权重及必要分词文件已存在；可测试实际推理。'}
    return {'state':'incomplete' if found else 'missing','bytes':0,'repair_required':repair,'message':'缓存文件不完整，请重新准备模型。' if found else '后台尚未下载此模型。'}


def inventory(root,presets):
    from fastembed import TextEmbedding
    descriptions={m['model']:m for m in TextEmbedding.list_supported_models()}
    cache=cache_directory(root);result={}
    for preset in presets:
        if preset['provider']!='local':continue
        try:
            description=descriptions.get(preset['model'])
            result[preset['model']]=inspect_model(cache,description) if description else {'state':'unknown','bytes':0,'message':'当前运行时不支持此模型。'}
            if description:result[preset['model']]['estimated_bytes']=int(description.get('size_in_GB',0)*1000**3)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            result[preset['model']]={'state':'unknown','bytes':0,'message':'缓存无法检查，请检查后台文件权限或重新准备模型。'}
    return result
