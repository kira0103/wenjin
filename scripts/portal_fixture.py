"""Disposable local portal used only by the hidden-window GUI verification."""
import argparse
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from backend.portal import create_app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    password=os.environ['SHIXUE_GUI_PASSWORD']
    os.environ.pop('SHIXUE_PORTAL_PG_ADMIN_URL',None)
    os.environ['SHIXUE_MODEL_API_KEY']=''
    os.environ['HF_HUB_OFFLINE']='1'
    application=create_app(args.root,Path(__file__).resolve().parents[1]/'dist')
    accounts=application.state.accounts
    accounts.create('administrator','问津管理员',password,'admin',require_change=False)
    accounts.create('alice','林若舟',password,require_change=False)
    accounts.create('bobby','陈知远',password,require_change=False)
    server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=args.port,log_level='warning'))
    stop=threading.Event()
    def watch():
        while not stop.wait(.5):
            if (args.root/'gui.stop').exists():
                server.should_exit=True
                return
    thread=threading.Thread(target=watch,daemon=True);thread.start()
    try:
        server.run()
    finally:
        stop.set();thread.join(timeout=2)
        fleet=application.state.fleet
        fleet.request_stop_all([accounts.get(user['id']) for user in accounts.list()])
        for child in fleet.children.values():child.wait(timeout=120)


if __name__=='__main__':main()
