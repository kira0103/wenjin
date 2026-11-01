"""Embedding capabilities and immutable vector-space identities, without secrets."""
import hashlib,json
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,model_validator

LEGACY_MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
PRESETS=[
    {'id':'minilm','name':LEGACY_MODEL,'provider':'local','model':LEGACY_MODEL,'dimensions':384},
    {'id':'bge-small-zh','name':'BAAI/bge-small-zh-v1.5','provider':'local','model':'BAAI/bge-small-zh-v1.5','dimensions':512,'query_prefix':'为这个句子生成表示以用于检索相关文章：'},
    {'id':'bge-base-en','name':'BAAI/bge-base-en-v1.5','provider':'local','model':'BAAI/bge-base-en-v1.5','dimensions':768,'query_prefix':'Represent this sentence for searching relevant passages: '},
    {'id':'e5-large','name':'intfloat/multilingual-e5-large','provider':'local','model':'intfloat/multilingual-e5-large','dimensions':1024,'document_prefix':'passage: ','query_prefix':'query: '},
    {'id':'bge-m3','name':'BAAI/bge-m3','provider':'openai_compatible','model':'BAAI/bge-m3','dimensions':1024},
    {'id':'qwen3','name':'Qwen/Qwen3-Embedding-0.6B','provider':'openai_compatible','model':'Qwen/Qwen3-Embedding-0.6B','dimensions':1024,'min_dimensions':32,'max_dimensions':1024,'send_dimensions':True},
    {'id':'openai-small','name':'text-embedding-3-small','provider':'openai_compatible','model':'text-embedding-3-small','dimensions':1536,'min_dimensions':32,'max_dimensions':1536,'send_dimensions':True,'base_url':'https://api.openai.com/v1'},
    {'id':'openai-large','name':'text-embedding-3-large','provider':'openai_compatible','model':'text-embedding-3-large','dimensions':3072,'min_dimensions':32,'max_dimensions':3072,'send_dimensions':True,'base_url':'https://api.openai.com/v1'},
    {'id':'dashscope','name':'text-embedding-v4','provider':'openai_compatible','model':'text-embedding-v4','dimensions':1024,'allowed_dimensions':[64,128,256,512,768,1024,1536,2048],'send_dimensions':True},
]


class EmbeddingOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['local','openai_compatible']='local'
    model:str=Field(default=LEGACY_MODEL,min_length=1,max_length=200)
    dimensions:int=Field(default=384,ge=32,le=4096)
    base_url:str=Field(default='',max_length=1000)
    send_dimensions:bool=False
    credential_id:UUID|None=None
    api_key_env:Literal['SHIXUE_EMBEDDING_API_KEY','OPENAI_API_KEY','DASHSCOPE_API_KEY']='SHIXUE_EMBEDDING_API_KEY'
    batch_size:int=Field(default=16,ge=1,le=64)
    timeout_seconds:int=Field(default=60,ge=5,le=120)
    document_prefix:str=Field(default='',max_length=1000)
    query_prefix:str=Field(default='',max_length=1000)
    revision:str=Field(default='',max_length=100)

    @model_validator(mode='after')
    def valid(self):
        self.model=self.model.strip();self.base_url=self.base_url.strip().rstrip('/')
        if not self.model:raise ValueError('请填写嵌入模型名称')
        preset=next((p for p in PRESETS if p['provider']==self.provider and p['model']==self.model),None)
        if self.provider=='local':
            if not preset:raise ValueError('该本地模型不在已支持的运行时列表中')
            if self.dimensions!=preset['dimensions']:raise ValueError('该本地模型输出维度固定，不能任意更改')
            if self.base_url or self.send_dimensions:raise ValueError('本地模型不使用接口地址或接口维度参数')
        else:
            u=urlsplit(self.base_url)
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('请填写不含密钥、查询参数的 HTTP(S) 服务根地址')
            if preset:
                if 'allowed_dimensions' in preset and self.dimensions not in preset['allowed_dimensions']:raise ValueError('该模型不支持所选维度')
                if 'max_dimensions' in preset and not preset['min_dimensions']<=self.dimensions<=preset['max_dimensions']:raise ValueError('维度超出该模型支持范围')
                if 'max_dimensions' not in preset and 'allowed_dimensions' not in preset and self.dimensions!=preset['dimensions']:raise ValueError('该模型使用固定输出维度')
                if self.dimensions!=preset['dimensions'] and not self.send_dimensions:raise ValueError('调整输出维度时须启用 dimensions 参数')
        return self


def space(config):
    value=EmbeddingOptions.model_validate(config).model_dump(mode='json')
    return hashlib.sha256(json.dumps({k:value[k] for k in ('provider','model','dimensions','base_url','document_prefix','query_prefix','revision')},sort_keys=True).encode()).hexdigest()


def current(root,connection=None):
    from .retrieval_settings import read
    return read(root,connection)['options']['embedding']
