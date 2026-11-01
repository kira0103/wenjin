"""Bounded OpenAI-compatible requests; model errors never echo provider bodies."""
import json,math
import httpx
from .embedding_secrets import read as read_key


def validate_vectors(vectors,count,dimensions):
    if not isinstance(vectors,list) or len(vectors)!=count:raise RuntimeError('嵌入接口返回数量与输入不一致')
    result=[]
    for vector in vectors:
        if not isinstance(vector,list) or len(vector)!=dimensions or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):raise RuntimeError('嵌入向量维度或数值不符合配置')
        norm=math.sqrt(sum(x*x for x in vector))
        if not math.isfinite(norm) or norm<=0:raise RuntimeError('嵌入接口返回无效的零向量')
        result.append([x/norm for x in vector])
    return result


def remote_vectors(root,texts,config):
    key=read_key(root,config);output=[]
    endpoint=config['base_url'] if config['base_url'].endswith('/embeddings') else config['base_url']+'/embeddings'
    try:
        with httpx.Client(timeout=config['timeout_seconds'],follow_redirects=False,trust_env=False) as client:
            for start in range(0,len(texts),config['batch_size']):
                values=texts[start:start+config['batch_size']]
                payload={'model':config['model'],'input':values,'encoding_format':'float'}
                if config['send_dimensions']:payload['dimensions']=config['dimensions']
                with client.stream('POST',endpoint,json=payload,headers={'Authorization':'Bearer '+key} if key else {}) as response:
                    if not response.is_success:raise ValueError('provider status')
                    body=bytearray()
                    for part in response.iter_bytes():
                        body.extend(part)
                        if len(body)>20*1024*1024:raise ValueError('oversized response')
                data=json.loads(body)['data']
                if not isinstance(data,list) or len(data)!=len(values):raise ValueError('invalid response count')
                if any(type(item['index']) is not int for item in data):raise ValueError('invalid index type')
                if sorted(item['index'] for item in data)!=list(range(len(values))):raise ValueError('invalid response indices')
                output.extend(validate_vectors([item['embedding'] for item in sorted(data,key=lambda item:item['index'])],len(values),config['dimensions']))
        return output
    except Exception:raise RuntimeError('嵌入接口请求失败或返回维度不符，请检查服务地址、模型、密钥和维度设置') from None
