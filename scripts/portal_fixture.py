"""Disposable local portal used only by the hidden-window GUI verification."""
import argparse
import os
from pathlib import Path
import sys
import threading
import json
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from backend.portal import create_app


def seed_completed_result(root, accounts, command):
    """Local test control only: no model call, no HTTP seeding endpoint."""
    user = next(item for item in accounts.list() if item['username']=='alice')
    workspace = accounts.get(user['id'])['workspace_id']
    timestamp = datetime.now(timezone.utc).isoformat()
    body = json.dumps({'content':'界面验收用的研究成果。原文、研究判断与人工复核分别保存。',
                       'source_context':{'selection':'第 1 段','model':'隔离界面夹具'},'source_excerpt':'界面验收原文。'},ensure_ascii=False)
    with sqlite3.connect(root/'workspaces'/workspace/'library.sqlite') as db:
        if not db.execute('SELECT 1 FROM projects WHERE id=?',(command['project_id'],)).fetchone():
            raise ValueError('Fixture project missing')
        db.execute('INSERT INTO tasks(id,project_id,task_type,title,instructions,status,input_json,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('gui-layout-task',command['project_id'],'summary','界面验收任务','','completed',json.dumps({'task_type':'summary','project_id':command['project_id'],'source_text':'界面验收原文。'}),body,timestamp,timestamp))
        db.execute('INSERT INTO artifacts(id,project_id,task_id,title,artifact_type,body,created_at) VALUES(?,?,?,?,?,?,?)',
                   ('gui-layout-artifact',command['project_id'],'gui-layout-task','界面验收研究成果','summary',body,timestamp))


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
            seed = args.root/'gui.seed.json'
            if seed.exists():
                try:
                    command=json.loads(seed.read_text(encoding='utf-8'))
                    seed.unlink()
                    seed_completed_result(args.root,accounts,command)
                    (args.root/'gui.seed-result.json').write_text('{"ok":true}',encoding='utf-8')
                except Exception as error:
                    (args.root/'gui.seed-result.json').write_text(json.dumps({'error':str(error)}),encoding='utf-8')
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
