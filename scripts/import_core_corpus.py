"""Import the local core corpus through a leased, resumable job into a candidate."""
import argparse,json,os,sys,threading,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import repository as r
from backend.knowledge.product_runtime import ProductDatabase
from backend.knowledge.worker import Worker


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,required=True);parser.add_argument('--source',type=Path,required=True);args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    for name in ('SHIXUE_KB_DATABASE_URL','SHIXUE_KB_RESTORE_URL'):os.environ.pop(name,None)
    root=args.data_dir.resolve()
    if ROOT not in root.parents:raise ValueError('目标须为当前工作区内的候选资料目录')
    output=ROOT/'.local/core-corpus-20261018';output.mkdir(parents=True,exist_ok=True)
    product=ProductDatabase(root,'internal');product.start()
    if product.startup_error:raise RuntimeError(product.startup_error)
    try:
        # This CLI owns a stopped candidate's managed cluster exclusively. Keep
        # the online worker's normal index intact; only this offline bulk path
        # rebuilds its disposable derived GIN index once after all source writes.
        with r.database.transaction(root) as c:
            r.execute(c,"SET LOCAL statement_timeout='10min'")
            r.execute(c,'DROP INDEX IF EXISTS kb_core_passages_terms')
        with r.database.transaction(root) as c:
            job=r.queue(c,'core_import',{'source_path':str(args.source.resolve())})
            owner=r.uid();r.execute(c,"UPDATE kb_jobs SET state='running',owner=:o,lease_until=clock_timestamp()+interval '45 seconds' WHERE id=:id",o=owner,id=job['id'])
            job=r.one(c,'SELECT * FROM kb_jobs WHERE id=:id',id=job['id'])
        runner=Worker();runner.root=root
        finished=threading.Event()
        def monitor():
            while not finished.wait(20):
                try:
                    with r.database.transaction(root) as c:
                        value=r.one(c,'SELECT state,progress,error FROM kb_jobs WHERE id=:id',id=job['id'])
                        corpus=r.execute(c,"SELECT state,statistics FROM kb_corpora WHERE slug='daizhige'").mappings().first()
                    report={'job_id':str(job['id']),**value,'corpus':dict(corpus) if corpus else None}
                    (output/'progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps(report,ensure_ascii=False),flush=True)
                except Exception:pass
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        try:runner.run(job)
        finally:
            finished.set();thread.join(2)
            print('Building core lexical GIN index...',flush=True)
            with r.database.transaction(root) as c:
                r.execute(c,"SET LOCAL statement_timeout='120min'")
                r.execute(c,"SET LOCAL maintenance_work_mem='512MB'")
                r.execute(c,'CREATE INDEX IF NOT EXISTS kb_core_passages_terms ON kb_core_passages USING gin(terms)')
                r.execute(c,'ANALYZE kb_core_passages')
                r.execute(c,'ANALYZE kb_core_books')
                r.execute(c,'ANALYZE kb_editions')
                r.execute(c,'ANALYZE kb_blocks')
        with r.database.transaction(root) as c:
            result=r.one(c,'SELECT state,result,error FROM kb_jobs WHERE id=:id',id=job['id'])
            corpus=r.one(c,"SELECT * FROM kb_corpora WHERE slug='daizhige'")
        report={'job_id':str(job['id']),'data_dir':str(root),'job':result,'corpus':json.loads(json.dumps(corpus,ensure_ascii=False,default=str))}
        (output/'import-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (output/'progress.json').write_text(json.dumps({'job_id':str(job['id']),'state':result['state'],
            'progress':100 if result['state']=='succeeded' else 0,'error':result['error'],
            'corpus':report['corpus']},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if result['state']!='succeeded' or result['result'].get('failed_books'):raise RuntimeError('仍有未完成导入，请查看报告后重试')
    finally:product.stop()


if __name__=='__main__':main()
