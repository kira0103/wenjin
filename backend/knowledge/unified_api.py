"""v4.1 service endpoints; historical routes are retired at global activation."""
from uuid import UUID
from typing import Literal

from fastapi import Query, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .catalog import CatalogService
from .execution import WorkingInputService
from .project_sources import ProjectSourceService
from .schemas import Strict, EditionCreate
from .worker import worker
from .literature_categories import LiteratureCategories


class CategoryWrite(Strict):
    name: str = Field(min_length=1,max_length=80)
    parent_id: UUID|None = None
    position: int = Field(default=0,ge=0,le=1000000)
    revision: int = Field(ge=0)


class CategoryAssignment(Strict):
    work_ids: list[UUID] = Field(min_length=1,max_length=500)
    category_ids: list[UUID] = Field(default_factory=list,max_length=100)
    mode: Literal['add','remove','replace'] = 'add'
    revision: int = Field(ge=0)


class MembershipRead(Strict):
    work_ids: list[UUID] = Field(min_length=1,max_length=500)


class PageRange(Strict):
    asset_id: UUID
    page_from: int = Field(ge=1)
    page_to: int = Field(ge=1)


class SourceSelection(Strict):
    roles: list[str] = Field(default_factory=lambda: ['reference'], max_length=5)
    mode: str = 'edition'
    ranges: list[PageRange] = Field(default_factory=list, max_length=500)
    witness_label: str = Field(default='', max_length=100)
    scope_note: str = Field(default='', max_length=2000)
    version: int | None = Field(default=None, ge=1)


class FreezeInput(Strict):
    project_id: str = Field(min_length=1, max_length=100)
    edition_id: UUID
    ranges: list[PageRange] | None = Field(default=None, max_length=500)


class SourceUpdate(SourceSelection):
    version: int = Field(ge=1)


class WorkUpdate(Strict):
    title: str = Field(min_length=1, max_length=300)
    item_type: str = 'other'
    authors: list[str] = Field(default_factory=list, max_length=50)
    date_label: str = Field(default='', max_length=100)
    version: int = Field(ge=1)


class EditionUpdate(Strict):
    label: str = Field(default='', max_length=500)
    publisher: str = Field(default='', max_length=500)
    publication_year: int | None = Field(default=None, ge=1, le=9999)
    doi: str = Field(default='', max_length=300)
    source_note: str = Field(default='', max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    version: int = Field(ge=1)


def register(router, root, local_connection, now):
    from .document_api import register as register_documents
    register_documents(router,root,local_connection,now)
    @router.get('/literature-categories')
    def category_tree():
        return LiteratureCategories(root()).tree()

    @router.post('/literature-categories')
    def create_category(payload: CategoryWrite):
        return LiteratureCategories(root()).save(payload.model_dump(mode='json'))

    @router.patch('/literature-categories/{identifier}')
    def update_category(identifier: UUID,payload: CategoryWrite):
        return LiteratureCategories(root()).save(payload.model_dump(mode='json'),str(identifier))

    @router.delete('/literature-categories/{identifier}')
    def delete_category(identifier: UUID,revision: int=Query(ge=0),subtree: bool=False):
        return LiteratureCategories(root()).delete(str(identifier),revision,subtree)

    @router.post('/literature-memberships/read')
    def read_memberships(payload: MembershipRead):
        return LiteratureCategories(root()).memberships(payload.work_ids)

    @router.post('/literature-memberships')
    def assign_categories(payload: CategoryAssignment):
        return LiteratureCategories(root()).assign(payload.model_dump(mode='json'))

    def catalog():
        return CatalogService(root())

    def projects():
        return ProjectSourceService(root(), local_connection)

    def inputs():
        return WorkingInputService(root(), local_connection, now)

    @router.get('/catalog')
    def list_catalog(query: str = '', project_id: str | None = None,
                     cursor: str | None = None, limit: int = Query(100, ge=1, le=200),
                     kind: Literal['all','ancient','papers','unclassified']='all',
                     source: Literal['all','core','personal']='all', category: str='', include_core: bool=False,
                     year:str='',tag:str='',state:Literal['all','metadata','text','ready']='all',
                     category_id: UUID|None=None,uncategorized: bool=False):
        selected = [item['edition_id'] for item in projects().list(project_id)] if project_id else None
        return catalog().list(query=query, cursor=cursor, limit=limit, edition_ids=selected,
                              kind=kind,source=source,category=category,include_core=include_core,year=year,tag=tag,state=state,
                              category_id=category_id,uncategorized=uncategorized)

    @router.post('/catalog')
    def create_catalog(payload: EditionCreate):
        return catalog().create(payload.model_dump(mode='json'))

    @router.patch('/catalog/works/{work_id}')
    def update_work(work_id: UUID, payload: WorkUpdate):
        return catalog().update_work(work_id, payload.model_dump(mode='json'), payload.version)

    @router.patch('/catalog/editions/{edition_id}')
    def update_edition(edition_id: UUID, payload: EditionUpdate):
        return catalog().update_edition(edition_id, payload.model_dump(mode='json'), payload.version)

    @router.post('/catalog/editions/{edition_id}/imports')
    async def import_original(edition_id: UUID, request: Request, filename: str = Query(min_length=1, max_length=220)):
        data = bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data) > 250 * 1024 * 1024:
                from fastapi import HTTPException
                raise HTTPException(413, '原件超过 250 MB')
        job = await run_in_threadpool(catalog().import_file, edition_id, filename, bytes(data))
        if not job['duplicate']:
            worker.start(root())
        return job

    @router.get('/projects/{project_id}/sources')
    def list_sources(project_id: str):
        return projects().list(project_id)

    @router.put('/projects/{project_id}/sources/{edition_id}')
    def put_source(project_id: str, edition_id: UUID, payload: SourceSelection):
        return projects().put(project_id, edition_id, payload.model_dump(mode='json'))

    @router.delete('/projects/{project_id}/sources/{source_id}')
    def remove_source(project_id: str, source_id: UUID):
        projects().remove(project_id, source_id)
        return {'removed': True, 'frozen_work_retained': True}

    @router.patch('/projects/{project_id}/sources/{source_id}')
    def update_source(project_id:str,source_id:UUID,payload:SourceUpdate):
        return projects().update(project_id,source_id,payload.model_dump(mode='json'))

    @router.post('/working-inputs')
    def freeze_input(payload: FreezeInput):
        return inputs().catalog(payload.project_id, payload.edition_id,
                                [item.model_dump(mode='json') for item in payload.ranges] if payload.ranges is not None else None)

    @router.get('/working-inputs/{identifier}')
    def get_input(identifier: UUID, project_id: str):
        return inputs().get(str(identifier), project_id)

    @router.post('/reference-candidates/{identifier}/add-to-catalog')
    def add_candidate(identifier: str):
        return catalog().add_candidate(identifier)
