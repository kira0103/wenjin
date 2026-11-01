"""Server-only immutable credentials; no key is written into jobs or settings."""
import base64,ctypes,json,os,secrets
from pathlib import Path
from uuid import UUID


def directory(root):
    return Path(os.environ.get('SHIXUE_LIBRARY_ROOT') or root)/'.embedding-secrets'


def crypt(data,decrypt=False):
    if os.name!='nt':return data
    from ctypes import wintypes
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    api=ctypes.windll.crypt32
    ok=api.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)) if decrypt else api.CryptProtectData(ctypes.byref(source),'Wenjin embedding key',None,None,None,1,ctypes.byref(target))
    if not ok:raise RuntimeError('无法访问后台嵌入凭据，请重新设置密钥')
    try:return ctypes.string_at(target.data,target.size)
    finally:ctypes.windll.kernel32.LocalFree(target.data)


def store(root,value,audience):
    folder=directory(root);folder.mkdir(parents=True,exist_ok=True)
    from .managed import _protect,_current_sid
    _protect(folder,_current_sid() if os.name=='nt' else '')
    identifier=secrets.token_hex(16);path=folder/(identifier+'.json')
    try:
        with path.open('x',encoding='utf-8') as stream:json.dump({'format':'dpapi' if os.name=='nt' else 'private-file','audience':audience,'value':base64.b64encode(crypt(value.encode())).decode()},stream)
        _protect(path,_current_sid() if os.name=='nt' else '')
    except Exception:path.unlink(missing_ok=True);raise
    return str(UUID(identifier))


def read(root,config):
    if config.get('credential_id'):
        path=directory(root)/(UUID(str(config['credential_id'])).hex+'.json')
        try:
            stored=json.loads(path.read_text(encoding='utf-8'))
            if stored['format']!=('dpapi' if os.name=='nt' else 'private-file'):raise ValueError()
            if stored.get('audience')!=config['base_url']:raise ValueError()
            return crypt(base64.b64decode(stored['value']),True).decode()
        except Exception:raise RuntimeError('嵌入服务密钥不可用，请在后台重新设置') from None
    return os.environ.get(config.get('api_key_env','SHIXUE_EMBEDDING_API_KEY'),'')
