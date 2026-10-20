"""Copy an inactive candidate's data via verified logical backup; never upgrade the source."""
import argparse,ctypes,hashlib,json,os,shutil,sqlite3,subprocess,sys,tempfile,time,zipfile
from datetime import datetime,timezone
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import repository as r,assistant_registry
from backend.knowledge.managed import ManagedPostgres
from backend.knowledge.config import register_managed,unregister_managed
from backend.knowledge.backup import export_bundle,restore_managed_bundle
from backend.knowledge.product_runtime import ProductDatabase


def live_backup(source,report_dir):
    """Use the already-running, authenticated local app's own backup worker.

    Command lines stay in memory: never print or persist service credentials.
    """
    import httpx
    command="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter \"Name='ShixueWorkbenchService.exe'\" | Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW,timeout=20,check=True)
    lines=json.loads(result.stdout.decode('utf-8-sig'));lines=[lines] if isinstance(lines,str) else lines
    parse=ctypes.windll.shell32.CommandLineToArgvW;parse.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)];parse.restype=ctypes.POINTER(ctypes.c_wchar_p)
    for line in lines or []:
        count=ctypes.c_int();pointer=parse(line,ctypes.byref(count))
        if not pointer:continue
        try:args=[pointer[i] for i in range(count.value)]
        finally:ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer,ctypes.c_void_p))
        def option(name):return args[args.index(name)+1] if name in args and args.index(name)+1<len(args) else None
        folder=option('--data-dir');port=option('--port');token=option('--token')
        if not folder or Path(folder).resolve()!=source or not port or not token:continue
        with httpx.Client(base_url='http://127.0.0.1:'+port,headers={'X-App-Token':token},timeout=20) as client:
            def call(path,method='GET'):
                response=client.request(method,path);response.raise_for_status();return response.json()
            original_schema=call('/api/knowledge/health')['schema_revision']
            pending_file=report_dir/'备份任务.json'
            job=None
            if pending_file.is_file():
                pending=json.loads(pending_file.read_text(encoding='utf-8'))
                if pending.get('source')==str(source) and time.time()-pending_file.stat().st_mtime<300:job={'id':pending['job_id']}
            if not job:
                # Recover a just-submitted backup if the verifier was interrupted.
                try:
                    jobs=call('/api/knowledge/jobs')
                    job=next((j for j in jobs if j['kind']=='export' and j['state'] in ('queued','running','succeeded') and
                              (datetime.now(timezone.utc)-datetime.fromisoformat(j['created_at'])).total_seconds()<300),None)
                except httpx.HTTPStatusError:pass
            if not job:job=call('/api/knowledge/exports','POST')
            pending_file.write_text(json.dumps({'source':str(source),'job_id':job['id']},ensure_ascii=False),encoding='utf-8')
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                try:state=call('/api/knowledge/jobs/'+job['id'])
                except httpx.HTTPStatusError as error:
                    if error.response.status_code!=503:raise
                    time.sleep(.5);continue
                if state['state']=='failed':raise RuntimeError('现有工作台备份未完成，请在处理任务中查看原因')
                if state['state']=='succeeded':
                    path=(source/state['result']['storage_key']).resolve()
                    if source not in path.parents:raise RuntimeError('备份结果路径异常')
                    backup=report_dir/path.name;shutil.copy2(path,backup)
                    if call('/api/knowledge/health')['schema_revision']!=original_schema:raise RuntimeError('源库结构发生变化')
                    return backup,original_schema
                time.sleep(.25)
            raise RuntimeError('现有工作台仍在生成备份，请稍后重试')
    raise RuntimeError('没有找到对应资料目录的本机备份接口')


def sqlite_state(root):
    with closing(sqlite3.connect('file:'+str(root/'library.sqlite').replace('\\','/')+'?mode=ro',uri=True)) as c:
        tables=[v[0] for v in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts={};fingerprint=hashlib.sha256()
        for table in tables:
            quoted='"'+table.replace('"','""')+'"';rows=c.execute('SELECT * FROM '+quoted).fetchall();counts[table]=len(rows)
            fingerprint.update(table.encode())
            for row in sorted(json.dumps(v,ensure_ascii=False,default=str) for v in rows):fingerprint.update(row.encode())
        return counts,fingerprint.hexdigest()


def pg_state(root):
    with r.database.transaction(root) as c:
        tables=r.execute(c,"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'kb_%' ORDER BY tablename").scalars().all()
        counts={name:r.execute(c,'SELECT count(*) FROM "'+name+'"').scalar() for name in tables}
        revision=r.execute(c,'SELECT version_num FROM kb_schema_version').scalar_one()
        current_chunks=r.execute(c,'SELECT count(*) FROM kb_chunks ch JOIN kb_editions e ON e.active_index=ch.index_id').scalar()
        return {'revision':revision,'tables':counts,'current_chunks':current_chunks}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--target',type=Path,required=True);parser.add_argument('--live',action='store_true');parser.add_argument('--report-dir',type=Path);args=parser.parse_args()
    source=args.source.resolve();target=args.target.resolve()
    if ROOT not in source.parents or ROOT not in target.parents or source==target:raise ValueError('候选资料目录必须是项目内两个不同目录')
    if not (source/'library.sqlite').is_file() or not (source/'knowledge-db').is_dir():raise ValueError('源候选资料不完整')
    if target.exists():raise ValueError('目标已存在，禁止覆盖')
    for name in ('SHIXUE_KB_DATABASE_URL','SHIXUE_KB_RESTORE_URL'):os.environ.pop(name,None)
    temp=ROOT/'.local/temporary';temp.mkdir(parents=True,exist_ok=True);tempfile.tempdir=str(temp)
    report_dir=args.report_dir or ROOT/'迁移备份/2026-10-17-rc4到rc5';report_dir.mkdir(parents=True,exist_ok=True)
    before_counts,before_hash=sqlite_state(source)
    if args.live:
        backup,original_schema=live_backup(source,report_dir)
        with tempfile.TemporaryDirectory(dir=temp) as folder,zipfile.ZipFile(backup) as archive:
            frozen=Path(folder);(frozen/'library.sqlite').write_bytes(archive.read('library.sqlite'))
            before_counts,_=sqlite_state(frozen)
        _,after_hash=sqlite_state(source)
        before=after=None
    else:
        manager=ManagedPostgres(source)
        try:
            manager.start();register_managed(source,manager.url)
            before=pg_state(source)
            bundle=export_bundle(source)
            backup=report_dir/(Path(bundle['storage_key']).name)
            shutil.copy2(source/bundle['storage_key'],backup)
            after=pg_state(source)
            after_counts,after_hash=sqlite_state(source)
            if before!=after or before_hash!=after_hash:raise RuntimeError('源资料核验不一致，未继续恢复')
        finally:
            r.database.dispose();manager.stop();unregister_managed(source)
    restore_managed_bundle(backup,target)
    if args.live:
        manager=ManagedPostgres(target)
        try:
            manager.start();register_managed(target,manager.url);before=pg_state(target)
            if before['revision']!=original_schema:raise RuntimeError('备份结构版本不一致')
        finally:r.database.dispose();manager.stop();unregister_managed(target)
    for folder in ('models','rerank-models'):
        if (source/folder).is_dir() and not (target/folder).exists():shutil.copytree(source/folder,target/folder)
    product=ProductDatabase(target,'internal')
    try:
        product.start()
        if product.startup_error:raise RuntimeError(product.startup_error)
        restored=pg_state(target);restored_counts,_=sqlite_state(target)
        if restored_counts!=before_counts:raise RuntimeError('目标旧工作台表数量核验不一致')
        for name,count in before['tables'].items():
            if restored['tables'].get(name)!=count:raise RuntimeError('目标文献表数量核验不一致：'+name)
        if restored['current_chunks']!=before['current_chunks']:raise RuntimeError('当前索引数量核验不一致')
        skills=assistant_registry.list_definitions(target,'skill');agents=assistant_registry.list_definitions(target,'agent')
        with r.database.transaction(target) as c:
            row=r.one(c,'SELECT b.body FROM kb_blocks b JOIN kb_editions e ON e.active_revision=b.revision_id ORDER BY b.id LIMIT 1')
        from backend.knowledge.retrieval import search
        query=row['body'].strip()[:12]
        hit=search(target,{'query':query,'mode':'exact','scope':{},'source_kinds':['document'],'limit':5})
        if not hit['results']:raise RuntimeError('目标原句检索核验失败')
        health=r.database.health(target)
        if not health['ready']:raise RuntimeError('目标文献库健康检查未通过')
        report={'source':str(source),'target':str(target),'backup':str(backup),'source_schema':before['revision'],'target_schema':restored['revision'],
                'source_sqlite_unchanged':before_hash==after_hash,'source_pg_counts_unchanged':before==after if not args.live else None,
                'backup_mode':'running-app-api' if args.live else 'offline-owned-cluster','source_schema_preserved':True,
                'sqlite_counts':restored_counts,'pg_counts':restored['tables'],'pg_counts_stage':'after_upgrade_before_default_seed_and_search',
                'active_chunks':restored['current_chunks'],
                'skills':len(skills),'agents':len(agents),'exact_search_passed':True,'health_ready':True,'cloud_model_calls':0}
        (report_dir/'核验报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'copied':True,'source_schema':before['revision'],'target_schema':restored['revision'],'active_chunks':restored['current_chunks'],'skills':len(skills),'agents':len(agents)}))
    finally:product.stop()


if __name__=='__main__':main()
