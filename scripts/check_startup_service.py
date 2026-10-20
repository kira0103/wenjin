"""Measure packaged service startup against an isolated, existing GUI fixture."""
import argparse,json,os,secrets,socket,subprocess,time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('binary',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    root=Path(json.loads((ROOT/'.local/folders-rc10/packaged-gui-report.json').read_text(encoding='utf-8'))['root'])
    if (root/'knowledge-db/postmaster.pid').exists():raise RuntimeError('隔离资料正在使用')
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    token=secrets.token_hex(32);env={**os.environ,'HF_HUB_OFFLINE':'1'};env.pop('SHIXUE_KB_DATABASE_URL',None)
    start=time.monotonic()
    with args.output.with_suffix('.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([str(args.binary.resolve()),'--port',str(port),'--data-dir',str(root),'--token',token,'--database-mode','internal'],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-App-Token':token},timeout=10) as client:
            try:
                deadline=start+90
                while time.monotonic()<deadline:
                    if child.poll() is not None:raise RuntimeError('资料服务提前退出')
                    try:
                        response=client.get('/health')
                        if response.is_success:break
                    except httpx.HTTPError:pass
                    time.sleep(.05)
                else:raise RuntimeError('资料服务启动超时')
                ready=time.monotonic()-start
                response=client.get('/api/bootstrap?project_id=project-main');response.raise_for_status()
                report={'binary':str(args.binary),'version':client.get('/health').json()['version'],'service_ready_seconds':round(ready,3),'bootstrap_seconds':round(time.monotonic()-start,3),'isolated_data':True,'cloud_model_calls':0}
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report))
            finally:
                if child.poll() is None:client.post('/api/service/exit').raise_for_status();child.wait(timeout=40)
    if (root/'knowledge-db/postmaster.pid').exists():raise RuntimeError('隔离资料未正常关闭')

if __name__=='__main__':main()
