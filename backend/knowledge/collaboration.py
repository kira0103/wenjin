"""A bounded, durable research DAG. Scheduling never waits on a model call."""
import copy
import re
from typing import Literal
from uuid import UUID

from fastapi import HTTPException,Query
from pydantic import Field

from . import repository as r, assistant_registry as registry
from . import research_assistant as research

TERMINAL={'succeeded','failed','cancelled','blocked','skipped'}
ROLE_SLUGS={'classic':'classic-researcher','paper':'paper-researcher','general':'research-coordinator',
            'review':'evidence-reviewer','writer':'research-writer'}


class CollaborationRequest(research.ResearchRequest):
    token_budget:int=Field(default=180000,ge=40000,le=600000)
    max_rounds:int=Field(default=6,ge=2,le=8)
    workflow:Literal['compare','review']='compare'
    role_versions:dict[Literal['classic','paper','general','review','writer'],UUID]=Field(default_factory=dict,max_length=5)
    max_parallel:int=Field(default=2,ge=1,le=2)


def detail(root,identifier):
    with r.database.transaction(root) as c:
        value=r.one(c,"SELECT t.*,j.state,j.stage,j.progress,j.error,t.request->>'query' question FROM kb_collaboration_runs t "
                    "JOIN kb_jobs j ON j.id=t.job_id WHERE t.id=:id AND t.workspace_key=current_setting('shixue.workspace')",id=identifier)
        value['nodes']=r.rows(c,"SELECT n.*,rr.answer,rr.evidence,rr.usage,j.error FROM kb_collaboration_nodes n "
                            "LEFT JOIN kb_research_runs rr ON rr.id=n.research_run_id LEFT JOIN kb_jobs j ON j.id=rr.job_id "
                            "WHERE n.collaboration_id=:id ORDER BY n.specification->>'order',n.node_key",id=identifier)
        totals={'calls':0,'reserved_tokens':0,'provider_total_tokens':0,'provider_reported_calls':0}
        for node in value['nodes']:
            for key in totals:totals[key]+=(node.get('usage') or {}).get(key,0)+node['specification'].get('previous_usage',{}).get(key,0)
        value['usage']=totals
        return value


def start(root,payload,resolved,settings):
    if not settings.get('model') or not settings.get('base_url'):raise HTTPException(409,'请先配置问答模型')
    if payload.agent_version_id or payload.skill_version_ids:raise HTTPException(400,'协作研究请通过各角色选择已绑定的技能')
    if payload.scope.project_id:
        from .execution import activated
        with research.local_connection(root) as local:
            if not activated(local):raise HTTPException(409,'请先完成统一文献库迁移')
    with r.database.transaction(root) as c:
        previous=research.previous_request(c,payload,'kb_collaboration_runs')
        if previous:return detail(root,previous)
        registry.ensure_defaults(c)
        snapshot=research.snapshot_for(c,payload,resolved);snapshot['model']=research.model_identity(settings)
        groups=[];warnings=[]
        if payload.workflow=='compare':
            for key,title,kinds in [('classic','古籍证据搜集',{'ancient'}),('paper','论文论证分析',{'article','thesis'}),('general','其他资料补充',{'book','archive','other'})]:
                sources=[s for s in snapshot['sources'] if (s['item_type'] not in {'ancient','article','thesis'} if key=='general' else s['item_type'] in kinds)]
                if sources:groups.append((key,title,sources))
                elif key!='general':warnings.append(('古籍' if key=='classic' else '论文')+'资料缺失，本次无法完成对应类别的充分比较')
        else:
            kinds={s['item_type'] for s in snapshot['sources']}
            key='classic' if kinds=={'ancient'} else 'paper' if kinds.issubset({'article','thesis'}) else 'general'
            groups=[(key,'原文证据搜集',snapshot['sources'])]
        if not groups:raise HTTPException(409,'当前资料缺少可识别的文献类型，请完善类型后研究')
        profiles={}
        for key in {g[0] for g in groups}|{'review','writer'}:
            slug=ROLE_SLUGS[key];chosen=payload.role_versions.get(key)
            if not chosen:
                chosen=r.one(c,"SELECT active_version FROM kb_assistant_definitions WHERE workspace_key=current_setting('shixue.workspace') AND kind='agent' AND slug=:s",s=slug)['active_version']
            if not chosen:raise HTTPException(409,'请先发布协作角色：'+slug)
            profiles[key]=registry.freeze(c,chosen)
        count=len(groups)+2;allocation=min(120000,payload.token_budget//count)
        job=r.queue(c,'collaboration',{});identifier=r.uid()
        r.execute(c,"INSERT INTO kb_collaboration_runs(id,workspace_key,job_id,request,snapshot,budget,warnings) "
                  "VALUES(:id,current_setting('shixue.workspace'),:job,CAST(:q AS jsonb),CAST(:s AS jsonb),:b,CAST(:w AS jsonb))",id=identifier,job=job['id'],q=r.j(payload.model_dump(mode='json')),s=r.j(snapshot),b=payload.token_budget,w=r.j(warnings))
        gather_keys=[g[0] for g in groups]
        plan=[(key,title,sources,[]) for key,title,sources in groups]+[
            ('review','论断与证据复核',snapshot['sources'],gather_keys),('writer','综合研究草稿',snapshot['sources'],['review'])]
        for order,(key,title,sources,deps) in enumerate(plan):
            child_snapshot=copy.deepcopy(snapshot)
            child_snapshot['sources']=sources;child_snapshot['scope']['edition_ids']=[s['id'] for s in sources]
            child_snapshot['assistant']=profiles[key]
            child_snapshot['execution']={'team_id':identifier,'node_key':key,'final_only':key in ('review','writer')}
            specification={'snapshot':child_snapshot,'budget':allocation,'order':order,'previous_usage':{}}
            r.execute(c,'INSERT INTO kb_collaboration_nodes(id,collaboration_id,node_key,title,dependencies,specification) VALUES(:id,:t,:key,:title,CAST(:d AS jsonb),CAST(:s AS jsonb))',
                      id=r.uid(),t=identifier,key=key,title=title,d=r.j(deps),s=r.j(specification))
    return detail(root,identifier)


def transfer(inputs):
    evidence=[];seen={};handoffs=[]
    for node in inputs:
        mapping={}
        for source in node.get('evidence') or []:
            key=(source['block_id'],source['start_offset'],source['end_offset'])
            if key not in seen:
                if len(evidence)>=24 or sum(len(v['quote']) for v in evidence)+len(source['quote'])>16000:continue
                identifier='E'+str(len(evidence)+1);seen[key]=identifier
                evidence.append({**source,'evidence_id':identifier,'origin':node['node_key']+':'+source['evidence_id']})
            mapping[source['evidence_id']]=seen[key]
        text=re.sub(r'\[(E\d+)\]',lambda m:'['+mapping[m[1]]+']' if m[1] in mapping else '[未交接证据]',node.get('answer') or '')
        handoffs.append(node['title']+'（待核对意见）\n'+text[:1200])
    return evidence,'\n\n'.join(handoffs)[:3600]


def cancel_in(c,team,state='cancelled',message='用户停止了协同研究'):
    r.execute(c,"UPDATE kb_jobs SET state=:s,error=:e,updated_at=now(),lease_until=NULL WHERE id=:id AND state IN ('queued','running')",s=state,e=message,id=team['job_id'])
    r.execute(c,"UPDATE kb_jobs j SET state='cancelled',updated_at=now() FROM kb_research_runs rr JOIN kb_collaboration_nodes n ON n.research_run_id=rr.id "
              "WHERE rr.job_id=j.id AND n.collaboration_id=:id AND j.state IN ('queued','running')",id=team['id'])
    r.execute(c,"UPDATE kb_collaboration_nodes SET state='cancelled',message=:m WHERE collaboration_id=:id AND state NOT IN ('succeeded','failed','blocked','skipped')",id=team['id'],m=message)


def advance(root,settings):
    """One bounded scheduling transaction; safe to call after a process restart."""
    with r.database.transaction(root) as c:
        teams=r.rows(c,"SELECT t.*,j.state FROM kb_collaboration_runs t JOIN kb_jobs j ON j.id=t.job_id "
                     "WHERE t.workspace_key=current_setting('shixue.workspace') AND (j.state IN ('queued','running') OR "
                     "(j.state='cancelled' AND EXISTS(SELECT 1 FROM kb_collaboration_nodes n WHERE n.collaboration_id=t.id AND n.state NOT IN ('succeeded','failed','cancelled','blocked','skipped')))) "
                     "ORDER BY t.created_at LIMIT 10 FOR UPDATE OF t,j SKIP LOCKED")
        for team in teams:
            if team['state']=='cancelled':cancel_in(c,team);continue
            try:
                if research.model_identity(settings)!=team['snapshot']['model']:raise HTTPException(409,'模型配置已变化，请重新开始协作研究')
                research.verify_scope(root,team['snapshot'])
                r.execute(c,"UPDATE kb_jobs SET state='running',stage='collaborating',updated_at=now() WHERE id=:id",id=team['job_id'])
                nodes=r.rows(c,"SELECT n.*,rr.answer,rr.evidence,rr.usage,j.state job_state,j.error FROM kb_collaboration_nodes n "
                             "LEFT JOIN kb_research_runs rr ON rr.id=n.research_run_id LEFT JOIN kb_jobs j ON j.id=rr.job_id WHERE n.collaboration_id=:id ORDER BY n.specification->>'order'",id=team['id'])
                for node in nodes:registry.validate_frozen(c,node['specification']['snapshot']['assistant'])
                by_key={n['node_key']:n for n in nodes}
                for node in nodes:
                    if node['research_run_id']:
                        node['state']=node['job_state']
                        r.execute(c,'UPDATE kb_collaboration_nodes SET state=:s WHERE id=:id',id=node['id'],s=node['state'])
                    if node['state']!='pending':continue
                    upstream=[by_key[key] for key in node['dependencies']]
                    if any(n['state'] not in TERMINAL for n in upstream):continue
                    if node['node_key']=='writer' and any(n['state']!='succeeded' for n in upstream):
                        node['state']='blocked';node['message']='证据复核未完成，未生成综合草稿'
                    else:
                        snapshot=copy.deepcopy(node['specification']['snapshot'])
                        registry.validate_frozen(c,snapshot['assistant'])
                        evidence,handoff=transfer([n for n in upstream if n['state']=='succeeded'])
                        if upstream and not evidence:
                            node['state']='blocked';node['message']='上游没有可交接的原文证据'
                        else:
                            request={k:v for k,v in team['request'].items() if k not in ('workflow','role_versions','max_parallel')}
                            request['request_key']=None
                            request['token_budget']=node['specification']['budget']-node['specification'].get('previous_usage',{}).get('reserved_tokens',0)
                            request['scope']['edition_ids']=snapshot['scope']['edition_ids']
                            request=research.ResearchRequest.model_validate(request)
                            if upstream:snapshot['execution']['handoff']=handoff
                            node['research_run_id']=research.enqueue(c,request,snapshot,settings,evidence)
                            node['state']='queued'
                    r.execute(c,'UPDATE kb_collaboration_nodes SET state=:s,message=:m,research_run_id=:run WHERE id=:id',id=node['id'],s=node['state'],m=node.get('message',''),run=node['research_run_id'])
                done=sum(n['state'] in TERMINAL for n in nodes)
                r.execute(c,'UPDATE kb_jobs SET progress=:p WHERE id=:id',id=team['job_id'],p=done*100//len(nodes))
                if done==len(nodes):
                    writer=by_key['writer'];failed=[n for n in nodes if n['state']!='succeeded']
                    empty=[n for n in nodes if n['node_key'] not in ('review','writer') and n['state']=='succeeded' and not n.get('evidence')]
                    outcome='partial' if failed or empty or team['warnings'] else 'complete'
                    if writer['state']=='succeeded':
                        research.lock_final_scope(c,team['snapshot']);research.verify_evidence(c,writer['evidence'] or [],team['snapshot'])
                        warnings=list(team['warnings'])+[n['title']+'：'+(n.get('error') or n.get('message') or n['state']) for n in failed]
                        warnings.extend(n['title']+'：未提供可支持结论的原文证据，比较结果不完整' for n in empty)
                        if not writer['evidence']:outcome='partial';warnings.append('未取得支持结论的充分证据')
                        r.execute(c,'UPDATE kb_collaboration_runs SET answer=:a,evidence=CAST(:e AS jsonb),warnings=CAST(:w AS jsonb),outcome=:o WHERE id=:id',
                                  id=team['id'],a=writer['answer'],e=r.j(writer['evidence'] or []),w=r.j(warnings),o=outcome)
                        r.execute(c,"UPDATE kb_jobs SET state='succeeded',stage='complete',progress=100,updated_at=now() WHERE id=:id",id=team['job_id'])
                    else:
                        r.execute(c,"UPDATE kb_collaboration_runs SET outcome='partial' WHERE id=:id",id=team['id'])
                        r.execute(c,"UPDATE kb_jobs SET state='failed',error='部分分支失败或缺少证据，请检查过程后重试',updated_at=now() WHERE id=:id",id=team['job_id'])
            except HTTPException as error:cancel_in(c,team,'failed',str(error.detail)[:300])


def retry_branch(root,identifier,node_id):
    with r.database.transaction(root) as c:
        team=r.one(c,"SELECT t.*,j.state FROM kb_collaboration_runs t JOIN kb_jobs j ON j.id=t.job_id WHERE t.id=:id "
                   "AND t.workspace_key=current_setting('shixue.workspace') FOR UPDATE OF t,j",id=identifier)
        if team['state'] in ('queued','running'):raise HTTPException(409,'请等待协作结束或停止后再重试分支')
        nodes=r.rows(c,'SELECT n.*,rr.usage FROM kb_collaboration_nodes n LEFT JOIN kb_research_runs rr ON rr.id=n.research_run_id WHERE collaboration_id=:id',id=identifier)
        target=next((n for n in nodes if str(n['id'])==str(node_id)),None)
        if not target:raise HTTPException(404,'找不到协作分支')
        affected={target['node_key']}
        while True:
            new=affected|{n['node_key'] for n in nodes if set(n['dependencies'])&affected}
            if new==affected:break
            affected=new
        for n in nodes:
            if n['node_key'] not in affected:continue
            specification=n['specification'];previous=specification.setdefault('previous_usage',{})
            for key,value in (n['usage'] or {}).items():
                if isinstance(value,(int,float)):previous[key]=previous.get(key,0)+value
            if specification['budget']-previous.get('reserved_tokens',0)<8000:raise HTTPException(409,'该分支剩余预算不足，请调整总预算后重新研究')
            registry.validate_frozen(c,specification['snapshot']['assistant'])
            r.execute(c,"UPDATE kb_collaboration_nodes SET state='pending',message='重新执行，保留历史运行',research_run_id=NULL,specification=CAST(:s AS jsonb) WHERE id=:id",id=n['id'],s=r.j(specification))
        r.execute(c,"UPDATE kb_collaboration_runs SET answer='',evidence='[]',outcome='' WHERE id=:id",id=identifier)
        r.execute(c,"UPDATE kb_jobs SET state='queued',stage='starting',progress=0,error='',updated_at=now() WHERE id=:id",id=team['job_id'])
    return detail(root,identifier)


def register(router,root,resolve_scope,model_settings):
    from .worker import worker

    @router.get('/collaborations')
    def listing(limit:int=Query(30,ge=1,le=100),project_id:str|None=Query(None,max_length=100)):
        with r.database.transaction(root()) as c:return r.rows(c,"SELECT t.id,t.created_at,t.outcome,t.request->>'query' question,j.state FROM kb_collaboration_runs t JOIN kb_jobs j ON j.id=t.job_id "
                                                              "WHERE t.workspace_key=current_setting('shixue.workspace') AND (CAST(:project AS text) IS NULL OR COALESCE(t.request->>'context_project_id',t.request->'scope'->>'project_id')=:project) ORDER BY t.created_at DESC LIMIT :n",n=limit,project=project_id)

    @router.post('/collaborations')
    def create(payload:CollaborationRequest):
        value=start(root(),payload,resolve_scope(payload),model_settings());worker.start(root());return value

    @router.get('/collaborations/{identifier}')
    def get(identifier:UUID):return detail(root(),identifier)

    @router.post('/collaborations/{identifier}/cancel')
    def cancel(identifier:UUID):
        with r.database.transaction(root()) as c:
            team=r.one(c,"SELECT t.* FROM kb_collaboration_runs t JOIN kb_jobs j ON j.id=t.job_id WHERE t.id=:id AND t.workspace_key=current_setting('shixue.workspace') FOR UPDATE OF j",id=identifier)
            cancel_in(c,team)
        return detail(root(),identifier)

    @router.post('/collaborations/{identifier}/retry')
    def retry(identifier:UUID):
        old=detail(root(),identifier)
        if old['state'] in ('queued','running'):raise HTTPException(409,'请先停止当前研究')
        return create(CollaborationRequest.model_validate(old['request']).model_copy(update={'request_key':None}))

    @router.post('/collaborations/{identifier}/nodes/{node_id}/retry')
    def retry_node(identifier:UUID,node_id:UUID):
        value=retry_branch(root(),identifier,node_id);worker.start(root());return value
