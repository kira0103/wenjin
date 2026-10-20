from uuid import UUID
from typing import Literal
from fastapi import Query,HTTPException
from pydantic import Field,model_validator
from .schemas import Strict,Scope
from .document_library import DocumentLibrary,has_selection
from .document_imports import DocumentImports
from .worker import worker


class SourcePreview(Strict):
    mode:Literal['files','directory']
    paths:list[str]=Field(min_length=1,max_length=5000)


class StartImport(Strict):
    category_name:str=Field(default='',max_length=100)
    item_type:Literal['ancient','article','thesis']='article'


class GroupRename(Strict):
    name:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1)


class MoveDocuments(Strict):
    scope:Scope
    category_name:str=Field(default='',max_length=100)
    revision:int=Field(ge=0)
    @model_validator(mode='after')
    def selected(self):
        if not has_selection(self.scope.model_dump()):raise ValueError('请先选择文件、目录或分类')
        if self.scope.edition_ids or self.scope.project_id or self.scope.page_from or self.scope.page_to:raise ValueError('移动只接受文献目录范围')
        return self


def register(router,root,local_connection,now):
    @router.get('/document-groups')
    def groups():return DocumentLibrary(root()).groups()

    @router.patch('/document-groups/{identifier}')
    def rename(identifier:UUID,payload:GroupRename):return DocumentLibrary(root()).rename(identifier,payload.name,payload.version)

    @router.delete('/document-groups/{identifier}')
    def delete(identifier:UUID,version:int=Query(ge=1)):return DocumentLibrary(root()).delete(identifier,version)

    @router.get('/document-tree')
    def browse(group_id:UUID|None=None,uncategorized:bool=False,path:str='',query:str=Query('',max_length=300),
               kind:Literal['all','ancient','papers']='all',offset:int=Query(0,ge=0)):
        if group_id and uncategorized:raise HTTPException(400,'分类范围无效')
        return DocumentLibrary(root()).browse({'document_group_id':group_id,'document_uncategorized':uncategorized},path,query,kind,offset)

    @router.post('/document-entries/move')
    def move(payload:MoveDocuments):return DocumentLibrary(root()).move(payload.scope.model_dump(mode='json'),payload.category_name,payload.revision)

    @router.get('/document-entries/{identifier}')
    def entry(identifier:UUID):return DocumentLibrary(root()).entry(identifier)

    @router.post('/document-entries/{identifier}/project-source')
    def use_file(identifier:UUID,project_id:str):
        from .project_sources import ProjectSourceService
        item=DocumentLibrary(root()).entry(identifier)
        if not item['asset_id'] or not item['pages']:raise HTTPException(409,'此文件尚无正文，请等待解析后再选用')
        return ProjectSourceService(root(),local_connection).put(project_id,item['edition_id'],{'roles':['reference'],'mode':'ranges',
            'ranges':[{'asset_id':str(item['asset_id']),'page_from':1,'page_to':item['pages']}]})

    @router.post('/document-entries/{identifier}/working-input')
    def working_input(identifier:UUID,project_id:str):
        from .execution import WorkingInputService
        item=DocumentLibrary(root()).entry(identifier)
        if not item['asset_id'] or not item['pages']:raise HTTPException(409,'此文件尚无正文，请等待解析后再用于点校')
        # Explicit selection adds this file's complete range; it never grants its sibling files.
        use_file(identifier,project_id)
        frozen=WorkingInputService(root(),local_connection,now).catalog(project_id,item['edition_id'],
            [{'asset_id':str(item['asset_id']),'page_from':1,'page_to':item['pages']}])
        from .compat import alias
        return {**frozen,'navigation_material_id':alias(item['edition_id'],item['asset_id'])}

    @router.post('/document-imports/preview')
    def preview(payload:SourcePreview):return DocumentImports(root()).preview(payload.mode,payload.paths)

    @router.post('/document-imports/{identifier}/start')
    def start(identifier:UUID,payload:StartImport):
        result=DocumentImports(root()).start(identifier,payload.category_name,payload.item_type)
        worker.start(root());return result

    @router.get('/document-imports/{identifier}')
    def get_import(identifier:UUID):return DocumentImports(root()).get(identifier)

    @router.post('/document-imports/{identifier}/retry')
    def retry(identifier:UUID):
        result=DocumentImports(root()).start(identifier,'','article',True);worker.start(root());return result
