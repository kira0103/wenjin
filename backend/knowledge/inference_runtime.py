"""Optional project-local GPU runtime; vector-space configuration is unchanged."""
import json,logging,os,sys,time
from pathlib import Path
logger=logging.getLogger(__name__)
_dll_handles=[]

def runtime_directory():
    default=Path(sys.executable).parent/'gpu-runtime' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]/'.local/gpu-runtime'
    return Path(os.environ.get('SHIXUE_GPU_RUNTIME',default))

def tf32_enabled():
    return os.environ.get('SHIXUE_TF32','1')!='0'

def activate():
    directory=runtime_directory()
    if os.environ.get('SHIXUE_EMBEDDING_DEVICE')=='cpu' or not (directory/'ready.json').is_file():return
    try:
        marker=json.loads((directory/'ready.json').read_text(encoding='utf-8'))
        if not marker.get('validated') or marker.get('python')!=f'{sys.version_info.major}.{sys.version_info.minor}':return
    except (OSError,ValueError):return
    if 'onnxruntime' in sys.modules:return
    # cuBLAS runs fp32 matmuls ~20% faster on Ada/Ampere with TF32 tensor cores;
    # the vectors stay in the same space (cosine > 0.99999). Set the variable
    # before the CUDA context exists, and let SHIXUE_TF32=0 restore strict fp32.
    os.environ.setdefault('NVIDIA_TF32_OVERRIDE','1' if tf32_enabled() else '0')
    sys.path.insert(0,str(directory))
    if os.name=='nt':
        for folder in (directory/'nvidia').glob('*/bin'):
            try:_dll_handles.append(os.add_dll_directory(str(folder)))
            except OSError:pass
    import onnxruntime as ort
    if hasattr(ort,'preload_dlls'):ort.preload_dlls(directory='')

def options():
    activate()
    import onnxruntime as ort
    device=os.environ.get('SHIXUE_EMBEDDING_DEVICE','auto')
    threads=max(1,min(32,int(os.environ.get('SHIXUE_EMBEDDING_THREADS',min(8,max(1,(os.cpu_count() or 4)//2))))))
    gpu=device!='cpu' and 'CUDAExecutionProvider' in ort.get_available_providers()
    providers=[('CUDAExecutionProvider',{'device_id':0,'gpu_mem_limit':6*1024**3,'arena_extend_strategy':'kSameAsRequested'}),'CPUExecutionProvider'] if gpu else ['CPUExecutionProvider']
    if device=='cuda' and not gpu:raise RuntimeError('CUDA 推理不可用，请检查 GPU 运行时安装状态')
    return {'threads':threads,'providers':providers},'CUDAExecutionProvider' if gpu else 'CPUExecutionProvider'

def record(root,model,session,threads,fallback='',fp16=False):
    try:
        root=Path(root);worker=root/'worker-status.json'
        if os.environ.get('SHIXUE_EMBEDDING_RECORD_STATUS')=='0':return
        if worker.is_file() and json.loads(worker.read_text(encoding='utf-8')).get('pid')!=os.getpid():return
        from backend.portal_database import save_private_json
        providers=session.get_providers()
        save_private_json(root/'embedding-execution.json',{'model':model,'provider':providers[0],'providers':providers,'threads':threads,'pid':os.getpid(),'updated_at':time.time(),
                                                          'tf32':bool(providers[0]=='CUDAExecutionProvider' and tf32_enabled()),'fp16':bool(fp16),'fallback':fallback})
    except (OSError,ValueError,TypeError):logger.warning('无法写入向量推理设备状态')

def status(root):
    try:
        value=json.loads((Path(root)/'embedding-execution.json').read_text(encoding='utf-8'))
        worker=json.loads((Path(root)/'worker-status.json').read_text(encoding='utf-8'))
        if value.get('pid')!=worker.get('pid'):return {'provider':'unknown','message':'等待后台加载模型'}
        return value
    except (OSError,ValueError):return {'provider':'unknown','message':'等待后台加载模型'}
