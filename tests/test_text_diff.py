import unittest,json
from backend.text_diff import compare_text
from backend import collation
import test_collation as fixture

class DiffTests(unittest.TestCase):
    def verify(self,source,output):
        value=compare_text(source,output)
        self.assertEqual(''.join(s['text'] for s in value['source_spans']),source)
        self.assertEqual(''.join(s['text'] for s in value['result_spans']),output)
        return value
    def test_replacements_and_deletion_are_visible_on_both_sides(self):
        value=self.verify('甲相乙葢丙','甲乙盖丙。')
        self.assertTrue(any(s['kind']=='removed' and '相' in s['text'] for s in value['source_spans']))
        self.assertTrue(any(s['kind']=='changed' and '葢' in s['text'] for s in value['source_spans']))
        self.assertTrue(any(s['kind']=='changed' and '盖' in s['text'] for s in value['result_spans']))
        self.assertEqual(value['inserted_punctuation'],1)
    def test_addition_reordering_unicode_and_whitespace_are_not_hidden(self):
        value=self.verify('𠀀甲乙丙丁\n','𠀀丙丁甲乙。\n')
        self.assertTrue(any(s['kind']=='removed' for s in value['source_spans']))
        self.assertTrue(any(s['kind']=='added' for s in value['result_spans']))
        self.assertGreater(value['changed_groups'],0)
        addition=self.verify('天地','天新地。')
        self.assertTrue(any(s['kind']=='added' and s['text']=='新' for s in addition['result_spans']))
        self.verify('甲\n  乙','甲乙。')
    def test_insertions_do_not_mark_original_punctuation_as_new(self):
        value=self.verify('𠀀天。地\n','𠀀，天。地！\n')
        self.assertEqual(value['inserted_punctuation'],2)
        self.assertEqual(value['changed_groups'],0)
        changed=self.verify('葢，','盖、')
        self.assertFalse(any(s['kind']=='punctuation' for s in changed['source_spans']))
        self.assertEqual(changed['inserted_punctuation'],1)
    def test_large_repeated_text_retains_every_character(self):
        source='天地玄黄\n'*2500
        self.verify(source,source.replace('天地','天地，')+'。')
    def test_changes_without_punctuation_are_reviewable_but_empty_or_length_is_invalid(self):
        reply={'text':json.dumps({'text':'盖','questions':[]})}
        self.assertEqual(collation.validate_reply('葢',reply,require_progress=True).text,'盖')
        with self.assertRaises(ValueError):collation.validate_reply('葢',{'text':'{"text":"","questions":[]}'})
        with self.assertRaises(ValueError):collation.validate_reply('葢',{**reply,'finish_reason':'length'})

class ReviewFlowTests(unittest.TestCase):
    setUp=fixture.BatchTests.setUp
    tearDown=fixture.BatchTests.tearDown
    upload=fixture.BatchTests.upload
    create=fixture.BatchTests.create
    execute=fixture.BatchTests.execute
    get=fixture.BatchTests.get
    items=fixture.BatchTests.items
    def test_recover_prior_fidelity_failures_with_no_paid_request_or_receipt_rewrite(self):
        batch=self.create([self.texts[0]['id']])
        with fixture.service.connection() as db:
            chunk=db.execute('SELECT * FROM collation_chunks LIMIT 1').fetchone()
            source=chunk['input_text']
            reply={'text':json.dumps({'text':source.replace('天地','天帝')+'。','questions':[]}), 'finish_reason':'stop'}
            db.execute('INSERT INTO collation_attempts(id,batch_id,item_id,chunk_id,budget_id,status,response_json,error,started_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       ('old-invalid',batch['id'],chunk['item_id'],chunk['id'],chunk['root_id'],'invalid',json.dumps(reply),'标点结果未通过原字校验：原文字符位置 5 附近改字',fixture.service.utc_now()))
        self.assertEqual(self.execute(),0)
        self.assertEqual(self.get(batch)['counts']['done'],1)
        item=self.items(batch)[0];self.assertTrue(item['has_text_changes'])
        unit=self.client.get('/api/units/'+item['unit_id']).json()
        self.assertEqual(unit['status'],'review');self.assertIn('天帝',unit['content'])
        self.assertEqual(self.client.get('/api/units/'+unit['id']+'/baseline').json()['baseline'],source)
        with fixture.service.connection() as db:
            self.assertEqual(db.execute("SELECT status FROM collation_attempts WHERE id='old-invalid'").fetchone()[0],'invalid')
    def test_compare_endpoint_is_read_only_and_contains_colored_spans(self):
        value=self.client.post('/api/collation/compare',json={'baseline':'相葢天地','content':'辅盖天新。'}).json()
        self.assertGreater(value['changed_groups'],0)
        self.assertTrue(any(s['kind']=='changed' for s in value['source_spans']))
