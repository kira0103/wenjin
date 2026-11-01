"""Automatic routing, source maps and document overrides through real jobs."""
import json,os,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend.knowledge.adaptive_chunking import plan_blocks
from backend.knowledge.schemas import ChunkConfig,IngestionOptions


def block(body,n=1,**extra):
    return {'id':f'block-{n}','asset_id':'asset','page_index':1,'ordinal':n,'kind':'body','section_path':'','body':body,**extra}


class AdaptiveRules(unittest.TestCase):
    def test_auto_uses_content_and_handles_mixed_layouts_independently(self):
        config=ChunkConfig(mode='auto',profile='general',size=100,parent_size=400,overlap=10).model_dump()
        cases=[('# 第一章\n'+'财政制度。'*100+'\n# 第二章\n'+'行政制度。'*100,'parent_child'),
               ('普通短文。','recursive'),('姓名\t内容\n甲\t第一项\n乙\t第二项\n丙\t第三项','structure')]
        for text,expected in cases:
            with self.subTest(expected=expected):
                plan=plan_blocks([block(text)],config)
                self.assertEqual(plan['analysis']['selected_mode'],expected)
                self.assertTrue(plan['analysis']['checks']['source_positions_valid'])
        plan=plan_blocks([block(cases[0][0],quality={'page_signals':{'possible_multicolumn':True}})],config)
        self.assertEqual(plan['analysis']['selected_mode'],'recursive');self.assertTrue(plan['analysis']['warnings'])

    def test_auto_bulk_defaults_avoid_semantic_inference_but_explicit_opt_in_works(self):
        text='财政赋税制度与行政沿革。'*350
        config=ChunkConfig(mode='auto',size=150,parent_size=1000,overlap=10).model_dump()
        def forbidden(_texts):raise AssertionError('Automatic bulk chunking must not infer sentence vectors')
        plan=plan_blocks([block(text)],config,embedder=forbidden)
        self.assertEqual(plan['analysis']['parent_mode'],'recursive');self.assertEqual(plan['analysis']['semantic_vectors'],0)
        self.assertTrue(plan['analysis']['checks']['source_positions_valid'])
        plan=plan_blocks([block(text)],{**config,'auto_semantic':True},embedder=lambda texts:[[1.,0.] for _ in texts])
        self.assertEqual(plan['analysis']['parent_mode'],'semantic');self.assertGreater(plan['analysis']['semantic_vectors'],0)

    def test_semantic_topic_boundaries_and_hard_size_preserve_unicode(self):
        first='赋税制度𠀀'*12+'。';last='佛教义理'*12+'。';text=first*2+last*2
        config=ChunkConfig(mode='semantic',size=200,overlap=0,semantic_min_characters=20).model_dump()
        calls=[]
        def embed(texts):calls.extend(texts);return [[1.,0.] if '赋税' in t else [0.,1.] for t in texts]
        plan=plan_blocks([block(text)],config,embedder=embed)
        self.assertTrue(calls);self.assertEqual(plan['analysis']['semantic_fallbacks'],0)
        self.assertFalse(any('赋税' in c['body'] and '佛教' in c['body'] for c in plan['chunks']))
        for item in plan['items']:self.assertEqual(item['body'],text[item['start']:item['end']])
        self.assertLessEqual(plan['analysis']['checks']['maximum_chunk_characters'],200)

    def test_missing_model_budget_and_cancellation_are_explicit(self):
        text='税制发展。'*100
        config=ChunkConfig(mode='semantic',size=100,semantic_budget=32).model_dump()
        for embedder in (None,lambda texts:(_ for _ in ()).throw(RuntimeError('provider-secret'))):
            plan=plan_blocks([block(text)],config,embedder=embedder)
            self.assertGreater(plan['analysis']['semantic_fallbacks'],0)
            self.assertNotIn('provider-secret',json.dumps(plan['analysis']))
        def cancelled():raise HTTPException(409,'cancelled')
        with self.assertRaises(HTTPException):plan_blocks([block(text)],config,check=cancelled)

    def test_parent_child_joins_source_paragraphs_without_crossing_page_or_asset(self):
        blocks=[block('# 第一章\n'+('赋税制度。'*10)),block('同章原文𠀀。'*12,2),block('另一页不能拼入。'*10,3,page_index=2)]
        config=ChunkConfig(mode='parent_child',parent_mode='structure',profile='general',size=80,overlap=10,parent_size=500).model_dump()
        plan=plan_blocks(blocks,config)
        both=next(item for item in plan['items'] if len(item['parent_spans'])==2)
        self.assertEqual({s['block_id'] for s in both['parent_spans']},{'block-1','block-2'})
        for item in plan['items']:
            self.assertEqual(item['body'],item['block']['body'][item['start']:item['end']])
            self.assertFalse('block-3' in {s['block_id'] for s in item['parent_spans']} and item['block']['page_index']==1)

    def test_cleaning_across_paragraphs_keeps_evidence_coordinates(self):
        blocks=[block('# 第一章\n原文𠀀 https://example.org'),block('另一段正文\n重复行\n重复行',2)]
        config=ChunkConfig(mode='structure',size=100,cleaning={'remove_urls':True,'deduplicate_lines':True}).model_dump()
        plan=plan_blocks(blocks,config)
        self.assertTrue(plan['items'])
        for item in plan['items']:
            self.assertEqual(item['body'],item['block']['body'][item['start']:item['end']]);self.assertNotIn('https://',item['index_text'])

    def test_table_groups_retain_an_explicit_original_header_span(self):
        header='地区\t赋税额\n';text=header+''.join(f'州县{n}\t{n}石赋税\n' for n in range(80))
        plan=plan_blocks([block(text)],ChunkConfig(mode='auto',size=100,parent_size=300,overlap=0).model_dump())
        self.assertEqual(plan['analysis']['selected_mode'],'structure')
        later=next(c for c in plan['items'] if c['start']>300)
        prefix=next(s for s in later['parent_spans'] if s.get('role')=='header')
        self.assertEqual(text[prefix['start']:prefix['end']],header)


@unittest.skipUnless(os.name=='nt' and os.environ.get('SHIXUE_MANAGED_TESTS')=='1','需要独立内置 PostgreSQL')
class AdaptiveNative(unittest.TestCase):
    from tests.test_rag_pipeline import PipelineNative as _Fixture
    setUpClass=classmethod(_Fixture.setUpClass.__func__)
    tearDownClass=classmethod(_Fixture.tearDownClass.__func__)
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    document=_Fixture.document
    checked=_Fixture.checked

    def test_document_overrides_versions_inheritance_and_frozen_jobs(self):
        from backend.knowledge import repository as r
        a,_,ra=self.document('自动策略甲','财政制度。'*20)
        b,_,rb=self.document('固定策略乙','佛教义理。'*20)
        configs=[IngestionOptions(chunking=ChunkConfig(mode='semantic',size=120)).model_dump(),IngestionOptions(chunking=ChunkConfig(mode='fixed',size=80)).model_dump()]
        for edition,processing in zip((a,b),configs):
            saved=self.checked(self.client.put(f'/api/knowledge/rag-documents/{edition}/policy',json={'version':0,'processing':processing}))
            self.assertEqual(saved['version'],1)
            self.assertEqual(self.client.put(f'/api/knowledge/rag-documents/{edition}/policy',json={'version':0,'processing':processing}).status_code,409)
        submitted=self.checked(self.client.post('/api/knowledge/rag-process',json={'operation':'index','documents':[{'id':a,'revision_id':ra},{'id':b,'revision_id':rb}]}))
        with r.database.transaction(self.root) as db:
            frozen=[r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=j['id'])['payload'] for j in submitted['jobs']]
        self.assertEqual({j['edition_id']:j['config']['mode'] for j in frozen},{a:'semantic',b:'fixed'})
        self.checked(self.client.put(f'/api/knowledge/rag-documents/{a}/policy',json={'version':1,'processing':None}))
        policy=self.checked(self.client.get(f'/api/knowledge/rag-documents/{a}/policy'))
        self.assertEqual(policy['source'],'default');self.assertIsNone(policy['override'])
        with r.database.transaction(self.root) as db:
            a_job=next(j for j in submitted['jobs'] if j['edition_id']==a)
            self.assertEqual(r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=a_job['id'])['payload']['config']['mode'],'semantic')

    def test_upload_batch_overrides_are_per_document_and_preview_is_fenced(self):
        import uuid
        from backend.knowledge import repository as r
        from backend.knowledge.document_imports import DocumentImports,process_batch
        from backend.knowledge.ingestion import process_import,process_index
        folder=self.root/uuid.uuid4().hex;folder.mkdir()
        files=[]
        for name,text in [('甲.txt','# 第一章\n\n财政制度。'*50),('乙.txt','普通原文𠀀。'*70)]:
            path=folder/name;path.write_text(text,encoding='utf-8');files.append(str(path))
        imports=DocumentImports(self.root);preview=imports.preview('files',files)
        overrides={'甲.txt':ChunkConfig(mode='structure',size=100).model_dump(),'乙.txt':ChunkConfig(mode='fixed',size=80).model_dump()}
        started=imports.start(preview['id'],'混合文档','article',processing=IngestionOptions().model_dump(),document_strategies=overrides)
        with r.database.transaction(self.root) as db:payload=r.one(db,'SELECT payload FROM kb_jobs WHERE id=:id',id=started['job_id'])['payload']
        result=process_batch(self.root,payload,lambda:None,lambda *a:None);self.assertEqual(result['succeeded'],2)
        with r.database.transaction(self.root) as db:
            manifest=r.one(db,'SELECT manifest FROM kb_document_imports WHERE id=:id',id=preview['id'])['manifest']
        for entry in manifest:
            edition=entry['edition_id'];wanted=overrides[entry['relative_path']]['mode']
            with r.database.transaction(self.root) as db:
                job=r.one(db,"SELECT payload FROM kb_jobs WHERE kind='import' AND payload->>'edition_id'=:id ORDER BY created_at DESC LIMIT 1",id=edition)['payload']
            self.assertEqual(job['processing']['chunking']['mode'],wanted)
            parsed=process_import(self.root,job,lambda:None,lambda *a:None)
            with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,texts:([[1.0]+[0.0]*383 for _ in texts],'adaptive-fixture')):
                indexed=process_index(self.root,{'edition_id':edition,'revision_id':parsed['revision_id'],'config':job['processing']['chunking']},lambda:None,lambda *a:None)
            self.assertEqual(indexed['analysis']['selected_mode'],wanted)
            self.assertEqual(self.client.post(f'/api/knowledge/rag-documents/{edition}/preview',json={'expected_revision':str(uuid.uuid4())}).status_code,409)
            plan=self.checked(self.client.post(f'/api/knowledge/rag-documents/{edition}/preview',json={'expected_revision':parsed['revision_id']}))
            self.assertEqual(plan['analysis']['selected_mode'],wanted);self.assertTrue(plan['analysis']['checks']['source_positions_valid'])

    def test_stored_parents_span_paragraphs_but_cannot_escape_edition_or_scope(self):
        from backend.knowledge import repository as r
        from backend.knowledge.ingestion import process_index
        from backend.knowledge.retrieval import search
        edition,asset,revision=self.document('结构父子关联','# 第一章\n\n目标税制原文。\n\n同章税制背景。')
        other,_,other_revision=self.document('禁止拼入其他文献','其他文献秘密税制')
        cfg=ChunkConfig(mode='parent_child',parent_mode='structure',profile='general',size=80,parent_size=500).model_dump()
        with patch('backend.knowledge.ingestion.embed',side_effect=lambda root,texts:([[1.0]+[0.0]*383 for _ in texts],'adaptive-fixture')):
            result=process_index(self.root,{'edition_id':edition,'revision_id':revision,'config':cfg},lambda:None,lambda *a:None)
        with r.database.transaction(self.root) as db:
            foreign=r.one(db,'SELECT id,body FROM kb_blocks WHERE revision_id=:id ORDER BY ordinal LIMIT 1',id=other_revision)
            rows=r.rows(db,'SELECT id,parent_spans FROM kb_chunks WHERE index_id=:id',id=result['index_id'])
            for row in rows:
                row['parent_spans'].append({'block_id':str(foreign['id']),'start':0,'end':len(foreign['body'])})
                r.execute(db,'UPDATE kb_chunks SET parent_spans=CAST(:spans AS jsonb) WHERE id=:id',id=row['id'],spans=r.j(row['parent_spans']))
        found=search(self.root,{'query':'目标税制','mode':'keyword','scope':{'edition_ids':[edition],'page_from':2,'page_to':2}})
        self.assertTrue(found['results']);parts=found['results'][0]['parent_context']['parts']
        self.assertGreaterEqual(len(parts),2);self.assertTrue(all(p['page_index']==2 and p['asset_id']==asset for p in parts))
        self.assertNotIn('其他文献',str(parts))
        with r.database.transaction(self.root) as db:
            for p in parts:self.assertEqual(r.one(db,'SELECT body FROM kb_blocks WHERE id=:id',id=p['block_id'])['body'][p['start']:p['end']],p['body'])

    def test_automatic_semantic_parent_is_actually_built_by_the_index_worker(self):
        from backend.knowledge import repository as r
        from backend.knowledge.ingestion import process_index
        text=('赋税制度𠀀'*12+'。')*22+('佛教义理'*12+'。')*22
        edition,_,revision=self.document('自动语义父块',text)
        def embeddings(root,texts):return [([1.,0.]+[0.]*382 if '赋税' in t else [0.,1.]+[0.]*382) for t in texts],'semantic-fixture'
        cfg=ChunkConfig(mode='auto',profile='general',size=150,parent_size=1000,overlap=10,auto_semantic=True).model_dump()
        with patch('backend.knowledge.ingestion.embed',side_effect=embeddings):
            result=process_index(self.root,{'edition_id':edition,'revision_id':revision,'config':cfg},lambda:None,lambda *a:None)
        self.assertEqual(result['analysis']['selected_mode'],'parent_child')
        self.assertEqual(result['analysis']['parent_mode'],'semantic');self.assertGreater(result['analysis']['semantic_vectors'],0)
        with r.database.transaction(self.root) as db:
            chunks=r.rows(db,'SELECT c.*,b.body original FROM kb_chunks c JOIN kb_blocks b ON b.id=c.block_id WHERE c.index_id=:id',id=result['index_id'])
            self.assertTrue(chunks)
            for c in chunks:
                self.assertFalse('赋税' in c['body'] and '佛教' in c['body'])
                self.assertEqual(c['body'],c['original'][c['start_offset']:c['end_offset']]);self.assertTrue(c['parent_spans'])
