"""Verify all imported originals and exercise the full-size candidate locally."""
import argparse,hashlib,json,os,sqlite3,sys,time
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import core_corpus as core,repository as r
from backend.knowledge.product_runtime import ProductDatabase


def sha(file):
    with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,required=True);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--reuse-originals-report',action='store_true');args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    for key in ('SHIXUE_KB_DATABASE_URL','SHIXUE_KB_RESTORE_URL'):os.environ.pop(key,None)
    root=args.data_dir.resolve();source=args.source.resolve();output=ROOT/'.local/core-corpus-20261018'
    baseline=json.loads((ROOT/'迁移备份/2026-10-17-rc5到rc6/核验报告.json').read_text(encoding='utf-8'))
    product=ProductDatabase(root,'internal');product.start()
    if product.startup_error:raise RuntimeError(product.startup_error)
    try:
        health=r.database.health(root)
        if not health['ready']:raise RuntimeError('候选库健康检查失败')
        source_books,revision=core.inventory(source)
        expected_files={p for paths in source_books.values() for p in paths}
        with r.database.transaction(root) as c:
            r.execute(c,"SET LOCAL statement_timeout='60s'")
            corpus=r.one(c,"SELECT * FROM kb_corpora WHERE slug='daizhige'")
            if corpus['state']!='ready' or corpus['statistics'].get('failed_books'):raise RuntimeError('古籍导入尚未全部完成')
            files=r.rows(c,'SELECT f.source_path,a.sha256,a.storage_key FROM kb_core_files f JOIN kb_core_books cb ON cb.imported_revision=f.revision_id '
                         'JOIN kb_assets a ON a.id=f.asset_id WHERE cb.corpus_id=:id ORDER BY f.source_path',id=corpus['id'])
            if {v['source_path'] for v in files}!=expected_files:raise RuntimeError('源文件集合与导入集合不一致')
            imported_books=r.execute(c,'SELECT count(*) FROM kb_core_books WHERE corpus_id=:id',id=corpus['id']).scalar_one()
            if imported_books!=len(source_books):raise RuntimeError('归并书目数量不一致')
            index=r.one(c,"SELECT indisvalid,indisready,pg_relation_size(indexrelid) bytes FROM pg_index WHERE indexrelid='kb_core_passages_terms'::regclass")
            if not index['indisvalid'] or not index['indisready']:raise RuntimeError('原文索引未就绪')
            bytes_db=r.execute(c,'SELECT pg_database_size(current_database())').scalar_one()
            # Existing user editions remain in the same tables with their active
            # source and vector indexes; no subset of the corpus overwrites them.
            retained=r.one(c,'SELECT count(*) editions FROM kb_editions e WHERE NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)')
            active_chunks=r.execute(c,'SELECT count(*) FROM kb_chunks ch JOIN kb_editions e ON e.active_index=ch.index_id WHERE NOT EXISTS(SELECT 1 FROM kb_core_books cb WHERE cb.edition_id=e.id)').scalar_one()
            if retained['editions']!=baseline['pg_counts']['kb_editions'] or active_chunks!=baseline['active_chunks']:raise RuntimeError('原有文献或当前向量索引数量不一致')
            preserved={}
            for table in ('kb_assistant_definitions','kb_assistant_versions','kb_notes','kb_note_revisions','kb_project_sources','kb_research_runs','kb_collaboration_runs'):
                preserved[table]=r.execute(c,'SELECT count(*) FROM '+table).scalar_one()
                if preserved[table]!=baseline['pg_counts'][table]:raise RuntimeError('原有研究数据数量不一致：'+table)
            song=r.one(c,"SELECT edition_id,file_count FROM kb_core_books WHERE source_key='史藏/正史/宋史.md'")
            pei=r.one(c,"SELECT edition_id,file_count FROM kb_core_books WHERE source_key='子藏/类书/御定佩文韵府/@parts'")
        if args.reuse_originals_report:
            previous=json.loads((output/'candidate-data-verification.json').read_text(encoding='utf-8'))
            if root!=Path(baseline['target']).resolve() or not previous['ready'] or previous['files']!=len(files) or previous['source_git_baseline']!=revision:
                raise RuntimeError('没有同一候选库的完整原件核验记录，不能复用')
            verified=previous['original_sha256_verified']
            print('Reusing completed original-file verification; rerunning full-size queries.',flush=True)
        else:
            print('Verifying all source and copied-file SHA-256 hashes...',flush=True)
            verified=0
            for item in files:
                if sha(source/item['source_path'])!=item['sha256'] or sha(r.asset_path(root,item['storage_key']))!=item['sha256']:
                    raise RuntimeError('源文件或复制原件校验不一致：'+item['source_path'])
                verified+=1
                if verified%5000==0:print('verified originals:',verified,flush=True)
        with closing(sqlite3.connect('file:'+str(root/'library.sqlite').replace('\\','/')+'?mode=ro',uri=True)) as db:
            counts={table:db.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0] for table in baseline['sqlite_counts']}
        if counts!=baseline['sqlite_counts']:raise RuntimeError('旧工作台资料数量不一致')
        queries=[]
        def check(query,mode='literal',categories=None,edition_id=None,minimum=1):
            before=time.perf_counter();found=core.search(root,query=query,mode=mode,categories=categories,edition_id=edition_id,limit=10)
            elapsed=round((time.perf_counter()-before)*1000,1)
            if len(found['items'])<minimum:raise RuntimeError('全量库检索未命中：'+query)
            if any(query not in hit['quote'] for hit in found['items']) and mode=='literal':raise RuntimeError('逐字检索引用范围不一致')
            result={'query':query,'mode':mode,'returned':len(found['items']),'elapsed_ms':elapsed,'has_next':bool(found['next_cursor'])}
            queries.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
            return found
        check('太祖啟運立極',edition_id=song['edition_id'])
        check('学而时习之','exact',['儒藏'])
        check('孔子')
        check('之')
        check('萬幾在御',edition_id=pei['edition_id'])
        first=check('孔子',categories=['儒藏'])
        if first['next_cursor']:
            second=core.search(root,query='孔子',mode='literal',categories=['儒藏'],limit=10,cursor=first['next_cursor'])
            a={(x['source_path'],x['source_start']+x['start_offset']) for x in first['items']}
            b={(x['source_path'],x['source_start']+x['start_offset']) for x in second['items']}
            if a&b:raise RuntimeError('逐字分页出现重复位置')
        from backend.knowledge.retrieval import search
        before=time.perf_counter();rag=search(root,{'query':'太祖啟運立極','mode':'exact','scope':{'edition_ids':[str(song['edition_id'])]},'source_kinds':['document'],'limit':5})
        if not rag['results']:raise RuntimeError('实际古籍 RAG 检索未命中')
        rag_ms=round((time.perf_counter()-before)*1000,1)
        result={'ready':True,'schema':health['schema_revision'],'source_git_baseline':revision,'books':imported_books,'files':len(files),'original_sha256_verified':verified,
                'database_bytes':bytes_db,'lexical_index_bytes':index['bytes'],'existing_editions':retained['editions'],'existing_active_vector_chunks':active_chunks,
                'existing_research_counts':preserved,'sqlite_counts':counts,'all_originals_verified':True,'queries':queries,'actual_core_rag_hits':len(rag['results']),'actual_core_rag_ms':rag_ms,
                'song_history_files':song['file_count'],'peiwen_files':pei['file_count'],'cloud_model_calls':0,'originals_verification_reused':args.reuse_originals_report}
        (output/'candidate-data-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:result[k] for k in ('ready','books','files','original_sha256_verified','database_bytes','actual_core_rag_hits')},ensure_ascii=False))
    finally:product.stop()


if __name__=='__main__':main()
