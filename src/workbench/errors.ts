export type FailureNotice={message:string;action:string;operation:string;status?:number;code?:string;requestId?:string};
export class ApiError extends Error {
  constructor(public notice:FailureNotice){super(notice.message);this.name='ApiError';}
}
export function operationName(path:string,method='GET'){
  const verb=method==='GET'?'读取':method==='DELETE'?'删除':'提交';
  const name=/submissions\/journals|admin\/journals/.test(path)?'期刊':/submissions/.test(path)?'投稿':/rag-documents|document-tree|catalog/.test(path)?'文献':/rag-process|jobs/.test(path)?'加工任务':/embedding|rerank-model/.test(path)?'模型':/settings/.test(path)?'配置':/projects/.test(path)?'项目':/session/.test(path)?'登录状态':'数据';
  return name+verb+'失败';
}
export function reportFailure(notice:FailureNotice){
  if(typeof window!=='undefined')window.dispatchEvent(new CustomEvent('wenjin:request-error',{detail:notice}));
}
export function statusAction(status:number,method='GET'){
  if(status===401)return '请重新登录。';
  if(status===403)return '当前账号没有此操作权限，请联系管理员。';
  if(status===404)return '请刷新列表后重新打开。';
  if(status===409)return '请先刷新相关资料，再确认是否重新提交。';
  if(status===413)return '请压缩或拆分文件后重试。';
  if(status===422||status===400)return '请检查输入内容后重试。';
  if(status===429)return '请求较多，请稍后再试。';
  return method==='GET'?'请使用页面中的重试或刷新按钮；持续失败时请检查资料服务。':'请先检查操作结果，再决定是否重新提交，避免重复创建任务。';
}
