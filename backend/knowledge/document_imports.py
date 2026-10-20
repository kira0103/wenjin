"""Durable local folder/file imports. Previews keep paths, never load files in the renderer."""
import hashlib
import os
from pathlib import Path
from fastapi import HTTPException
from . import repository as r
from .document_library import DocumentLibrary,relative_path

EXTENSIONS={'.pdf','.docx','.doc','.txt','.md','.png','.jpg','.jpeg','.tif','.tiff'}
MAX_FILE=250*1024*1024


def plain_file(path,root=None):
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise HTTPException(400,'导入不跟随符号链接或目录联接')
    actual=path.resolve()
    if root and actual!=root and root not in actual.parents:raise HTTPException(400,'文件超出所选目录')
    if not actual.is_file():raise HTTPException(400,'文件已不存在，请重新选择')
    return actual


def scan(mode,paths):
    result=[];ignored=0;size=0
    if mode=='directory':
        if len(paths)!=1:raise HTTPException(400,'请选择一个目录')
        raw=Path(paths[0])
        if raw.is_symlink() or getattr(raw,'is_junction',lambda:False)():raise HTTPException(400,'请选择实际目录')
        root=raw.resolve()
        if not root.is_dir():raise HTTPException(400,'目录不存在')
        def files():
            nonlocal ignored
            for folder,dirs,names in os.walk(root,followlinks=False):
                dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not (Path(folder)/d).is_symlink() and not getattr(Path(folder)/d,'is_junction',lambda:False)())
                for name in sorted(names):
                    p=Path(folder)/name
                    if p.suffix.lower() not in EXTENSIONS:ignored+=1;continue
                    yield p,p.relative_to(root).as_posix()
        chosen=files()
    else:
        if not paths or len(paths)>5000:raise HTTPException(400,'每批请选择 1–5000 个文件')
        root=None;chosen=((Path(p),Path(p).name) for p in paths)
    seen=set()
    for source,name in chosen:
        path=plain_file(source,root)
        if path.suffix.lower() not in EXTENSIONS:ignored+=1;continue
        if str(path).casefold() in seen:continue
        seen.add(str(path).casefold())
        info=path.stat()
        if not info.st_size or info.st_size>MAX_FILE:raise HTTPException(400,'文件为空或超过 250 MB：'+name)
        size+=info.st_size
        if len(result)>=50000 or size>20*1024**3:raise HTTPException(400,'每批最多 50,000 个文件、合计 20 GB')
        result.append({'path':str(path),'relative_path':relative_path(name),'size':info.st_size,'mtime_ns':info.st_mtime_ns,'state':'pending'})
    if not result:raise HTTPException(400,'没有找到支持的文献文件')
    return result,ignored,size,root


class DocumentImports:
    def __init__(self,root):self.root=root

    def preview(self,mode,paths):
        items,ignored,size,folder=scan(mode,paths)
        existing=None
        with r.database.transaction(self.root) as db:
            if folder:
                existing=next((c for c in r.rows(db,'SELECT id,source_path,slug FROM kb_corpora') if Path(c['source_path']).resolve()==folder),None)
            from .core_corpus import CATEGORIES
            core_layout=bool(folder and any((folder/c).is_dir() for c in CATEGORIES) and any(i['relative_path'].split('/')[0] in CATEGORIES and i['path'].endswith('.md') for i in items))
            if core_layout or existing:
                originals=[i for i in items if i['relative_path'].split('/')[0] in CATEGORIES and i['path'].lower().endswith('.md')]
                ignored+=len(items)-len(originals);items=originals;size=sum(i['size'] for i in items)
            selection={'mode':mode,'root':str(folder) if folder else '', 'label':folder.name if folder else '所选文件',
                       'existing_corpus':str(existing['id']) if existing else None,'core_layout':core_layout,'ignored':ignored,'bytes':size}
            identifier=r.uid()
            r.execute(db,"INSERT INTO kb_document_imports(id,workspace_key,selection,manifest) VALUES(:id,current_setting('shixue.workspace'),CAST(:selection AS jsonb),CAST(:manifest AS jsonb))",id=identifier,selection=r.j(selection),manifest=r.j(items))
        return {'id':identifier,'mode':mode,'source_label':selection['label'],'files':len(items),'bytes':size,'ignored':ignored,
                'reuse_existing':bool(existing),'preview':[{'relative_path':i['relative_path'],'size':i['size']} for i in items[:100]]}

    def get(self,identifier):
        with r.database.transaction(self.root) as db:
            item=r.one(db,"SELECT * FROM kb_document_imports WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)
        return {'id':item['id'],'state':item['state'],'group_id':item['group_id'],'job_id':item['job_id'],'result':item['result'],
                'files':len(item['manifest']),'failures':[{'relative_path':i['relative_path'],'error':i.get('error','')} for i in item['manifest'] if i['state']=='failed'][:100]}

    def start(self,identifier,category_name,item_type,retry=False):
        library=DocumentLibrary(self.root)
        with r.database.transaction(self.root) as db:
            library.state(db)
            batch=r.one(db,"SELECT * FROM kb_document_imports WHERE id=:id AND workspace_key=current_setting('shixue.workspace') FOR UPDATE",id=identifier)
            if batch['state'] in ('queued','running') or batch['state']=='succeeded':return self._response(batch)
            if batch['state']!='preview' and not retry:raise HTTPException(409,'请通过重试入口继续未完成导入')
            if retry and batch['state'] not in ('partial','failed','cancelled'):raise HTTPException(409,'此批次没有需要重试的导入')
            group=batch['group_id'] if retry else library.group(db,category_name)
            job=r.queue(db,'document_batch',{'batch_id':str(identifier)})
            batch=r.one(db,"UPDATE kb_document_imports SET state='queued',group_id=:group,item_type=:kind,job_id=:job WHERE id=:id RETURNING *",group=group,kind=batch['item_type'] if retry else item_type,job=job['id'],id=identifier)
            library.advance(db)
            return self._response(batch)

    def _response(self,batch):return {'id':batch['id'],'group_id':batch['group_id'],'job_id':batch['job_id'],'state':batch['state']}


def process_batch(root,payload,check,progress):
    library=DocumentLibrary(root);identifier=payload['batch_id']
    with r.database.transaction(root) as db:
        batch=r.one(db,"SELECT * FROM kb_document_imports WHERE id=:id AND workspace_key=current_setting('shixue.workspace')",id=identifier)
        r.execute(db,"UPDATE kb_document_imports SET state='running' WHERE id=:id",id=identifier)
    selection=batch['selection'];items=batch['manifest'];group=batch['group_id'];result={}
    try:
        if selection['existing_corpus'] or selection['core_layout']:
            corpus=selection['existing_corpus']
            if not corpus:
                from .core_corpus import process_import
                imported=process_import(root,{'source_path':selection['root'],'slug':'directory-'+hashlib.sha256(selection['root'].casefold().encode()).hexdigest()[:24],
                                             'name':selection['label'],'_job_id':payload.get('_job_id'),'_job_owner':payload.get('_job_owner')},check,progress)
                corpus=imported['corpus_id']
                if imported.get('failed_books'):raise HTTPException(409,'部分古籍导入失败，请检查任务后重试')
            check()
            with r.database.transaction(root) as db:
                library.sync(db,core_groups={str(corpus):group})
                moved=r.execute(db,"UPDATE kb_document_entries de SET group_id=:group,version=version+1 FROM kb_core_books cb WHERE cb.edition_id=de.edition_id AND cb.corpus_id=:corpus AND de.workspace_key=current_setting('shixue.workspace') AND de.origin_key LIKE 'core:%'",group=group,corpus=corpus).rowcount
                library.advance(db)
            result={'files':moved,'succeeded':moved,'failed':0,'reused':bool(selection['existing_corpus']),'originals_copied':not bool(selection['existing_corpus'])}
        else:
            from .catalog import CatalogService
            catalog=CatalogService(root)
            for n,item in enumerate(items):
                check();progress('document_import',int(n*95/max(1,len(items))))
                if item['state']=='succeeded':continue
                try:
                    path=plain_file(Path(item['path']),Path(selection['root']) if selection['root'] else None)
                    info=path.stat()
                    if info.st_size!=item['size'] or info.st_mtime_ns!=item['mtime_ns']:raise HTTPException(409,'文件在预览后已变化，请重新选择')
                    with path.open('rb') as source:data=source.read(MAX_FILE+1)
                    if len(data)!=item['size'] or len(data)>MAX_FILE:raise HTTPException(409,'文件读取过程中发生变化，请重新选择')
                    origin='upload:'+hashlib.sha256((str(path).casefold()+'\n'+hashlib.sha256(data).hexdigest()).encode()).hexdigest()
                    with r.database.transaction(root) as db:
                        library.state(db)
                        existing=r.execute(db,"SELECT * FROM kb_document_entries WHERE workspace_key=current_setting('shixue.workspace') AND origin_key=:origin",origin=origin).mappings().first()
                    if existing:
                        with r.database.transaction(root) as db:
                            library.state(db)
                            r.execute(db,'UPDATE kb_document_entries SET group_id=:group,version=version+1 WHERE id=:id',group=group,id=existing['id']);library.advance(db)
                        item.update(state='succeeded',entry_id=str(existing['id']),reused=True,error='')
                    else:
                        if not item.get('edition_id'):
                            document=catalog.create({'title':path.stem[:300],'item_type':batch['item_type'],'source_note':'原始目录：'+item['relative_path']})
                            item['edition_id']=str(document['id'])
                            with r.database.transaction(root) as db:r.execute(db,'UPDATE kb_document_imports SET manifest=CAST(:items AS jsonb) WHERE id=:id',items=r.j(items),id=identifier)
                        attached=catalog.import_file(item['edition_id'],path.name,data)
                        with r.database.transaction(root) as db:
                            library.state(db)
                            # Replace the compatibility reference, keeping the same edition and physical asset.
                            r.execute(db,"DELETE FROM kb_document_entries WHERE workspace_key=current_setting('shixue.workspace') AND edition_id=:edition AND (asset_id IS NULL OR origin_key LIKE 'catalog:%')",edition=item['edition_id'])
                            entry=r.one(db,"INSERT INTO kb_document_entries(id,workspace_key,group_id,edition_id,asset_id,relative_path,origin_key) VALUES(:id,current_setting('shixue.workspace'),:group,:edition,:asset,:path,:origin) ON CONFLICT(workspace_key,origin_key) DO UPDATE SET group_id=excluded.group_id RETURNING id",id=r.uid(),group=group,edition=item['edition_id'],asset=attached['asset_id'],path=item['relative_path'],origin=origin)
                            library.advance(db)
                        item.update(state='succeeded',entry_id=str(entry['id']),error='')
                except HTTPException as error:item.update(state='failed',error=str(error.detail)[:300])
                except (OSError,ValueError):item.update(state='failed',error='文件读取或导入失败，请检查原件后重试')
                with r.database.transaction(root) as db:r.execute(db,'UPDATE kb_document_imports SET manifest=CAST(:items AS jsonb) WHERE id=:id',items=r.j(items),id=identifier)
            result={'files':len(items),'succeeded':sum(i['state']=='succeeded' for i in items),'failed':sum(i['state']=='failed' for i in items),'reused':sum(bool(i.get('reused')) for i in items)}
        check()
        with r.database.transaction(root) as db:r.execute(db,'UPDATE kb_document_imports SET state=:state,result=CAST(:result AS jsonb) WHERE id=:id',state='partial' if result.get('failed') else 'succeeded',result=r.j(result),id=identifier)
        return {**result,'batch_id':str(identifier)}
    except BaseException:
        with r.database.transaction(root) as db:r.execute(db,"UPDATE kb_document_imports SET state='failed',manifest=CAST(:items AS jsonb) WHERE id=:id",items=r.j(items),id=identifier)
        raise
