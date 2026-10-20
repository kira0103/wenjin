"""Workspace-scoped, immutable instruction packages. No imported code is executed."""
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

import yaml
from fastapi import HTTPException
from pydantic import Field, model_validator

from . import repository as r
from .schemas import Strict

TOOLS={'search','read','list'}
MAX_PACKAGE=256*1024


class Definition(Strict):
    slug:str=Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$',max_length=64)
    name:str=Field(min_length=1,max_length=100)
    description:str=Field(min_length=1,max_length=1024)
    instructions:str=Field(min_length=1,max_length=12000)
    tools:list[Literal['search','read','list']]=Field(default_factory=lambda:['search','read','list'],max_length=3)
    skill_version_ids:list[UUID]=Field(default_factory=list,max_length=6)
    files:dict[str,str]=Field(default_factory=dict,max_length=30)
    edit_version:int=Field(default=0,ge=0)

    @model_validator(mode='after')
    def valid(self):
        if not self.name.strip() or not self.instructions.strip() or not self.description.strip():raise ValueError('名称、描述和方法不能为空')
        for name,body in self.files.items():validate_file(name,body)
        if len(r.j(self.model_dump(mode='json')).encode())>MAX_PACKAGE:raise ValueError('技能内容超过 256 KB')
        return self


def validate_file(name,body):
    path=PurePosixPath(name)
    if (not name or name in {'SKILL.md','manifest.json'} or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or
        name!=str(path) or len(name)>180 or path.suffix.lower() not in {'.md','.txt','.json','.yaml','.yml'} or
        any(part.startswith('.') for part in path.parts) or 'scripts' in path.parts):
        raise ValueError('仅支持技能目录内的 Markdown、文本和 JSON/YAML 参考文件，不支持脚本或越界路径')
    if not isinstance(body,str) or len(body.encode())>64000:raise ValueError('单个参考文件超过 64 KB')


def digest(content):return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


BUILTINS=[
 ('trace-classic','古籍引文追溯','追溯古籍引文的原文、版本与上下文。','区分本书记载与转引。保留篇卷、版本及可读取的页段。每项结论关联实际读取的证据，无法定位时说明不足。'),
 ('compare-variants','异文比较','比较所选不同版本中的文字差异。','分别阅读各版本原文，列出对应位置、字面差异及解释。保持底本原字，标点建议与改字候选分别说明。不声称已阅读未提供的版本。'),
 ('paper-argument','论文论证提取','分析论文的问题、材料、方法与论证局限。','依次提取研究问题、作者主张、使用材料、推理步骤、研究方法与局限。区分作者意见和你自己的解释。每项主要主张对应原文出处。'),
 ('compare-literature','跨文献观点比较','对照古籍记载与论文解释。','按共同问题组织比较，分别标出原始记载、论文观点与系统推论。保留分歧和反例，不强行统一观点；资料缺失时直接指出。'),
 ('audit-evidence','论断与证据复核','检查论断是否由引文支持。','逐项列出论断、支持证据、相反材料和不足。上游助手摘要只是待审意见，必须回到给定原文核验。把支持不足和矛盾单列，不能因多个助手一致就判为正确。'),
 ('cited-outline','带引文的研究提纲','根据证据形成可追溯的研究提纲或草稿。','先组织研究问题与层次，再使用已核验的原文形成提纲或草稿。保留资料不足与争议，不新增无依据的事实。每项有依据的论断附给定证据编号。')]
AGENTS=[
 ('research-coordinator','研究协调者','拆解研究问题并说明证据缺口。',['compare-literature']),
 ('classic-researcher','古籍证据助手','围绕问题检索古籍记载，保留版本及原字。',['trace-classic','compare-variants']),
 ('paper-researcher','论文分析助手','围绕问题分析论文材料、观点与方法。',['paper-argument']),
 ('evidence-reviewer','证据复核助手','审查上游论断与原文的支持关系，优先标出矛盾和不足。',['audit-evidence']),
 ('research-writer','综合写作助手','综合已交接的原文证据和复核意见，形成待人工审核的成果。',['compare-literature','cited-outline'])]


def row(connection,identifier,kind=None,lock=False):
    return r.one(connection,"SELECT * FROM kb_assistant_definitions WHERE id=:id AND workspace_key=current_setting('shixue.workspace')"+
                 (' AND kind=:kind' if kind else '')+(' FOR UPDATE' if lock else ''),id=identifier,kind=kind)


def release(connection,identifier,kind=None,new_run=True,lock=False):
    item=r.one(connection,"SELECT v.*,d.workspace_key,d.kind,d.enabled,d.slug,d.id definition_id FROM kb_assistant_versions v "
               "JOIN kb_assistant_definitions d ON d.id=v.definition_id WHERE v.id=:id AND d.workspace_key=current_setting('shixue.workspace')"+(' FOR SHARE OF v' if lock else ''),id=identifier)
    if kind and item['kind']!=kind:raise HTTPException(400,'版本类型不匹配')
    if item['revoked'] or new_run and not item['enabled']:raise HTTPException(409,'所选角色或技能已停用或撤销，请重新选择')
    if digest(item['content'])!=item['sha256']:raise HTTPException(409,'技能或角色内容校验失败')
    return item


def insert(connection,kind,payload):
    identifier=r.uid();content=payload.model_dump(mode='json',exclude={'edit_version'})
    if kind=='skill' and content['skill_version_ids']:raise HTTPException(400,'技能不能递归绑定其他技能')
    for identifier_ in content['skill_version_ids']:release(connection,identifier_,'skill')
    r.execute(connection,"INSERT INTO kb_assistant_definitions(id,workspace_key,kind,slug,draft) "
              "VALUES(:id,current_setting('shixue.workspace'),:k,:slug,CAST(:draft AS jsonb))",id=identifier,k=kind,slug=payload.slug,draft=r.j(content))
    return identifier


def publish(connection,identifier,expected,activate=True):
    item=row(connection,identifier,lock=True)
    if item['edit_version']!=expected:raise HTTPException(409,'草稿已变化，请刷新后发布')
    payload=Definition.model_validate(item['draft'])
    content=payload.model_dump(mode='json',exclude={'edit_version'})
    if item['kind']=='skill' and content['skill_version_ids']:raise HTTPException(400,'技能不能递归绑定其他技能')
    for version_id in content['skill_version_ids']:release(connection,version_id,'skill')
    current=r.execute(connection,'SELECT max(number) FROM kb_assistant_versions WHERE definition_id=:id',id=identifier).scalar() or 0
    sha=digest(content)
    previous=r.execute(connection,'SELECT id FROM kb_assistant_versions WHERE definition_id=:id AND sha256=:h AND NOT revoked ORDER BY number DESC LIMIT 1',id=identifier,h=sha).scalar()
    version_id=str(previous) if previous else r.uid()
    if not previous:r.execute(connection,'INSERT INTO kb_assistant_versions(id,definition_id,number,content,sha256) VALUES(:id,:d,:n,CAST(:c AS jsonb),:h)',
                             id=version_id,d=identifier,n=current+1,c=r.j(content),h=sha)
    if activate:r.execute(connection,'UPDATE kb_assistant_definitions SET active_version=:v,updated_at=now() WHERE id=:id',id=identifier,v=version_id)
    return version_id


def ensure_defaults(connection):
    # Same lock for every initializer; only seed an absent identity, never overwrite user edits.
    r.execute(connection,"SELECT pg_advisory_xact_lock(hashtext(current_setting('shixue.workspace')),931)")
    ids={}
    for slug,name,description,instructions in BUILTINS:
        current=r.execute(connection,"SELECT * FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace') AND kind='skill' AND slug=:s",s=slug).mappings().first()
        if current:ids[slug]=str(current['active_version']) if current['active_version'] else None
        else:
            identifier=insert(connection,'skill',Definition(slug=slug,name=name,description=description,instructions=instructions))
            ids[slug]=publish(connection,identifier,1)
    for slug,name,instructions,skills in AGENTS:
        exists=r.execute(connection,"SELECT 1 FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace') AND kind='agent' AND slug=:s",s=slug).scalar()
        if not exists:
            # A disabled built-in skill must not block the management page.
            bindings=[ids[s] for s in skills if ids.get(s) and r.execute(connection,'SELECT d.enabled AND NOT v.revoked FROM kb_assistant_versions v JOIN kb_assistant_definitions d ON d.id=v.definition_id WHERE v.id=:id',id=ids[s]).scalar()]
            identifier=insert(connection,'agent',Definition(slug=slug,name=name,description=instructions,instructions=instructions,skill_version_ids=bindings))
            publish(connection,identifier,1)


def list_definitions(root,kind):
    with r.database.transaction(root) as connection:
        ensure_defaults(connection)
        return r.rows(connection,"SELECT d.*,v.number active_number FROM kb_assistant_definitions d LEFT JOIN kb_assistant_versions v ON v.id=d.active_version "
                      "WHERE d.workspace_key=current_setting('shixue.workspace') AND kind=:k ORDER BY d.created_at,d.slug",k=kind)


def detail(root,identifier,kind=None):
    with r.database.transaction(root) as connection:
        item=row(connection,identifier,kind)
        item['versions']=r.rows(connection,'SELECT id,number,sha256,revoked,created_at,content FROM kb_assistant_versions WHERE definition_id=:id ORDER BY number DESC',id=identifier)
        item['used_by']=r.rows(connection,"SELECT DISTINCT d.id,d.draft->>'name' name FROM kb_assistant_definitions d JOIN kb_assistant_versions v ON v.definition_id=d.id "
                     "WHERE d.workspace_key=current_setting('shixue.workspace') AND d.kind='agent' AND EXISTS (SELECT 1 FROM kb_assistant_versions s "
                     "WHERE s.definition_id=:id AND v.content->'skill_version_ids' @> jsonb_build_array(s.id::text))",id=identifier)
        return item


def freeze(connection,agent_version_id=None,skill_version_ids=None):
    selected=list(skill_version_ids or [])
    agent=release(connection,agent_version_id,'agent') if agent_version_id else None
    if agent:
        bound=agent['content']['skill_version_ids']
        if selected and not set(map(str,selected)).issubset(bound):raise HTTPException(403,'只能选择当前角色版本绑定的技能')
        selected=selected or bound
    skills=[release(connection,identifier,'skill') for identifier in dict.fromkeys(map(str,selected))]
    tools=set(agent['content']['tools']) if agent else TOOLS.copy()
    if skills:tools.intersection_update(set().union(*(set(s['content']['tools']) for s in skills)))
    return {'agent':{'id':str(agent['id']),'name':agent['content']['name'],'sha256':agent['sha256'],'instructions':agent['content']['instructions'],'files':agent['content']['files']} if agent else None,
            'skills':[{'id':str(s['id']),'name':s['content']['name'],'sha256':s['sha256'],'instructions':s['content']['instructions'],'files':s['content']['files']} for s in skills],
            'tools':sorted(tools)}


def validate_frozen(connection,profile):
    if not profile:return
    values=([profile['agent']] if profile.get('agent') else [])+profile.get('skills',[])
    for value in values:
        current=release(connection,value['id'],new_run=False,lock=True)
        if current['sha256']!=value['sha256']:raise HTTPException(409,'运行绑定的角色或技能版本已变化')


def method_text(profile):
    if not profile:return ''
    parts=[]
    if profile.get('agent'):parts.append('角色：'+profile['agent']['name']+'\n'+profile['agent']['instructions'])
    for path,content in (profile.get('agent') or {}).get('files',{}).items():
        if path.startswith('references/'):parts.append('角色参考 '+path+'\n'+content)
    for s in profile.get('skills',[]):
        parts.append('技能：'+s['name']+'\n'+s['instructions'])
        for path,content in s.get('files',{}).items():
            if path.startswith('references/'):parts.append('方法参考 '+path+'\n'+content)
    return '\n\n'.join(parts)


def export_package(content):
    data=Definition.model_validate(content).model_dump(mode='json',exclude={'edit_version'})
    metadata={'name':data['slug'],'description':data['description']}
    skill='---\n'+yaml.safe_dump(metadata,allow_unicode=True,sort_keys=False)+'---\n\n'+data['instructions']+'\n'
    files={'SKILL.md':skill,'manifest.json':r.j({'schema_version':1,'display_name':data['name'],'requested_tools':data['tools']}) ,**data['files']}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,body in files.items():archive.writestr(data['slug']+'/'+name,body)
    return buffer.getvalue()


def import_package(data):
    if not data or len(data)>MAX_PACKAGE:raise HTTPException(400,'技能包为空或超过 256 KB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            items=archive.infolist()
            if len(items)>35 or sum(v.file_size for v in items)>MAX_PACKAGE:raise ValueError('技能包展开后超过限制')
            files={}
            for item in items:
                path=PurePosixPath(item.filename)
                if '\\' in item.filename or ':' in item.filename or path.is_absolute() or '..' in path.parts or stat.S_ISLNK(item.external_attr>>16):raise ValueError('技能包包含非法路径或链接')
                if item.is_dir():continue
                if item.filename in files:raise ValueError('技能包包含重复文件')
                if item.file_size>64000:raise ValueError('单个文件超过 64 KB')
                files[item.filename]=archive.read(item).decode('utf-8-sig')
        skill_names=[name for name in files if name=='SKILL.md' or name.endswith('/SKILL.md')]
        if len(skill_names)!=1:raise ValueError('技能包必须包含一个 SKILL.md')
        skill_path=skill_names[0];prefix=skill_path[:-len('SKILL.md')]
        if any(not name.startswith(prefix) for name in files):raise ValueError('技能包文件必须位于同一目录')
        files={name[len(prefix):]:body for name,body in files.items()}
        raw=files.pop('SKILL.md');match=re.match(r'^---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)$',raw,re.S)
        if not match:raise ValueError('SKILL.md 缺少 YAML 元数据')
        header=yaml.safe_load(match[1]);manifest=json.loads(files.pop('manifest.json','{}'))
        if not isinstance(header,dict) or not isinstance(manifest,dict):raise ValueError('技能清单格式错误')
        if manifest.get('execution_mode','instructions')!='instructions':raise ValueError('当前仅支持方法型技能')
        if manifest.get('schema_version',1)!=1:raise ValueError('技能包协议版本不兼容')
        if manifest.keys()-{'schema_version','version','display_name','requested_tools','execution_mode'}:raise ValueError('技能包包含当前不支持的清单字段，请转换后导入')
        slug=header.get('name','')
        if prefix and prefix.rstrip('/').split('/')[-1]!=slug:raise ValueError('技能名称必须与目录名称相同')
        return Definition(slug=slug,name=manifest.get('display_name',slug),description=header.get('description',''),instructions=match[2].strip(),
                          tools=manifest.get('requested_tools',['search','read','list']),files=files)
    except Exception as error:
        if isinstance(error,HTTPException):raise
        raise HTTPException(400,'技能包校验失败：'+str(error)[:200]) from None
