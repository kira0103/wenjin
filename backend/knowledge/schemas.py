from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class EditionCreate(Strict):
    category_ids:list[UUID]=Field(default_factory=list,max_length=100)
    work_id:UUID|None=None
    title:str=Field(min_length=1,max_length=300)
    item_type:Literal['ancient','article','thesis','book','archive','other']='other'
    authors:list[str]=Field(default_factory=list,max_length=50)
    date_label:str=Field(default='',max_length=100)
    label:str=Field(default='',max_length=500)
    publisher:str=Field(default='',max_length=500)
    publication_year:int|None=Field(default=None,ge=1,le=9999)
    doi:str=Field(default='',max_length=300)
    source_note:str=Field(default='',max_length=5000)
    tags:list[str]=Field(default_factory=list,max_length=100)


class Source(Strict):
    anchor_id:UUID
    relation:Literal['citation','support','oppose','background']='citation'
    comment:str=Field(default='',max_length=2000)


class Note(Strict):
    title:str=Field(min_length=1,max_length=300)
    body:str=Field(default='',max_length=200000)
    note_type:Literal['reading','concept','question','judgment']='reading'
    status:Literal['draft','organized','review']='draft'
    tags:list[str]=Field(default_factory=list,max_length=100)
    version:int=Field(default=1,ge=1)
    sources:list[Source]=Field(default_factory=list,max_length=200)
    origin:Literal['human','ai']='human'


class Topic(Strict):
    title:str=Field(min_length=1,max_length=300)
    question:str=Field(default='',max_length=3000)
    description:str=Field(default='',max_length=20000)
    note_ids:list[UUID]=Field(default_factory=list,max_length=500)
    edition_ids:list[UUID]=Field(default_factory=list,max_length=500)


class Scope(Strict):
    asset_ids: list[UUID] = Field(default_factory=list,max_length=500)
    document_group_id: UUID|None = None
    document_uncategorized: bool = False
    document_path: str = Field(default='',max_length=2000)
    document_entry_ids: list[UUID] = Field(default_factory=list,max_length=500)
    category_id: UUID|None = None
    uncategorized: bool = False
    include_core: bool = False
    library_kind: Literal['all','ancient','papers'] = 'all'
    edition_ids:list[UUID]=Field(default_factory=list,max_length=500)
    project_id:str|None=Field(default=None,max_length=100)
    page_from:int|None=Field(default=None,ge=1)
    page_to:int|None=Field(default=None,ge=1)
    @model_validator(mode='after')
    def range_valid(self):
        if self.document_group_id and self.document_uncategorized:raise ValueError('不能同时指定资料分类和未分类')
        if self.document_path:
            from .document_library import relative_path
            relative_path(self.document_path)
            if not (self.document_group_id or self.document_uncategorized):raise ValueError('选择目录时必须同时指定所属分类')
        if self.category_id and self.uncategorized:raise ValueError('不能同时指定分类和未分类')
        if self.page_from and self.page_to and self.page_from>self.page_to:raise ValueError('起始页不能超过结束页')
        return self


class RetrievalOptions(Strict):
    rerank_enabled:bool=False
    rerank_candidates:int=Field(default=30,ge=5,le=80)
    rerank_min_score:float|None=Field(default=None,ge=-100,le=100,allow_inf_nan=False)
    parent_enabled:bool=True
    parent_characters:int=Field(default=1800,ge=256,le=4000)
    neighbor_blocks:int=Field(default=2,ge=0,le=4)


class RetrievalSettings(Strict):
    version:int=Field(default=0,ge=0)
    options:RetrievalOptions


class Search(Strict):
    query:str=Field(min_length=1,max_length=1000)
    mode:Literal['exact','keyword','semantic','hybrid']='hybrid'
    scope:Scope=Field(default_factory=Scope)
    source_kinds:list[Literal['document','note']]=Field(default_factory=lambda:['document'],max_length=2)
    include_commentary:bool=True
    limit:int=Field(default=20,ge=1,le=100)
    task:Literal['answer','outline','paragraph','overview']='answer'
    retrieval:RetrievalOptions|None=None

    @field_validator('query')
    @classmethod
    def nonempty_query(cls,value):
        if not value.strip():raise ValueError('查询不能为空白')
        return value.strip()


class ChunkConfig(Strict):
    profile:Literal['legacy','ancient','paper','general']='legacy'
    size:int=Field(default=100,ge=40,le=500)
    overlap:int=Field(default=15,ge=0,le=100)

    @model_validator(mode='after')
    def validate_overlap(self):
        if self.overlap>=self.size//2:raise ValueError('重叠长度必须小于分块长度的一半')
        return self


class ChunkPreview(Strict):
    text:str=Field(min_length=1,max_length=100000)
    config:ChunkConfig=Field(default_factory=ChunkConfig)


class Reindex(Strict):
    expected_revision:UUID|None=None
    config:ChunkConfig|None=None


class EvalSuite(Strict):
    name:str=Field(min_length=1,max_length=100)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls,value):
        if not value.strip():raise ValueError('评测集名称不能为空白')
        return value.strip()


class GoldSpan(Strict):
    block_id:UUID
    start:int=Field(ge=0)
    end:int=Field(ge=1)
    relevance:int=Field(default=1,ge=1,le=3)

    @model_validator(mode='after')
    def valid_span(self):
        if self.end<=self.start:raise ValueError('标注结束位置必须大于起始位置')
        return self


class EvalCase(Strict):
    search:Search
    expected:list[GoldSpan]=Field(default_factory=list,max_length=50)
    expect_empty:bool=False

    @model_validator(mode='after')
    def labeled(self):
        if self.expect_empty==bool(self.expected):raise ValueError('请选择原文证据，或明确标注应无结果')
        if self.search.source_kinds!=['document']:raise ValueError('评测仅支持原文文献')
        keys=[(item.block_id,item.start,item.end) for item in self.expected]
        if len(keys)!=len(set(keys)):raise ValueError('原文标注不能重复')
        return self


class Anchor(Strict):
    block_id:UUID
    start:int=Field(ge=0)
    end:int=Field(ge=1)


class PageLabel(Strict):
    printed_label:str=Field(default='',max_length=100)
    volume_label:str=Field(default='',max_length=100)
    leaf_label:str=Field(default='',max_length=100)
    side:Literal['','recto','verso']=''


class TextPage(Strict):
    source_block_id:UUID|None=None
    reviewed:bool|None=None
    asset_id:UUID
    page_index:int=Field(ge=1)
    body:str=Field(max_length=1000000)
    kind:Literal['body','commentary','footnote','table','reference']='body'
    section_path:str=Field(default='',max_length=500)


class Revision(Strict):
    expected_revision:UUID
    pages:list[TextPage]=Field(min_length=1,max_length=5000)


class ProjectLink(Strict):
    project_id:str=Field(min_length=1,max_length=100)
    roles:list[Literal['base','witness','citation','reference','auxiliary']]=Field(default_factory=lambda:['reference'],min_length=1,max_length=5)


class Migration(Strict):
    material_ids:list[str]=Field(default_factory=list,max_length=500)


class NoteRelation(Strict):
    target_id:UUID
    relation:Literal['related','support','oppose','extends']='related'
