"""Exclusive, backed-up kb0011 upgrade; preserves all existing literature and SQLite."""
import hashlib
import json
import sys
import time
from pathlib import Path
from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge.database import database,SCHEMA_REVISION
from backend.knowledge.managed import ManagedPostgres
from backend.knowledge.config import register_managed,unregister_managed
from backend.knowledge.activation import upgrade_backup
from backend.knowledge.migration import upgrade
from backend.knowledge.literature_categories import LiteratureCategories


def fingerprint(root):
    with database.transaction(root) as db:
        db.execute(text('SET LOCAL statement_timeout=0'))
        names=('kb_works','kb_editions','kb_assets','kb_blocks','kb_revisions','kb_indexes','kb_chunks','kb_anchors',
               'kb_core_books','kb_core_files','kb_core_passages','kb_project_sources','kb_notes','kb_topics',
               'kb_assistant_definitions','kb_assistant_versions')
        counts={name:db.execute(text('SELECT count(*) FROM '+name)).scalar_one() for name in names}
        # Stable identity and active revision/index prove editions were not replaced or reindexed.
        identities=db.execute(text("SELECT md5(string_agg(row_to_json(v)::text,'' ORDER BY id)) FROM (SELECT id,work_id,active_revision,active_index,version,deleted FROM kb_editions) v")).scalar_one()
    with (root/'library.sqlite').open('rb') as stream:sqlite=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'tables':counts,'edition_fingerprint':identities,'sqlite_sha256':sqlite}


def main():
    root=ROOT/'工作台数据';out=ROOT/'.local/categories-rc9';out.mkdir(parents=True,exist_ok=True)
    if not (root/'library.sqlite').is_file():raise RuntimeError('固定资料目录不存在')
    if (root/'knowledge-db/postmaster.pid').exists():raise RuntimeError('工作台仍在使用资料库，请先正常退出；未执行升级')
    manager=ManagedPostgres(root);started=time.monotonic();backup=None
    try:
        manager.start();register_managed(root,manager.url)
        with database.transaction(root) as db:before_revision=db.execute(text('SELECT version_num FROM kb_schema_version')).scalar_one()
        if before_revision not in ('kb0010',SCHEMA_REVISION):raise RuntimeError('资料版本不在本轮升级范围')
        before=fingerprint(root)
        (out/'before-category-upgrade.json').write_text(json.dumps({'schema':before_revision,**before},ensure_ascii=False,indent=2),encoding='utf-8')
        if before_revision!=SCHEMA_REVISION:
            print('正在生成升级前完整备份…',flush=True)
            backup=upgrade_backup(root)
            print('完整备份已生成，开始添加分类结构…',flush=True)
            upgrade(manager.url)
        after=fingerprint(root)
        if before!=after:raise RuntimeError('已有文献或项目数据核对不一致，请检查升级报告及完整备份')
        health=database.health(root)
        if not health['ready']:raise RuntimeError('新资料结构健康检查未通过')
        tree=LiteratureCategories(root).tree()
        report={'from_schema':before_revision,'to_schema':SCHEMA_REVISION,'backup':backup,'data_root':str(root),
                'existing_data_equal':True,'before':before,'after':after,'category_counts':{'ancient':tree['all']['ancient'],'papers':tree['all']['papers']},
                'auto_classification':False,'version_data_copy':False,'seconds':round(time.monotonic()-started,2)}
        (out/'category-upgrade-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('before','after')},ensure_ascii=False),flush=True)
    finally:
        database.dispose();manager.stop();unregister_managed(root)


if __name__=='__main__':main()
