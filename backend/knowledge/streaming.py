import json
import queue
import threading
import ipaddress
from urllib.parse import urlparse

import httpx
try:
    from backend.credentials import model_key
except ModuleNotFoundError:
    from credentials import model_key

from .retrieval import answer


def events(root,payload,settings):
    messages=queue.Queue();cancelled=threading.Event();response_ref=[]

    def completion(prompts,options,budget):
        secret=model_key()
        host=(urlparse(options['base_url']).hostname or '').lower()
        local=host=='localhost'
        try:local=local or ipaddress.ip_address(host).is_loopback
        except ValueError:pass
        if not local and not secret:raise ValueError('缺少模型密钥')
        headers={'Content-Type':'application/json'}
        if secret:headers['Authorization']='Bearer '+secret
        try:
            from backend.collation_prompt import request_options
        except ModuleNotFoundError:
            from collation_prompt import request_options
        body={'model':options['model'],**request_options(options),'messages':prompts,'max_tokens':budget,'stream':True}
        output=[]
        with httpx.stream('POST',options['base_url'].rstrip('/')+'/chat/completions',headers=headers,json=body,
                          timeout=httpx.Timeout(60,connect=15)) as response:
            response_ref.append(response);response.raise_for_status()
            for line in response.iter_lines():
                if cancelled.is_set():raise ValueError('已取消生成')
                if not line.startswith('data:'):continue
                data=line[5:].strip()
                if data=='[DONE]':break
                item=json.loads(data);choices=item.get('choices',[])
                if not choices:continue
                delta=choices[0].get('delta',{}).get('content','')
                if not isinstance(delta,str):continue
                if secret:delta=delta.replace(secret,'[凭据已隐藏]')
                output.append(delta)
                if sum(map(len,output))>50000:raise ValueError('生成超出长度限制')
                messages.put({'type':'delta','text':delta})
        return {'text':''.join(output)}

    def run():
        try:
            result=answer(root,payload,completion,settings)
            messages.put({'type':'complete',**result})
        except Exception:
            messages.put({'type':'error','message':'回答未通过或请求未完成，请检查模型连接与证据后重试；流式草稿未保存。'})
        finally:messages.put(None)

    thread=threading.Thread(target=run,daemon=True,name='knowledge-answer');thread.start()
    try:
        while True:
            try:item=messages.get(timeout=2)
            except queue.Empty:
                yield ': heartbeat\n\n';continue
            if item is None:break
            yield 'data: '+json.dumps(item,ensure_ascii=False)+'\n\n'
    finally:
        cancelled.set()
        for response in response_ref:response.close()
