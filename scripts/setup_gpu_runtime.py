"""Install and validate an optional GPU runtime without replacing the CPU environment."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library-root',type=Path)
    parser.add_argument('--model',default='intfloat/multilingual-e5-large')
    parser.add_argument('--reinstall',action='store_true')
    args=parser.parse_args();project=Path(__file__).resolve().parents[1];target=project/'.local/gpu-runtime'
    if args.reinstall or not (target/'nvidia/cudnn/bin/cudnn64_9.dll').is_file():
        subprocess.run([sys.executable,'-m','pip','install','--no-deps','--no-cache-dir','--upgrade','--target',str(target),'-r',str(project/'backend/requirements-gpu.txt')],check=True)
    sys.path.insert(0,str(project));sys.path.insert(0,str(target));handles=[]
    if os.name=='nt':
        for folder in (target/'nvidia').glob('*/bin'):handles.append(os.add_dll_directory(str(folder)))
    import onnxruntime as ort
    ort.preload_dlls(directory='')
    if not args.library_root:
        launcher=json.loads((project/'.local/backend-launcher.json').read_text(encoding='utf-8'));args.library_root=Path(launcher['data_dir'])/'shared-library'
    from fastembed import TextEmbedding
    start=time.monotonic();model=TextEmbedding(model_name=args.model,cache_dir=str(args.library_root/'models'),local_files_only=True,threads=8,providers=['CUDAExecutionProvider','CPUExecutionProvider'])
    providers=model.model.model.get_providers()
    if providers[0]!='CUDAExecutionProvider':raise RuntimeError('GPU model initialization failed; CPU installation was not changed')
    vectors=list(model.embed(['passage: 文献向量模型 GPU 固定测试句。'],batch_size=1))
    value={'validated':True,'python':f'{sys.version_info.major}.{sys.version_info.minor}','runtime':ort.__version__,'model':args.model,'providers':providers,'dimensions':len(vectors[0]),'validated_at':time.time()}
    (target/'ready.json').write_text(json.dumps(value),encoding='utf-8');print(json.dumps({**value,'elapsed_seconds':round(time.monotonic()-start,2)},ensure_ascii=False))

if __name__=='__main__':main()
