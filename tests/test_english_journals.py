"""英文期刊库后端测试：真实数据契约 + 临时小数据集上的过滤/分页/错误分支。"""
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import english_journals as ej

BASE = '/api/submissions/english-journals'
HAS_DATA = ej.DATA.is_file()
SKIP_DATA = '缺少英文期刊库真实数据（backend/data/english_journals.json.gz），跳过真实数据用例'


def journal(identity, name, issn='', jif=None, jcr_q='', cas_cat='', cas_tier=None, cas_top=False,
            xr_tier=None, xr_top=False, wos='SCIE', jcr_cats=''):
    """构造一条与数据文件字段一致的最小期刊记录。"""
    return {
        'id': identity, 'name': name, 'issn': issn, 'wos': wos, 'jif': jif, 'jcr_q': jcr_q,
        'jcr_cats': jcr_cats, 'cas_cat': cas_cat, 'cas_tier': cas_tier, 'cas_top': cas_top,
        'cas_rank': '', 'cas_small': '', 'cas_review': False, 'cas_oa': False, 'cas_flag': '',
        'xr_cat': '', 'xr_tier': xr_tier, 'xr_top': xr_top, 'xr_small': '', 'xr_flag': '',
    }


SMALL = {
    'meta': {'label': '测试英文期刊库', 'generated_at': '2026-10-20', 'count': 5,
             'disciplines': [{'name': '教育学', 'count': 2}, {'name': '未收录中科院分区', 'count': 1}]},
    'journals': [
        journal('en1', 'ACADEMIC MEDICINE', '1040-2446/1938-808X', 4.5, 'Q1', '教育学', 1, True, 1, True),
        journal('en2', 'EDUCATION RESEARCH', '0013-161X', 2.0, 'Q2', '教育学', 2, False, 2, False),
        # 无 IF、无 JCR 分区、无新锐分区，用于验证空值排最后。
        journal('en3', 'HISTORY TODAY', '0018-2753', None, '', '历史学', 3, False, None, False),
        journal('en4', 'Medicine Journal', '1234-5678', 10.0, 'Q1', '医学', 1, True, None, False),
        # JCR “N/A”、无中科院大类，但属于“未收录中科院分区”汇总。
        journal('en5', 'JOURNAL OF TESTS', '9876-5432', 1.0, 'N/A', '', None, False, 1, True),
    ],
}


def ids(journals):
    return [item['id'] for item in journals]


def temporary_root():
    """临时文件放在工作区内（与项目其它测试一致，便于沙箱环境运行）。"""
    root = Path(__file__).resolve().parents[1] / '.local' / 'temporary'
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_payload_file(small=SMALL):
    """把临时小数据写成 gzip 文件，返回路径（用 mkstemp 避免沙箱目录权限问题）。"""
    handle, name = tempfile.mkstemp(dir=temporary_root(), prefix='english-journals-', suffix='.json.gz')
    os.close(handle)
    path = Path(name)
    with gzip.open(path, 'wb') as stream:
        stream.write(json.dumps(small, ensure_ascii=False).encode('utf-8'))
    return path


class FilterLogicTest(unittest.TestCase):
    """纯函数：过滤 / 排序 / 分页。"""

    def test_text_search_matches_name_and_issn_ignoring_case(self):
        payload = SMALL
        self.assertEqual(ids(ej.filter_journals(payload, q='medicine')), ['en1', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, q='education')), ['en2'])
        self.assertEqual(ids(ej.filter_journals(payload, q='1040-2446')), ['en1'])
        self.assertEqual(ids(ej.filter_journals(payload, q='ACADEMIC')), ['en1'])
        self.assertEqual(ids(ej.filter_journals(payload, q='   ')), ids(SMALL['journals']))
        self.assertEqual(ids(ej.filter_journals(payload, q='没有这本刊')), [])

    def test_discipline_exact_match_and_unranked_bucket(self):
        payload = SMALL
        self.assertEqual(ids(ej.filter_journals(payload, discipline='教育学')), ['en1', 'en2'])
        self.assertEqual(ids(ej.filter_journals(payload, discipline='教育')), [])
        # meta.disciplines 的“未收录中科院分区”对应 cas_cat 为空。
        self.assertEqual(ids(ej.filter_journals(payload, discipline=ej.UNRANKED_DISCIPLINE)), ['en5'])

    def test_jcr_filter_keeps_ranked_only(self):
        payload = SMALL
        self.assertEqual(ids(ej.filter_journals(payload, jcr=1)), ['en1', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, jcr=2)), ['en1', 'en2', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, jcr=4)), ['en1', 'en2', 'en4'])

    def test_tier_filters_exclude_unknown(self):
        payload = SMALL
        self.assertEqual(ids(ej.filter_journals(payload, cas=1)), ['en1', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, cas=2)), ['en1', 'en2', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, cas=4)), ['en1', 'en2', 'en3', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, xr=1)), ['en1', 'en5'])
        self.assertEqual(ids(ej.filter_journals(payload, xr=2)), ['en1', 'en2', 'en5'])

    def test_top_flags_and_combined_filters(self):
        payload = SMALL
        self.assertEqual(ids(ej.filter_journals(payload, top=True)), ['en1', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, xrtop=True)), ['en1', 'en5'])
        self.assertEqual(ids(ej.filter_journals(payload, q='medicine', cas=2, jcr=3)), ['en1', 'en4'])
        self.assertEqual(ids(ej.filter_journals(payload, discipline='教育学', top=True)), ['en1'])

    def test_sort_if_puts_missing_if_last(self):
        self.assertEqual(ids(ej.sort_journals(SMALL['journals'], 'if')), ['en4', 'en1', 'en2', 'en5', 'en3'])

    def test_sort_cas_and_xr_ascending_with_if_tiebreak(self):
        self.assertEqual(ids(ej.sort_journals(SMALL['journals'], 'cas')), ['en4', 'en1', 'en2', 'en3', 'en5'])
        self.assertEqual(ids(ej.sort_journals(SMALL['journals'], 'xr')), ['en1', 'en5', 'en2', 'en4', 'en3'])

    def test_sort_jcr_quartile_order_then_unknown(self):
        self.assertEqual(ids(ej.sort_journals(SMALL['journals'], 'jcr')), ['en4', 'en1', 'en2', 'en5', 'en3'])

    def test_sort_name_case_insensitive(self):
        self.assertEqual(ids(ej.sort_journals(SMALL['journals'], 'name')), ['en1', 'en2', 'en3', 'en5', 'en4'])

    def test_unknown_sort_raises(self):
        with self.assertRaises(ValueError):
            ej.sort_journals(SMALL['journals'], 'if-asc')

    def test_query_pagination_and_page_overflow(self):
        payload = SMALL
        page = ej.query_journals(payload, page=1, page_size=2)
        self.assertEqual((page['total'], page['page'], page['page_size'], page['pages']), (5, 1, 2, 3))
        self.assertEqual(ids(page['items']), ['en4', 'en1'])
        self.assertEqual(ids(ej.query_journals(payload, page=3, page_size=2)['items']), ['en3'])
        overflow = ej.query_journals(payload, page=9, page_size=2)
        self.assertEqual(overflow['items'], [])
        self.assertEqual((overflow['total'], overflow['pages']), (5, 3))
        empty = ej.query_journals(payload, q='没有这本刊')
        self.assertEqual((empty['total'], empty['pages'], empty['items']), (0, 0, []))

    def test_query_passes_meta_through(self):
        self.assertEqual(ej.query_journals(SMALL)['meta'], SMALL['meta'])

    def test_query_rejects_out_of_range_page_size(self):
        for page_size in (0, 201, -1):
            with self.assertRaises(ValueError):
                ej.query_journals(SMALL, page_size=page_size)
        with self.assertRaises(ValueError):
            ej.query_journals(SMALL, page=0)


class ApiWithSmallDataTest(unittest.TestCase):
    """接口层：用临时小数据验证参数、分页与 503/422 分支。"""

    def setUp(self):
        self.path = write_payload_file()
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.patch = patch.object(ej, 'DATA', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        ej.load.cache_clear()
        self.addCleanup(ej.load.cache_clear)
        app = FastAPI()
        ej.register(app)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, **params):
        response = self.client.get(BASE, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_response_contract(self):
        body = self.get()
        self.assertEqual((body['total'], body['page'], body['page_size'], body['pages']), (5, 1, 50, 1))
        self.assertEqual(ids(body['items']), ['en4', 'en1', 'en2', 'en5', 'en3'])
        self.assertEqual(body['meta'], SMALL['meta'])
        self.assertEqual(set(body['items'][0]), set(SMALL['journals'][0]))

    def test_filters_through_api(self):
        self.assertEqual(ids(self.get(discipline='教育学')['items']), ['en1', 'en2'])
        self.assertEqual(ids(self.get(q='history')['items']), ['en3'])
        self.assertEqual(ids(self.get(jcr=1, cas=2)['items']), ['en4', 'en1'])
        self.assertEqual(ids(self.get(top='true', xrtop='true')['items']), ['en1'])
        self.assertEqual(ids(self.get(xrtop='true')['items']), ['en1', 'en5'])

    def test_sort_through_api(self):
        self.assertEqual(ids(self.get(sort='name')['items']), ['en1', 'en2', 'en3', 'en5', 'en4'])
        self.assertEqual(ids(self.get(sort='cas')['items']), ['en4', 'en1', 'en2', 'en3', 'en5'])

    def test_page_size_and_overflow_through_api(self):
        body = self.get(page=2, page_size=2)
        self.assertEqual(ids(body['items']), ['en2', 'en5'])
        self.assertEqual((body['total'], body['pages']), (5, 3))
        overflow = self.get(page=4, page_size=2)
        self.assertEqual(overflow['items'], [])
        self.assertEqual((overflow['total'], overflow['pages']), (5, 3))
        self.assertEqual(len(self.get(page_size=200)['items']), 5)

    def test_invalid_parameters_return_422(self):
        for params in ({'page_size': 201}, {'page_size': 0}, {'page': 0}, {'jcr': 5}, {'cas': 0},
                       {'xr': 5}, {'sort': 'if-asc'}):
            response = self.client.get(BASE, params=params)
            self.assertEqual(response.status_code, 422, (params, response.text))

    def test_missing_data_returns_503(self):
        missing = self.path.with_name(self.path.name + '.missing')
        with patch.object(ej, 'DATA', missing):
            ej.load.cache_clear()
            response = self.client.get(BASE)
        ej.load.cache_clear()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('英文期刊库数据缺失', response.json()['detail'])

    def test_corrupt_data_returns_503(self):
        broken = self.path.with_name(self.path.name + '.broken')
        broken.write_bytes(b'not a gzip file')
        self.addCleanup(broken.unlink, missing_ok=True)
        with patch.object(ej, 'DATA', broken):
            ej.load.cache_clear()
            response = self.client.get(BASE)
        ej.load.cache_clear()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('英文期刊库数据', response.json()['detail'])

    def test_payload_loader_accepts_path(self):
        payload = ej._payload(self.path)
        self.assertEqual(payload['meta']['count'], 5)
        self.assertEqual(len(payload['journals']), 5)


@unittest.skipUnless(HAS_DATA, SKIP_DATA)
class RealDataTest(unittest.TestCase):
    """真实数据：核对数量、用户场景与排序/分页契约。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = ej.load()
        app = FastAPI()
        ej.register(app)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def get(self, **params):
        response = self.client.get(BASE, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_dataset_shape(self):
        self.assertEqual(self.payload['meta']['count'], 23113)
        self.assertEqual(len(self.payload['journals']), 23113)
        self.assertEqual(len(self.payload['meta']['disciplines']), 22)
        self.assertEqual(len({item['id'] for item in self.payload['journals']}), 23113)
        self.assertEqual(self.get()['total'], 23113)

    def test_user_scenario_education_cas2_jcr3(self):
        body = self.get(discipline='教育学', cas=2, jcr=3, page_size=50)
        self.assertEqual(body['total'], 107)
        self.assertEqual(body['pages'], 3)
        self.assertEqual(len(body['items']), 50)
        for item in body['items']:
            self.assertEqual(item['cas_cat'], '教育学')
            self.assertIsNotNone(item['cas_tier'])
            self.assertLessEqual(item['cas_tier'], 2)
            self.assertIn(item['jcr_q'], {'Q1', 'Q2', 'Q3'})

    def test_discipline_counts_match_meta(self):
        for entry in self.payload['meta']['disciplines']:
            body = self.get(discipline=entry['name'], page_size=1)
            self.assertEqual(body['total'], entry['count'], entry['name'])

    def test_top_and_quartile_filters_are_subsets(self):
        total = self.get(page_size=1)['total']
        top = self.get(top='true', page_size=200)
        self.assertLess(top['total'], total)
        self.assertTrue(all(item['cas_top'] is True for item in top['items']))
        xrtop = self.get(xrtop='true', page_size=1)
        self.assertLess(xrtop['total'], total)
        q1 = self.get(jcr=1, page_size=200)
        self.assertEqual({item['jcr_q'] for item in q1['items']}, {'Q1'})
        self.assertLess(q1['total'], total)

    def test_pagination_edges(self):
        first = self.get(page=1, page_size=50)
        self.assertEqual(first['pages'], 463)
        self.assertEqual(len(first['items']), 50)
        last = self.get(page=463, page_size=50)
        self.assertEqual(len(last['items']), 13)
        beyond = self.get(page=464, page_size=50)
        self.assertEqual(beyond['items'], [])
        self.assertEqual((beyond['total'], beyond['pages']), (23113, 463))
        big = self.get(page=116, page_size=200)
        self.assertEqual(big['pages'], 116)
        self.assertEqual(len(big['items']), 113)

    def test_sort_if_puts_largest_if_first(self):
        largest = max(item['jif'] for item in self.payload['journals'] if item['jif'] is not None)
        first = self.get(sort='if', page_size=200)
        self.assertEqual(first['items'][0]['jif'], largest)
        values = [item['jif'] for item in first['items']]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_sort_jcr_starts_with_q1(self):
        body = self.get(sort='jcr', page_size=200)
        self.assertEqual({item['jcr_q'] for item in body['items']}, {'Q1'})
        values = [item['jif'] for item in body['items']]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_sort_cas_and_xr_ascending_with_unknown_last(self):
        for sort, field, expected_tier in (('cas', 'cas_tier', 1), ('xr', 'xr_tier', 1)):
            body = self.get(sort=sort, page_size=200)
            self.assertEqual({item[field] for item in body['items']}, {expected_tier})
            values = [item['jif'] for item in body['items']]
            self.assertEqual(values, sorted(values, reverse=True))

    def test_sort_name_is_case_insensitive(self):
        names = [item['name'] for item in self.get(sort='name', page_size=200)['items']]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_text_search_matches_name_and_issn(self):
        body = self.get(q='academic medicine', page_size=50)
        self.assertGreater(body['total'], 0)
        for item in body['items']:
            needle = 'academic medicine'
            self.assertIn(needle, (item['name'] + '\n' + item['issn']).casefold())
        by_issn = self.get(q='1040-2446', page_size=50)
        self.assertGreater(by_issn['total'], 0)
        self.assertTrue(any('1040-2446' in item['issn'] for item in by_issn['items']))
