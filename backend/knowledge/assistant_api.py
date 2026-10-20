"""Authenticated role/skill lifecycle, package exchange and isolated skill trials."""
import base64
import io
import zipfile
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import Field

from . import assistant_registry as registry, repository as r
from .schemas import Strict
from .research_assistant import ResearchRequest,create
from .worker import worker


class VersionCheck(Strict):
    edit_version:int=Field(ge=1)


class State(Strict):
    enabled:bool


class Activate(Strict):
    version_id:UUID


class Import(Strict):
    data_base64:str=Field(default='',max_length=360000)
    files:dict[str,str]=Field(default_factory=dict,max_length=35)


class Trial(ResearchRequest):
    version_id:UUID|None=None
    edit_version:int=Field(default=0,ge=0)


def register(router,root,resolve_scope,model_settings):
    def routes(path,kind):
        @router.get(path)
        def listing():return registry.list_definitions(root(),kind)

        @router.post(path)
        def add(payload:registry.Definition):
            with r.database.transaction(root()) as c:
                if r.execute(c,"SELECT 1 FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace') AND kind=:k AND slug=:s",k=kind,s=payload.slug).scalar():raise HTTPException(409,'标识已存在，请修改标识或编辑原项目')
                identifier=registry.insert(c,kind,payload)
            return registry.detail(root(),identifier,kind)

        @router.get(path+'/{identifier}')
        def detail(identifier:UUID):return registry.detail(root(),identifier,kind)

        @router.put(path+'/{identifier}')
        def edit(identifier:UUID,payload:registry.Definition):
            with r.database.transaction(root()) as c:
                current=registry.row(c,identifier,kind,lock=True)
                if current['edit_version']!=payload.edit_version:raise HTTPException(409,'草稿已更新，请重新读取后合并')
                if current['slug']!=payload.slug:raise HTTPException(400,'已创建项目的标识不能修改，请复制为新项目')
                content=payload.model_dump(mode='json',exclude={'edit_version'})
                if kind=='skill' and content['skill_version_ids']:raise HTTPException(400,'技能不能绑定其他技能')
                for v in content['skill_version_ids']:registry.release(c,v,'skill')
                r.execute(c,'UPDATE kb_assistant_definitions SET draft=CAST(:d AS jsonb),edit_version=edit_version+1,updated_at=now() WHERE id=:id',id=identifier,d=r.j(content))
            return registry.detail(root(),identifier,kind)

        @router.post(path+'/{identifier}/publish')
        def publish(identifier:UUID,payload:VersionCheck):
            with r.database.transaction(root()) as c:
                registry.row(c,identifier,kind)
                registry.publish(c,identifier,payload.edit_version)
            return registry.detail(root(),identifier,kind)

        @router.post(path+'/{identifier}/state')
        def state(identifier:UUID,payload:State):
            with r.database.transaction(root()) as c:
                registry.row(c,identifier,kind)
                r.execute(c,'UPDATE kb_assistant_definitions SET enabled=:v,updated_at=now() WHERE id=:id',id=identifier,v=payload.enabled)
            return registry.detail(root(),identifier,kind)

        @router.post(path+'/{identifier}/activate')
        def activate(identifier:UUID,payload:Activate):
            with r.database.transaction(root()) as c:
                registry.row(c,identifier,kind,lock=True)
                v=registry.release(c,payload.version_id,kind,new_run=False)
                if str(v['definition_id'])!=str(identifier):raise HTTPException(400,'该版本不属于当前项目')
                r.execute(c,'UPDATE kb_assistant_definitions SET active_version=:v,updated_at=now() WHERE id=:id',id=identifier,v=payload.version_id)
            return registry.detail(root(),identifier,kind)

        @router.post(path+'/{identifier}/versions/{version}/revoke')
        def revoke(identifier:UUID,version:UUID):
            with r.database.transaction(root()) as c:
                registry.row(c,identifier,kind)
                v=registry.release(c,version,kind,new_run=False)
                if str(v['definition_id'])!=str(identifier):raise HTTPException(400,'该版本不属于当前项目')
                # Cooperative checks stop running calls; cancellation also fences publication.
                r.execute(c,"UPDATE kb_jobs j SET state='cancelled',updated_at=now() FROM kb_research_runs rr "
                          "WHERE rr.job_id=j.id AND rr.workspace_key=current_setting('shixue.workspace') AND j.state IN ('queued','running') "
                          "AND (rr.snapshot->'assistant'->'agent'->>'id'=:v OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(rr.snapshot->'assistant'->'skills','[]')) s WHERE s->>'id'=:v))",v=str(version))
                r.execute(c,'UPDATE kb_assistant_versions SET revoked=true WHERE id=:id',id=version)
            return registry.detail(root(),identifier,kind)

        @router.delete(path+'/{identifier}')
        def delete(identifier:UUID):
            with r.database.transaction(root()) as c:
                registry.row(c,identifier,kind,lock=True)
                if r.execute(c,'SELECT 1 FROM kb_assistant_versions WHERE definition_id=:id',id=identifier).scalar():raise HTTPException(409,'已发布版本需保留历史，请使用停用')
                r.execute(c,'DELETE FROM kb_assistant_definitions WHERE id=:id',id=identifier)
            return {'deleted':True}

    # Register concrete collection actions before identifier routes.
    @router.post('/skills/import')
    def import_skill(payload:Import):
        if bool(payload.data_base64)==bool(payload.files):raise HTTPException(400,'请选择一个 ZIP 包或一个技能目录')
        if payload.files:
            if sum(len(v.encode()) for v in payload.files.values())>registry.MAX_PACKAGE:raise HTTPException(400,'技能目录超过 256 KB')
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
                for name,body in payload.files.items():archive.writestr(name,body)
            data=buffer.getvalue()
        else:
            try:data=base64.b64decode(payload.data_base64,validate=True)
            except Exception:raise HTTPException(400,'技能包编码无效') from None
        draft=registry.import_package(data)
        with r.database.transaction(root()) as c:
            slug=draft.slug
            if r.execute(c,"SELECT 1 FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace') AND kind='skill' AND slug=:s",s=slug).scalar():
                draft=draft.model_copy(update={'slug':slug[:48]+'-copy-'+r.uid()[:8]})
            identifier=registry.insert(c,'skill',draft)
        return registry.detail(root(),identifier,'skill')

    @router.post('/skills/validate')
    def validate(payload:registry.Definition):
        if payload.skill_version_ids:raise HTTPException(400,'技能不能绑定其他技能')
        return {'valid':True,'sha256':registry.digest(payload.model_dump(mode='json',exclude={'edit_version'})),
                'message':'方法、文件和工具声明校验通过；研究效果请用样本文献试运行'}

    routes('/skills','skill');routes('/agents','agent')

    @router.get('/skills/{identifier}/versions/{version}/export')
    def export(identifier:UUID,version:UUID):
        with r.database.transaction(root()) as c:
            registry.row(c,identifier,'skill');value=registry.release(c,version,'skill',new_run=False)
            if str(value['definition_id'])!=str(identifier):raise HTTPException(400,'技能版本不匹配')
        return Response(registry.export_package(value['content']),media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{value["slug"]}.zip"'})

    @router.post('/skills/{identifier}/tests')
    def test(identifier:UUID,payload:Trial):
        with r.database.transaction(root()) as c:
            current=registry.row(c,identifier,'skill',lock=True)
            version_id=payload.version_id or registry.publish(c,identifier,payload.edit_version,activate=False)
            value=registry.release(c,version_id,'skill')
            if str(value['definition_id'])!=str(identifier):raise HTTPException(400,'技能版本不匹配')
        request=ResearchRequest.model_validate({k:v for k,v in payload.model_dump(mode='json').items() if k not in ('version_id','edit_version')})
        request=request.model_copy(update={'agent_version_id':None,'skill_version_ids':[UUID(str(version_id))]})
        run=create(root(),request,resolve_scope(request),model_settings())
        with r.database.transaction(root()) as c:
            r.execute(c,"INSERT INTO kb_skill_tests(id,workspace_key,skill_version_id,research_run_id) VALUES(:id,current_setting('shixue.workspace'),:v,:run) ON CONFLICT(research_run_id) DO NOTHING",id=r.uid(),v=version_id,run=run['id'])
        worker.start(root());return run

    @router.get('/skills/{identifier}/tests')
    def tests(identifier:UUID):
        with r.database.transaction(root()) as c:
            registry.row(c,identifier,'skill')
            return r.rows(c,"SELECT t.*,j.state,rr.question,rr.answer,rr.usage,j.error FROM kb_skill_tests t JOIN kb_assistant_versions v ON v.id=t.skill_version_id "
                          "JOIN kb_research_runs rr ON rr.id=t.research_run_id JOIN kb_jobs j ON j.id=rr.job_id "
                          "WHERE v.definition_id=:id AND t.workspace_key=current_setting('shixue.workspace') ORDER BY t.created_at DESC LIMIT 30",id=identifier)
