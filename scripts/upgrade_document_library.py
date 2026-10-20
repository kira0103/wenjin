"""Add directory references to the fixed library without making backup copies."""
import json
import sys
import time
from pathlib import Path
from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from upgrade_literature_categories import fingerprint
from backend.knowledge.database import database,SCHEMA_REVISION
from backend.knowledge.managed import ManagedPostgres
from backend.knowledge.config import register_managed,unregister_managed
from backend.knowledge.migration import upgrade
from backend.knowledge.document_library import DocumentLibrary


def archives(root):
    return {p.name:{'bytes':p.stat().st_size,'mtime':p.stat().st_mtime_ns} for p in (root/'backups').glob('*') if p.is_file()}


def main():
    root=ROOT/'工作台数据';output=ROOT/'.local/folders-rc10';output.mkdir(parents=True,exist_ok=True)
    if not (root/'library.sqlite').is_file():raise RuntimeError('固定资料不存在')
    if (root/'knowledge-db/postmaster.pid').exists():raise RuntimeError('工作台仍在使用资料，请先正常退出；未执行升级')
    manager=ManagedPostgres(root);started=time.monotonic()
    try:
        manager.start();register_managed(root,manager.url)
        with database.transaction(root) as db:previous=db.execute(text('SELECT version_num FROM kb_schema_version')).scalar_one()
        if previous not in ('kb0011',SCHEMA_REVISION):raise RuntimeError('资料版本不在本次更新范围')
        before=fingerprint(root);old_archives=archives(root)
        (output/'before-folder-upgrade.json').write_text(json.dumps({'schema':previous,**before},ensure_ascii=False,indent=2),encoding='utf-8')
        if previous!=SCHEMA_REVISION:upgrade(manager.url)
        groups=DocumentLibrary(root).groups()
        after=fingerprint(root)
        if before!=after:raise RuntimeError('旧文献或项目数据核对不一致，未生成完成报告')
        if archives(root)!=old_archives:raise RuntimeError('备份目录发生了意外改变，请检查')
        health=database.health(root)
        if not health['ready']:raise RuntimeError('目录结构健康检查未通过')
        report={'from_schema':previous,'to_schema':SCHEMA_REVISION,'existing_data_equal':True,'before':before,'after':after,
                'automatic_backup':False,'backup_directory_unchanged':True,'originals_copied':False,'indexes_rebuilt':False,
                'data_root':str(root),'version_data_copy':False,'groups':[{'name':g['name'],'files':g['files']} for g in groups['items']],
                'files':groups['total'],'uncategorized':groups['uncategorized'],'seconds':round(time.monotonic()-started,2)}
        (output/'folder-upgrade-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},ensure_ascii=False))
    finally:
        database.dispose();manager.stop();unregister_managed(root)


if __name__=='__main__':main()
