import json
import sqlite3
import io
import zipfile
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from backend import preprocessing
from backend.preprocessing import FIXED_RECIPE
from backend import app as service
from backend.preprocess_engine import BUILTINS, Recipe, analyze, digest, formatted, prepared, punctuation_error

SOURCE = '\n'.join(('提要', '卷首原文应完整保留', '钦定四库全书', '古今合璧事类备要前集卷一', '宋　谢维新　撰', '天文门', '天',
    '事类天地【丨丨之精】日月【丨之光】缺字【□】疑字【\ue001】', '古今合璧事类备要前集卷一', '<子部,类书类,古今合璧事类备要>',
    '钦定四库全书', '古今合璧事类备要前集卷二', '宋　谢维新　撰', '地理门', '地', '山川【川流不息】', '古今合璧事类备要前集卷二', ''))


class EngineTests(unittest.TestCase):
    def test_fixed_repeat_restoration_matches_sequential_headwords(self):
        text = '卷五\n事类折角【朱云丨其丨】陟位【三载汝丨帝丨书】日月【丨之光】天【丨丨照】\n【无词头丨】\n坏【嵌【丨】丨】\n'
        result = analyze(text, FIXED_RECIPE)
        output = formatted(prepared(text, result['edits']), FIXED_RECIPE)
        self.assertIn('折角 朱云折其角', output)
        self.assertIn('陟位 三载汝陟帝位书', output)
        self.assertIn('日月 日之光', output)
        self.assertIn('天 天天照', output)
        self.assertIn('无词头丨', output)
        self.assertIn('嵌 丨 丨', output)
        self.assertEqual(sum(e['status']=='applied' for e in result['edits']), 7)
        self.assertEqual(sum(bool(e.get('needs_review')) for e in result['edits']), 3)
        self.assertTrue(all(text[e['start']:e['end']]==e['original'] for e in result['edits']))
        legacy = Recipe.model_validate_json('{"name":"旧方案","restore_repeat":true}')
        self.assertEqual(legacy.repeat_mode, 'exact_count')
        self.assertIn('日月【丨之光】', prepared(text, analyze(text, legacy)['edits']))

    def test_inline_standalone_volume_frontmatter_and_footer(self):
        result = analyze(SOURCE, BUILTINS[1])
        self.assertEqual(len([n for n in result['nodes'] if n['label'] == '卷']), 2)
        self.assertTrue(result['coverage_ok'])
        self.assertEqual(prepared(SOURCE, result['edits']), SOURCE)
        old = SOURCE.replace('古今合璧事类备要前集卷一\n宋', '卷一\n古今合璧事类备要前集卷一\n宋')
        self.assertEqual(len([n for n in analyze(old, BUILTINS[1])['nodes'] if n['label'] == '卷']), 2)

    def test_new_format_is_declarative_and_lossless(self):
        source = '序言\n原文\nSECTION A\nITEM 1\n天地玄黄\nITEM 2\n宇宙洪荒\n'
        recipe = Recipe(name='新格式', rules=[{'label': '章节', 'level': 1, 'match': 'prefix', 'value': 'SECTION '}, {'label': '条目', 'level': 2, 'match': 'prefix', 'value': 'ITEM '}])
        result = analyze(source, recipe)
        self.assertEqual([n['title'] for n in result['nodes']], ['未分类文本 / 卷首材料', 'SECTION A', 'ITEM 1', 'ITEM 2'])
        self.assertEqual(prepared(source, result['edits']), source)
        with self.assertRaises(ValueError):
            Recipe(name='非法', rules=[{'label': '代码', 'level': 1, 'match': 'regex', 'value': '(a+)+'}])

    def test_repeat_counts_and_nested_unknown_marks(self):
        recipe = BUILTINS[1].model_copy(update={'restore_repeat': True, 'remove_note_brackets': False})
        text = '事类天地【丨丨之精】日月【丨之光】\n孤立丨\n天【注【丨】丨】\n'
        result = analyze(text, recipe)
        output = prepared(text, result['edits'])
        self.assertEqual(output, text.replace('天地【丨丨', '天地【天地'))
        self.assertEqual(result['applied_count'], 2)
        self.assertEqual(sum(e['kind'] == 'repeat' and e['status'] == 'pending' for e in result['edits']), 4)
        self.assertEqual(len(output), len(text))
        self.assertEqual(formatted(output, recipe).replace('\n', ''), output.replace('\n', ''))

    def test_damaged_volume_title_reports_uncertainty(self):
        text = SOURCE.replace('事类备要前集卷二', '事类备要绩集卷二')
        result = analyze(text, BUILTINS[1])
        self.assertEqual(len([n for n in result['nodes'] if n['label'] == '卷']), 2)
        self.assertTrue(any('集名异常' in w for w in result['warnings']))
        self.assertEqual(prepared(text, result['edits']), text)

    def test_bracket_cleaning_keeps_inner_text_and_other_symbols(self):
        poem = '五更山吐月牕逈室幽幽玉钩还挂户江练却明楼星河淡清晓鼓角冷如秋同上'
        recipe = Recipe(name='去注释括号', remove_note_brackets=True)
        self.assertEqual(formatted('【'+poem+'】', recipe), poem)
        self.assertEqual(formatted('𠀀【内【文】□】〔异文〕《书》丨\n【未闭合', recipe), '𠀀内文□〔异文〕《书》丨\n未闭合')
        legacy = Recipe.model_validate_json('{"name":"旧方案","split_notes":false}')
        self.assertEqual(formatted('【'+poem+'】', legacy), '【'+poem+'】')

    def test_bracket_cleaning_follows_repeat_restoration_and_line_splitting(self):
        text = '事类天地【丨丨之精】日月【明月】\n孤【注【□】丨】\n'
        recipe = BUILTINS[1].model_copy(update={'restore_repeat': True})
        result = analyze(text, recipe)
        canonical = prepared(text, result['edits'])
        self.assertIn('天地【天地之精】', canonical)
        self.assertEqual(formatted(canonical, recipe), '事类\n天地 天地之精\n日月 明月\n孤 注 □ 丨\n')
        self.assertTrue(any(e['kind'] == 'bracket' and e['status'] == 'pending' for e in result['edits']))
        self.assertTrue(all(text[e['start']:e['end']] == e['original'] for e in result['edits']))

    def test_punctuation_is_insertion_only(self):
        source = '天【天地】\n日月'
        self.assertEqual(punctuation_error(source, '天【天地】，\n日月。'), '')
        for output in ('天【大地】\n日月', '天【天地】日月', '天【天地】\n月日', '天【天地】\n日月〔疑〕'):
            self.assertTrue(punctuation_error(source, output))

    def test_quote_spacing_preserves_whitespace_and_standalone_quotes(self):
        recipe = Recipe(name='引文间隔', remove_note_brackets=True, quote_spacing=True)
        self.assertEqual(formatted('题【引文】续【后文】', recipe), '题 引文 续 后文')
        self.assertEqual(formatted('【整段诗】\n题 【引文】\n题【 注文 】尾\n', recipe), '整段诗\n题 引文\n题 注文 尾\n')
        self.assertEqual(formatted('甲【内【嵌套】】乙', recipe), '甲 内 嵌套 乙')
        self.assertEqual(formatted('题【文】', recipe.model_copy(update={'remove_note_brackets': False})), '题【文】')
        self.assertEqual(formatted('题【文】', recipe.model_copy(update={'quote_spacing': False})), '题文')

    def test_paragraph_labels_are_longest_first_and_never_inside_quotations(self):
        recipe = BUILTINS[1]
        text = '事理发挥【总论引用事类不可拆】\n事類緑沉【引文】詩集诗名【诗文】\n格物總論【总论】\n【引用跨行\n事类在引文内部】\n事类\n正文\n'
        output = formatted(text, recipe)
        self.assertEqual(output, '事理发挥\n总论引用事类不可拆\n事類\n緑沉 引文\n詩集\n诗名 诗文\n格物總論\n总论\n引用跨行\n事类在引文内部\n事类\n正文\n')
        self.assertNotIn('事理\n发挥', output)
        source = '卷五\n'+text+'卷六\n次卷正文\n'
        result = analyze(source, recipe)
        self.assertEqual(len(result['texts']), 2)
        self.assertTrue(result['coverage_ok'])

    def test_custom_paragraph_labels_are_literal_and_reusable(self):
        recipe = Recipe(name='新书小标题', paragraph_labels=['考证', '总论[甲]'])
        text = '考证标目【文】\n总论[甲]正文\n【考证不可拆】\n'
        self.assertEqual(formatted(text, recipe), '考证\n标目【文】\n总论[甲]\n正文\n【考证不可拆】\n')
        self.assertEqual(formatted(text, Recipe.model_validate_json(recipe.model_dump_json())), formatted(text, recipe))
        for labels in ([''], ['坏\n标题'], ['【正文】']):
            with self.assertRaises(ValueError):
                Recipe(name='非法标题', paragraph_labels=labels)

    def test_traditional_paragraph_prefix_does_not_pollute_repeat_restoration(self):
        text = '詩集明月【丨丨照窗】\n事理發揮天地【丨丨之精】\n'
        recipe = BUILTINS[1].model_copy(update={'restore_repeat': True})
        result = analyze(text, recipe)
        self.assertEqual(result['applied_count'], 4)
        self.assertEqual(formatted(prepared(text, result['edits']), recipe), '詩集\n明月 明月照窗\n事理發揮\n天地 天地之精\n')

    def test_agent_adaptable_keyword_and_next_line_distinguish_footers(self):
        source = '序言\n新书第五篇\n明某撰\n正文\n新书第五篇\n<卷尾>\n新书第六篇\n明某撰\n下一篇正文\n'
        recipe = Recipe(name='按篇拆分', rules=[{'label': '篇', 'level': 1, 'match': 'numbered_title', 'value': '篇', 'split': True, 'require_next': ['明'], 'exclude_next': ['<']}])
        result = analyze(source, recipe)
        self.assertEqual([t['title'] for t in result['texts']], ['卷首材料', '新书第五篇', '新书第六篇'])
        self.assertEqual(''.join(source[t['start']:t['end']] for t in result['texts']), source)
        self.assertIn('新书第五篇\n<卷尾>', source[result['texts'][1]['start']:result['texts'][1]['end']])
        disabled = recipe.model_copy(update={'rules': [recipe.rules[0].model_copy(update={'split': False})]})
        self.assertEqual(len(analyze(source, disabled)['texts']), 1)

    def test_repeated_keywords_merge_without_losing_footer(self):
        source = '卷五\n正文\n卷五\n卷六\n次卷正文\n'
        recipe = Recipe(name='卷题', rules=[{'label': '卷', 'level': 1, 'match': 'volume', 'split': True}])
        result = analyze(source, recipe)
        self.assertEqual(len(result['texts']), 2)
        self.assertEqual(source[result['texts'][0]['start']:result['texts'][0]['end']], '卷五\n正文\n卷五\n')
        self.assertTrue(any('重复拆分题名' in w for w in result['warnings']))


class PreprocessFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        service.initialize(Path(self.directory.name))
        service.service.token = 'preprocess-test'
        self.client = TestClient(service.app, headers={'X-App-Token': 'preprocess-test'})
        self.secret = patch.object(service.keyring, 'get_password', return_value=None)
        self.secret.start()
        self.project = self.client.post('/api/projects', json={'name': '预处理测试', 'project_type': 'collation'}).json()['id']
        self.material = self.upload(SOURCE)
        self.input = {'material_id': self.material['id'], 'page_from': 1, 'page_to': self.material['page_count']}
        self.recipe = BUILTINS[1].model_copy(update={'restore_repeat': True, 'remove_note_brackets': False, 'quote_spacing': False, 'paragraph_labels': []}).model_dump()

    def tearDown(self):
        for thread in list(service.service.tasks):
            thread.join(5)
        service.service.tasks.clear()
        self.client.close()
        self.secret.stop()
        self.directory.cleanup()

    def upload(self, text, filename='底本.txt', data=None):
        result = self.client.post('/api/materials/import', params={'filename': filename, 'project_id': self.project, 'role': 'base'}, content=data or text.encode('utf-8'), headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def create_run(self, recipe=None, source=None):
        body = {**(source or self.input), 'recipe': recipe or self.recipe}
        preview = self.client.post(f'/api/projects/{self.project}/preprocess/preview', json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        response = self.client.post(f'/api/projects/{self.project}/preprocess/runs', json={**body, 'preview_signature': preview.json()['signature'], 'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def accept(self, run):
        result = self.client.post(f'/api/preprocess/runs/{run["id"]}/accept', json={'expected_revision': run['revision'], 'retain_pending': True})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def configure(self):
        self.client.put('/api/settings', json={'base_url': 'http://127.0.0.1:9998/v1', 'model': 'fixture-model', 'provider_name': '测试'})

    def wait_job(self, job_id):
        for _ in range(100):
            value = self.client.get('/api/preprocess/jobs/'+job_id).json()
            if value['status'] not in {'running', 'queued'}:
                return value
            time.sleep(.02)
        self.fail('识别未结束')

    def split_fixed(self, material=None):
        response = self.client.post(f'/api/projects/{self.project}/preprocess/split', json={'material_id': (material or self.material)['id']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_bootstrap_preparation_count_uses_latest_attached_results(self):
        self.assertEqual(self.client.get('/api/bootstrap', params={'project_id': self.project}).json()['prepared_material_count'], 0)
        ready = self.split_fixed()
        self.assertEqual(self.client.get('/api/bootstrap', params={'project_id': self.project}).json()['prepared_material_count'], 1)
        text = self.client.get(f'/api/preprocess/runs/{ready["id"]}/texts').json()['items'][0]
        self.client.post(f'/api/preprocess/runs/{ready["id"]}/units', json={'text_ids': [text['id']]})
        self.create_run()
        # An old referenced ready snapshot must not make the new review result ready.
        self.assertEqual(self.client.get('/api/bootstrap', params={'project_id': self.project}).json()['prepared_material_count'], 0)
        self.split_fixed()
        with service.connection() as db:
            db.execute('DELETE FROM project_materials WHERE project_id=? AND material_id=?', (self.project, self.material['id']))
        self.assertEqual(self.client.get('/api/bootstrap', params={'project_id': self.project}).json()['prepared_material_count'], 0)

    def test_fixed_flow_is_immediately_exportable_and_retains_review_notes(self):
        with patch.object(service, 'generate_text', side_effect=AssertionError('fixed flow must not call a model')):
            run = self.split_fixed()
        self.assertEqual(run['status'], 'accepted')
        self.assertEqual(run['summary']['workflow'], 'fixed-basic-v1')
        self.assertEqual(run['summary']['restored_repeat_count'], 3)
        self.assertEqual(run['summary']['repeat_review_count'], 1)
        self.assertEqual(run['recipe'], FIXED_RECIPE.model_dump())
        texts = self.client.get(f'/api/preprocess/runs/{run["id"]}/texts').json()['items']
        self.assertEqual(len(texts), 3)
        volume = next(t for t in texts if t['kind']=='volume')
        url = f'/api/preprocess/runs/{run["id"]}'
        exported = self.client.get(url+f'/export?text_id={volume["id"]}')
        self.assertEqual(exported.status_code, 200, exported.text)
        output = exported.json()['content']
        self.assertIn('事类\n天地 天地之精\n日月 日之光', output)
        self.assertNotIn('【', output)
        self.assertNotIn('】', output)
        self.assertEqual(self.client.get(url+'/export?format=zip').status_code, 200)
        created = self.client.post(url+'/units', json={'text_ids': [volume['id']]})
        self.assertEqual(created.status_code, 200, created.text)
        unit = created.json()[0]
        self.assertEqual(unit['content'], output)
        self.assertTrue(any('数量不等' in n['evidence'] and n['status']=='pending' for n in unit['notes']))
        self.assertIn('□', unit['content'])
        self.assertEqual(self.client.get('/api/materials/'+self.material['id']).json()['pages'][0]['text'], SOURCE)
        approved = self.client.put('/api/units/'+unit['id'], json={'expected_revision': unit['revision'], 'content': unit['content'], 'notes': unit['notes'], 'status': 'approved', 'confirm_review': True})
        self.assertEqual(approved.status_code, 400)

    def test_fixed_flow_replaces_latest_and_preserves_referenced_old_draft(self):
        old = self.split_fixed()
        latest = self.split_fixed()
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).status_code, 404)
        text = self.client.get(f'/api/preprocess/runs/{latest["id"]}/texts').json()['items'][0]
        unit = self.client.post(f'/api/preprocess/runs/{latest["id"]}/units', json={'text_ids': [text['id']]}).json()[0]
        unit = self.client.get('/api/units/'+unit['id']).json()
        newest = self.split_fixed()
        self.assertEqual([r['id'] for r in self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()], [newest['id']])
        self.assertEqual(self.client.get('/api/preprocess/runs/'+latest['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json(), unit)

    def test_fixed_flow_rejects_configuration_and_invalid_project(self):
        url = f'/api/projects/{self.project}/preprocess/split'
        self.assertEqual(self.client.post(url, json={'material_id': self.material['id'], 'recipe': {'restore_repeat': False}}).status_code, 422)
        self.assertEqual(self.client.post(url, json={'material_id': self.material['id'], 'page_to': 1}).status_code, 422)
        foreign = self.client.post('/api/projects', json={'name': '无关项目'}).json()['id']
        self.assertEqual(self.client.post(f'/api/projects/{foreign}/preprocess/split', json={'material_id': self.material['id']}).status_code, 400)

    def test_fixed_flow_failure_keeps_original_result(self):
        old = self.split_fixed()
        with patch.object(preprocessing, 'prune_superseded_runs', side_effect=sqlite3.OperationalError('save failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.split_fixed()
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).json(), old)
        with service.connection() as db:
            db.execute('UPDATE pages SET text=? WHERE material_id=?', ('无法识别卷题的正文', self.material['id']))
        failed = self.client.post(f'/api/projects/{self.project}/preprocess/split', json={'material_id': self.material['id']})
        self.assertEqual(failed.status_code, 400)
        self.assertIn('没有识别到卷题', failed.text)
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).json(), old)

    def test_fixed_flow_cleans_word_and_reports_missing_pdf_text(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>卷五</w:t></w:r></w:p><w:p><w:r><w:t>事类天地【丨丨之精】</w:t></w:r></w:p><w:p><w:r><w:t>格物總論【格物文】</w:t></w:r></w:p></w:body></w:document>')
        word = self.upload('', filename='底本.docx', data=output.getvalue())
        run = self.split_fixed(word)
        exported = self.client.get(f'/api/preprocess/runs/{run["id"]}/export').json()['content']
        self.assertIn('事类\n天地 天地之精', exported)
        self.assertIn('格物總論\n格物文', exported)
        document = pymupdf.open()
        document.new_page().insert_text((50,50), 'Some text')
        document.new_page()
        pdf = self.upload('', filename='部分扫描.pdf', data=document.tobytes())
        document.close()
        failed = self.client.post(f'/api/projects/{self.project}/preprocess/split', json={'material_id': pdf['id']})
        self.assertEqual(failed.status_code, 400)
        self.assertIn('第 2', failed.json()['detail'])
        self.assertIn('没有文字层', failed.json()['detail'])

    def test_repeated_runs_keep_only_latest_and_reject_stale_preview(self):
        ids = [self.create_run()['id'] for _ in range(5)]
        listed = self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()
        self.assertEqual([r['id'] for r in listed], ids[-1:])
        for run_id in ids[:-1]:
            self.assertEqual(self.client.get('/api/preprocess/runs/'+run_id).status_code, 404)
        failed = self.client.post(f'/api/projects/{self.project}/preprocess/runs', json={**self.input, 'recipe': self.recipe, 'preview_signature': 'outdated', 'confirm': True})
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(self.client.get(f'/api/projects/{self.project}/preprocess/runs').json(), listed)
        with service.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM preprocess_runs').fetchone()[0], 1)

    def test_latest_replacement_is_scoped_to_project_and_material(self):
        first = self.create_run()
        other = self.upload('卷一\n另一本书的正文\n', filename='其他底本.txt')
        other_run = self.create_run(source={'material_id': other['id'], 'page_from': 1, 'page_to': other['page_count']})
        foreign = self.client.post('/api/projects', json={'name': '另一项目', 'project_type': 'collation'}).json()['id']
        attached = self.client.post('/api/materials/import', params={'filename': '底本.txt', 'project_id': foreign, 'role': 'base'}, content=SOURCE.encode(), headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(attached.status_code, 200, attached.text)
        foreign_material = attached.json()
        body = {'material_id': foreign_material['id'], 'page_from': 1, 'page_to': foreign_material['page_count'], 'recipe': self.recipe}
        signature = self.client.post(f'/api/projects/{foreign}/preprocess/preview', json=body).json()['signature']
        foreign_run = self.client.post(f'/api/projects/{foreign}/preprocess/runs', json={**body, 'preview_signature': signature, 'confirm': True}).json()
        latest = self.create_run()
        self.assertEqual({r['id'] for r in self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()}, {latest['id'], other_run['id']})
        self.assertEqual([r['id'] for r in self.client.get(f'/api/projects/{foreign}/preprocess/runs').json()], [foreign_run['id']])
        self.assertEqual(self.client.get('/api/preprocess/runs/'+first['id']).status_code, 404)
        with service.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM preprocess_runs').fetchone()[0], 3)

    def test_failed_replacement_rolls_back_both_insert_and_cleanup(self):
        old = self.create_run()
        with patch.object(preprocessing, 'prune_superseded_runs', side_effect=sqlite3.OperationalError('simulated save failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.create_run()
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).json(), old)
        with service.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM preprocess_runs').fetchone()[0], 1)

    def test_ai_artifact_source_is_retained_but_hidden_from_latest_list(self):
        old = self.accept(self.create_run())
        body = json.dumps({'source_context': {'preprocess': {'run_id': old['id']}}})
        with service.connection() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', ('artifact-source', self.project, 'fixture', self.material['id'], 'AI 成果', 'punctuate', body, service.utc_now()))
        latest = self.create_run()
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).json(), old)
        self.assertEqual([r['id'] for r in self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()], [latest['id']])
        with service.connection() as db:
            self.assertEqual(db.execute('SELECT body FROM artifacts WHERE id=?', ('artifact-source',)).fetchone()[0], body)

    def test_upgrade_backs_up_and_prunes_only_unreferenced_history(self):
        old = self.accept(self.create_run())
        text = self.client.get(f'/api/preprocess/runs/{old["id"]}/texts').json()['items'][0]
        unit = self.client.post(f'/api/preprocess/runs/{old["id"]}/units', json={'text_ids': [text['id']]}).json()[0]
        unit = self.client.get('/api/units/'+unit['id']).json()
        with service.connection() as db:
            snapshot = dict(db.execute('SELECT * FROM preprocess_runs WHERE id=?', (old['id'],)).fetchone())
            columns = ','.join(snapshot)
            marks = ','.join('?' for _ in snapshot)
            for run_id in ('legacy-unused', 'legacy-latest'):
                snapshot['id'] = run_id
                # A backwards clock must not make the older run active again.
                snapshot['created_at'] = '2000-01-01T00:00:00Z'
                db.execute(f'INSERT INTO preprocess_runs({columns}) VALUES({marks})', list(snapshot.values()))
        service.initialize(Path(self.directory.name))
        backup = Path(self.directory.name)/'backups'/'before-0.3.4.sqlite'
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM preprocess_runs').fetchone()[0], 3)
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json(), unit)
        self.assertEqual(self.client.get('/api/preprocess/runs/'+old['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/preprocess/runs/legacy-unused').status_code, 404)
        self.assertEqual([r['id'] for r in self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()], ['legacy-latest'])
        service.initialize(Path(self.directory.name))
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json(), unit)

    def test_preview_confirmation_signature_and_project_scope(self):
        body = {**self.input, 'recipe': self.recipe}
        preview = self.client.post(f'/api/projects/{self.project}/preprocess/preview', json=body).json()
        self.assertEqual(preview['summary']['applied_count'], 2)
        endpoint = f'/api/projects/{self.project}/preprocess/runs'
        self.assertEqual(self.client.post(endpoint, json={**body, 'preview_signature': preview['signature']}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={**body, 'recipe': {**self.recipe, 'restore_repeat': False}, 'preview_signature': preview['signature'], 'confirm': True}).status_code, 409)
        foreign = self.client.post('/api/projects', json={'name': '无关项目'}).json()['id']
        self.assertEqual(self.client.post(f'/api/projects/{foreign}/preprocess/preview', json=body).status_code, 400)

    def test_review_conflict_lock_versions_and_source_preservation(self):
        run = self.create_run()
        edits = self.client.get(f'/api/preprocess/runs/{run["id"]}/edits').json()['items']
        missing = next(e for e in edits if e['original'] == '□')
        url = f'/api/preprocess/runs/{run["id"]}/review'
        body = {'expected_revision': 1, 'edit_id': missing['id'], 'accept': True, 'replacement': '天'}
        reviewed = self.client.post(url, json=body)
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        current = reviewed.json()
        self.assertEqual(self.client.post(f'/api/preprocess/runs/{run["id"]}/accept', json={'expected_revision': current['revision']}).status_code, 400)
        accepted = self.accept(current)
        self.assertEqual(self.client.post(url, json={**body, 'expected_revision': accepted['revision']}).status_code, 409)
        snapshot = self.client.get(f'/api/preprocess/runs/{run["id"]}/text').json()
        self.assertEqual(snapshot['original'], SOURCE)
        self.assertIn('缺字【天】', snapshot['prepared'])
        self.assertEqual(accepted['source_hash'], digest(SOURCE))
        exported = self.client.get(f'/api/preprocess/runs/{run["id"]}/export?format=json').json()
        self.assertEqual(json.loads(exported['content'])['output_hash'], digest(snapshot['prepared']))
        second = self.create_run({**self.recipe, 'restore_repeat': False})
        self.assertNotEqual(second['id'], run['id'])
        self.assertEqual(self.client.get(f'/api/preprocess/runs/{run["id"]}').status_code, 404)
        self.assertEqual([r['id'] for r in self.client.get(f'/api/projects/{self.project}/preprocess/runs').json()], [second['id']])
        self.assertEqual(self.client.get('/api/materials/'+self.material['id']).json()['pages'][0]['text'], SOURCE)

    def test_units_deduplicate_parent_and_propagate_pending_notes(self):
        recipe = {**self.recipe, 'rules': self.recipe['rules'] + [{'label': '门', 'level': 2, 'match': 'suffix', 'value': '门', 'max_length': 8}]}
        run = self.accept(self.create_run(recipe))
        nodes = self.client.get(f'/api/preprocess/runs/{run["id"]}/nodes').json()['items']
        volume = next(n for n in nodes if n['label'] == '卷')
        child = next(n for n in nodes if n['parent'] == volume['id'])
        url = f'/api/preprocess/runs/{run["id"]}/units'
        first = self.client.post(url, json={'node_ids': [volume['id'], child['id']]}).json()
        second = self.client.post(url, json={'node_ids': [volume['id']]}).json()
        self.assertEqual([u['id'] for u in first], [u['id'] for u in second])
        self.assertEqual(''.join(u['original_text'] for u in first), SOURCE[volume['start']:volume['end']])
        self.assertEqual(first[0]['preprocess']['run_id'], run['id'])
        self.assertIn('天地【天地', first[0]['content'])
        self.assertTrue(first[0]['notes'])
        unit = first[0]
        approve = self.client.put('/api/units/'+unit['id'], json={'expected_revision': unit['revision'], 'content': unit['content'], 'notes': unit['notes'], 'status': 'approved', 'confirm_review': True})
        self.assertEqual(approve.status_code, 400)

    def test_cleaned_text_is_consistent_in_preview_exports_and_complete_volume(self):
        recipe = {**self.recipe, 'remove_note_brackets': True, 'quote_spacing': True, 'paragraph_labels': BUILTINS[1].paragraph_labels}
        preview = self.client.post(f'/api/projects/{self.project}/preprocess/preview', json={**self.input, 'recipe': recipe}).json()
        sample = next(s for s in preview['samples'] if '天地【丨丨' in s['original'])
        self.assertIn('事类\n天地 天地之精', sample['formatted'])
        self.assertNotIn('【', sample['formatted'])
        self.assertIn('天地【天地', sample['prepared'])
        stale = self.client.post(f'/api/projects/{self.project}/preprocess/runs', json={**self.input, 'recipe': self.recipe, 'preview_signature': preview['signature'], 'confirm': True})
        self.assertEqual(stale.status_code, 409)
        old = self.accept(self.create_run())
        old_text = next(t for t in self.client.get(f'/api/preprocess/runs/{old["id"]}/texts').json()['items'] if t['kind'] == 'volume')
        old_unit = self.client.post(f'/api/preprocess/runs/{old["id"]}/units', json={'text_ids': [old_text['id']]}).json()[0]
        old_unit = self.client.get('/api/units/'+old_unit['id']).json()
        clean = self.accept(self.create_run(recipe))
        self.assertEqual(old['source_hash'], clean['source_hash'])
        self.assertEqual(old['output_hash'], clean['output_hash'])
        url = f'/api/preprocess/runs/{clean["id"]}'
        snapshot = self.client.get(url+'/text').json()
        self.assertEqual(snapshot['original'], SOURCE)
        self.assertNotIn('【', snapshot['formatted'])
        self.assertNotIn('】', snapshot['formatted'])
        texts = self.client.get(url+'/texts').json()['items']
        volume = next(t for t in texts if t['kind'] == 'volume')
        export = self.client.get(url+f'/export?text_id={volume["id"]}').json()['content']
        unit = self.client.post(url+'/units', json={'text_ids': [volume['id']]}).json()[0]
        self.assertEqual(unit['content'], export)
        self.assertEqual(unit['original_text'], SOURCE[volume['start']:volume['end']])
        self.assertIn('天地 天地之精\n日月 丨之光', unit['content'])
        self.assertTrue(unit['notes'])
        with zipfile.ZipFile(io.BytesIO(self.client.get(url+'/export?format=zip').content)) as archive:
            self.assertEqual(archive.read(volume['title']+'.txt').decode(), export)
        record = json.loads(self.client.get(url+'/export?format=json').json()['content'])
        self.assertTrue(record['recipe']['remove_note_brackets'])
        self.assertEqual(record['formatted_output_hash'], digest(snapshot['formatted']))
        self.assertIn('【', self.client.get(f'/api/preprocess/runs/{old["id"]}/export').json()['content'])
        self.assertEqual(self.client.get('/api/units/'+old_unit['id']).json(), old_unit)
        self.assertEqual(self.client.get('/api/materials/'+self.material['id']).json()['pages'][0]['text'], SOURCE)

    def test_agent_does_not_enable_bracket_removal_without_user_choice(self):
        self.configure()
        recipe = Recipe(name='格式识别', remove_note_brackets=True).model_dump_json()
        with patch.object(service, 'generate_text', return_value=recipe):
            job = self.client.post(f'/api/projects/{self.project}/preprocess/analyze', json=self.input).json()
            self.assertEqual(self.wait_job(job['id'])['status'], 'failed')

    def test_long_volume_remains_one_complete_text_and_work(self):
        text = 'SECTION A\n' + '天地玄黄宇宙洪荒\n'*900
        material = self.upload(text, filename='新格式.txt')
        recipe = Recipe(name='章节文本', rules=[{'label': '章', 'level': 1, 'match': 'prefix', 'value': 'SECTION ', 'split': True}]).model_dump()
        run = self.accept(self.create_run(recipe, {'material_id': material['id'], 'page_from': 1, 'page_to': material['page_count']}))
        node = self.client.get(f'/api/preprocess/runs/{run["id"]}/texts').json()['items'][0]
        result = self.client.post(f'/api/preprocess/runs/{run["id"]}/units', json={'text_ids': [node['id']]}).json()
        self.assertEqual(len(result), 1)
        self.assertEqual(''.join(u['original_text'] for u in result), text)
        self.assertEqual(''.join(u['content'] for u in result), text)
        self.assertGreater(len(result[0]['original_text']), 2400)
        self.assertTrue(all(u['page_from'] <= u['page_to'] for u in result))

    def test_bieji_fifth_volume_is_separate_named_text_and_zip_member(self):
        text = SOURCE.replace('前集卷一', '别集卷五').replace('前集卷二', '别集卷六')
        material = self.upload(text, filename='别集底本.txt')
        recipe = {**self.recipe, 'restore_repeat': False, 'split_notes': False}
        run = self.accept(self.create_run(recipe, {'material_id': material['id'], 'page_from': 1, 'page_to': material['page_count']}))
        texts = self.client.get(f'/api/preprocess/runs/{run["id"]}/texts').json()['items']
        self.assertEqual([t['title'] for t in texts], ['卷首材料', '别集第五卷', '别集第六卷'])
        self.assertEqual(''.join(text[t['start']:t['end']] for t in texts), text)
        fifth = texts[1]
        exported = self.client.get(f'/api/preprocess/runs/{run["id"]}/export?text_id={fifth["id"]}').json()
        self.assertEqual(exported['filename'], '别集第五卷.txt')
        self.assertEqual(exported['content'], text[fifth['start']:fifth['end']])
        self.assertNotIn('别集卷六', exported['content'])
        zipped = self.client.get(f'/api/preprocess/runs/{run["id"]}/export?format=zip')
        self.assertEqual(zipped.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            self.assertIn('别集第五卷.txt', archive.namelist())
            self.assertEqual(archive.read('别集第五卷.txt').decode(), exported['content'])
            self.assertEqual(''.join(archive.read(t['title']+'.txt').decode() for t in texts), text)

    def test_volume_selection_is_validated_and_saved_without_splitting_work(self):
        self.configure()
        run = self.accept(self.create_run())
        texts = self.client.get(f'/api/preprocess/runs/{run["id"]}/texts').json()['items']
        volume = next(t for t in texts if t['kind'] == 'volume')
        unit = self.client.post(f'/api/preprocess/runs/{run["id"]}/units', json={'text_ids': [volume['id']]}).json()[0]
        start = unit['content'].index('天地【天地')
        end = start+len('天地【天地之精】')
        selected = unit['content'][start:end]
        body = {'project_id': self.project, 'material_id': self.material['id'], 'unit_id': unit['id'], 'task_type': 'punctuate',
                'source_text': selected, 'unit_text_from': start, 'unit_text_to': end, 'page_from': unit['page_from'], 'page_to': unit['page_to']}
        self.assertEqual(self.client.post('/api/tasks', json={**body, 'source_text': '不符原文'}).status_code, 409)
        self.assertEqual(self.client.post('/api/tasks', json={**body, 'unit_text_to': None}).status_code, 400)
        with patch.object(service, 'generate_text', return_value=selected+'。'):
            task = self.client.post('/api/tasks', json=body).json()
            for thread in list(service.service.tasks):
                thread.join(5)
        result = self.client.get('/api/tasks/'+task['id']).json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['artifact']['source_context']['unit_selection'], {'start': start, 'end': end, 'source_text': selected})
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'], unit['content'])

    def test_agent_validation_and_retry(self):
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/preprocess/analyze', json=self.input).status_code, 400)
        self.configure()
        recipe = Recipe(name='模型识别的新格式', rules=[{'label': '门', 'level': 1, 'match': 'suffix', 'value': '门'}]).model_dump_json()
        with patch.object(service, 'generate_text', return_value='{"name":"恶意","code":"run()"}'):
            job = self.client.post(f'/api/projects/{self.project}/preprocess/analyze', json=self.input).json()
            self.assertEqual(self.wait_job(job['id'])['status'], 'failed')
        with patch.object(service, 'generate_text', return_value=recipe) as model:
            self.assertEqual(self.client.post('/api/preprocess/jobs/'+job['id']+'/resume').status_code, 200)
            completed = self.wait_job(job['id'])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['recipe']['name'], '模型识别的新格式')
        self.assertEqual(self.client.get(f'/api/projects/{self.project}/preprocess/runs').json(), [])

    def test_restart_reuses_saved_model_response(self):
        self.configure()
        recipe = Recipe(name='中断恢复', rules=[]).model_dump_json()
        with patch.object(service, 'generate_text', return_value=recipe):
            job = self.client.post(f'/api/projects/{self.project}/preprocess/analyze', json=self.input).json()
            self.assertEqual(self.wait_job(job['id'])['status'], 'completed')
        for thread in list(service.service.tasks):
            thread.join(3)
        with service.connection() as db:
            db.execute("UPDATE preprocess_jobs SET status='running',recipe_json='' WHERE id=?", (job['id'],))
        service.initialize(Path(self.directory.name))
        self.assertEqual(self.client.get('/api/preprocess/jobs/'+job['id']).json()['status'], 'paused')
        with patch.object(service, 'generate_text', side_effect=AssertionError('不应再次调用模型')):
            self.client.post('/api/preprocess/jobs/'+job['id']+'/resume')
            completed = self.wait_job(job['id'])
        self.assertEqual(completed['status'], 'completed')

    def test_prepared_punctuation_validates_before_saving(self):
        self.configure()
        run = self.accept(self.create_run())
        node = self.client.get(f'/api/preprocess/runs/{run["id"]}/nodes').json()['items'][0]
        unit = self.client.post(f'/api/preprocess/runs/{run["id"]}/units', json={'node_ids': [node['id']]}).json()[0]
        body = {'project_id': self.project, 'material_id': self.material['id'], 'unit_id': unit['id'], 'task_type': 'punctuate', 'source_text': unit['content'], 'page_from': unit['page_from'], 'page_to': unit['page_to']}
        with patch.object(service, 'generate_text', return_value='模型擅自改写原文'):
            task = self.client.post('/api/tasks', json=body).json()
            for thread in list(service.service.tasks):
                thread.join(5)
        completed = self.client.get('/api/tasks/'+task['id']).json()
        self.assertEqual(completed['status'], 'completed')
        self.assertIn('文字差异',completed['artifact']['source_context']['text_change_warning'])
        self.assertEqual(completed['artifact']['source_context']['preprocess']['run_id'], run['id'])
        self.assertEqual(self.client.get('/api/units/'+unit['id']).json()['content'],unit['content'])

    def test_pdf_partial_text_reports_missing_and_scan_only_rejects(self):
        document = pymupdf.open()
        document.new_page().insert_text((50, 50), 'SECTION A\nText source')
        document.new_page()
        material = self.upload('', filename='部分扫描.pdf', data=document.tobytes())
        document.close()
        body = {'material_id': material['id'], 'page_from': 1, 'page_to': 2, 'recipe': Recipe(name='保留').model_dump()}
        preview = self.client.post(f'/api/projects/{self.project}/preprocess/preview', json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(any('缺失' in w for w in preview.json()['summary']['warnings']))
        body['page_from'] = 2
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/preprocess/preview', json=body).status_code, 400)

    def test_upgrade_preserves_existing_work_and_creates_backup(self):
        original = self.client.post(f'/api/projects/{self.project}/units', json={'title': '升级前工作', 'material_id': self.material['id'], 'page_from': 1, 'page_to': 1}).json()
        with service.connection() as db:
            db.execute('DROP TABLE preprocess_runs')
            db.execute('DROP TABLE preprocess_recipes')
            db.execute('DROP TABLE preprocess_jobs')
            db.execute('ALTER TABLE research_units DROP COLUMN preprocess_json')
        service.initialize(Path(self.directory.name))
        backup = Path(self.directory.name)/'backups'/'before-0.3.0.sqlite'
        self.assertTrue(backup.exists())
        upgraded = self.client.get('/api/units/'+original['id']).json()
        self.assertEqual(upgraded['original_text'], original['original_text'])
        self.assertEqual(upgraded['content'], original['content'])
        self.assertEqual(upgraded['revision'], 1)
        self.assertEqual(upgraded['preprocess'], {})
        created = self.client.post(f'/api/projects/{self.project}/units', json={'title': '升级后直接录文', 'material_id': self.material['id'], 'page_from': 1, 'page_to': 1})
        self.assertEqual(created.status_code, 200, created.text)


if __name__ == '__main__':
    unittest.main()
