"""Bounded user errors with log references; never echo exception messages or inputs."""
import logging
import traceback
from pathlib import Path
from uuid import uuid4
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError,TimeoutError as PoolTimeout

logger=logging.getLogger('wenjin.errors')

def problem_response(request,error,*,code=None,message=None,status=None):
    sqlstate=getattr(getattr(error,'orig',None),'sqlstate',None)
    if code is None:
        if sqlstate=='57014':code,message,status='DATABASE_TIMEOUT','文献库查询超时，请稍后重试；后台任务会继续执行。',503
        elif sqlstate=='55P03' or isinstance(error,PoolTimeout):code,message,status='DATABASE_BUSY','文献库当前繁忙，请稍后重试。',503
        elif sqlstate in {'23503','23505'}:code,message,status='DATA_CONFLICT','资料存在重复或关联冲突，请刷新后核对。',409
        elif isinstance(error,SQLAlchemyError):code,message,status='DATABASE_UNAVAILABLE','文献库暂时不可用，请检查后台数据库状态。',503
        elif isinstance(error,ValueError):code,message,status='INVALID_CONFIGURATION','文献库处理未完成，请检查配置和资料状态。',503
        else:code,message,status='SERVER_ERROR','后台操作发生异常，请稍后重试或联系管理员。',500
    identifier=uuid4().hex[:12]
    route=getattr(request.scope.get('route'),'path','/api')
    frames=' > '.join(f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}' for frame in [frame for frame in traceback.extract_tb(error.__traceback__) if 'site-packages' not in frame.filename][-6:])
    logger.error('request_id=%s code=%s method=%s route=%s type=%s sqlstate=%s frames=%s',identifier,code,request.method,route,type(error).__name__,sqlstate,frames)
    return JSONResponse({'detail':message,'code':code,'request_id':identifier},status_code=status,headers={'X-Request-ID':identifier})

def install_handlers(application):
    @application.exception_handler(Exception)
    async def unexpected(request,error):return problem_response(request,error)
