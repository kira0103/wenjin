"""Cancelable HTTP transport for in-flight batch requests."""
import asyncio
import httpx

class ModelCallCancelled(Exception):
    cancelled=True
    def __init__(self,sent=True):
        self.sent=sent
        self.reply={'text':'','usage':None,'finish_reason':'client_cancelled'} if sent else None
        super().__init__('本地已取消在途请求；服务端是否继续执行及已产生用量未知' if sent else '用户停止，模型请求尚未发送')

def cancellable_post(url,headers,body,cancel_event):
    async def request():
        if cancel_event.is_set():raise ModelCallCancelled(sent=False)
        async with httpx.AsyncClient(timeout=httpx.Timeout(180,connect=20)) as client:
            running=asyncio.create_task(client.post(url,headers=headers,json=body))
            async def watch():
                while not cancel_event.is_set():await asyncio.sleep(.05)
            stopping=asyncio.create_task(watch())
            try:
                done,_=await asyncio.wait((running,stopping),return_when=asyncio.FIRST_COMPLETED)
                if running in done:return await running
                running.cancel()
                await asyncio.gather(running,return_exceptions=True)
                raise ModelCallCancelled()
            finally:
                stopping.cancel()
                await asyncio.gather(stopping,return_exceptions=True)
    return asyncio.run(request())
