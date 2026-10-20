"""Check the copied candidate with local keyword/vector retrieval, no chat model."""
import hashlib,json,os,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.knowledge import repository as r,assistant_registry
from backend.knowledge.product_runtime import ProductDatabase
from backend.knowledge.retrieval import search


def main():
    os.environ.pop('SHIXUE_KB_DATABASE_URL',None);os.environ['HF_HUB_OFFLINE']='1'
    root=ROOT/'release/v0.5.0-rc.5/史学工作台验证数据';product=ProductDatabase(root,'internal')
    try:
        product.start()
        if product.startup_error:raise RuntimeError(product.startup_error)
        with r.database.transaction(root) as c:
            block=r.one(c,"SELECT b.body FROM kb_blocks b JOIN kb_editions e ON e.active_revision=b.revision_id WHERE length(trim(b.body))>12 ORDER BY b.id LIMIT 1")
            jobs=r.rows(c,'SELECT state,count(*) count FROM kb_jobs GROUP BY state')
        query=block['body'].strip()[:12];results={}
        for mode in ('exact','keyword','semantic'):
            found=search(root,{'query':query,'mode':mode,'scope':{},'source_kinds':['document'],'limit':3})
            if not found['results']:raise RuntimeError(mode+' 检索未返回结果')
            results[mode]={'hits':len(found['results']),'warnings':found['warnings']}
        skills=assistant_registry.list_definitions(root,'skill');agents=assistant_registry.list_definitions(root,'agent')
        status=r.database.health(root)
        if not status['ready']:raise RuntimeError('副本健康检查失败')
        output=ROOT/'.local/assistant-collaboration-20261017/candidate-retrieval.json'
        output.write_text(json.dumps({'ready':True,'schema':status['schema_revision'],'retrieval':results,'skills':len(skills),'agents':len(agents),'jobs':jobs,'chat_model_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ready':True,'schema':status['schema_revision'],'modes':list(results),'skills':len(skills),'agents':len(agents),'chat_model_calls':0}))
    finally:product.stop()


if __name__=='__main__':main()
