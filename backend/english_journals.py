"""英文期刊库：JCR 2025 / 中科院 2025 / 新锐 2026 分区对照（只读静态数据）。

数据来自 ``data/english_journals.json.gz``，不建表、不写数据库；接口只需注册路由。
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Query

DATA = Path(__file__).parent / 'data' / 'english_journals.json.gz'
# JCR 只有 Q1–Q4 参与分区筛选；“N/A”与空值都视为未分区。
JCR_ORDER = {'Q1': 1, 'Q2': 2, 'Q3': 3, 'Q4': 4}
SORTS = ('if', 'cas', 'jcr', 'xr', 'name')
# meta.disciplines 把“没有中科院大类”的期刊汇总成这一项，筛选时对应 cas_cat 为空。
UNRANKED_DISCIPLINE = '未收录中科院分区'


@lru_cache(maxsize=1)
def load():
    """读取数据文件；进程内只解析一次。"""
    return _payload(DATA)


def _payload(path):
    """读取并校验 gzip JSON 数据；测试可直接传入临时路径。"""
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            payload = json.load(stream)
    except FileNotFoundError as error:
        raise HTTPException(503, f'英文期刊库数据缺失：{path}') from error
    except (OSError, ValueError) as error:
        raise HTTPException(503, f'英文期刊库数据无法读取：{error}') from error
    if not isinstance(payload, dict) or not isinstance(payload.get('journals'), list):
        raise HTTPException(503, f'英文期刊库数据格式错误：{path}')
    return payload


def _number(value):
    """仅把真正的数字当作数值（布尔值不算）。"""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _name_key(journal):
    return (journal.get('name') or '').strip().casefold()


def _if_key(journal):
    """IF 降序；无 IF 排最后。"""
    value = _number(journal.get('jif'))
    return (value is None, -(value or 0.0))


def _match(journal, needle, discipline, jcr, cas, xr, top, xrtop):
    if needle:
        haystack = f"{journal.get('name') or ''}\n{journal.get('issn') or ''}".casefold()
        if needle not in haystack:
            return False
    if discipline:
        if discipline == UNRANKED_DISCIPLINE:
            if journal.get('cas_cat'):
                return False
        elif journal.get('cas_cat') != discipline:
            return False
    if jcr is not None:
        rank = JCR_ORDER.get(journal.get('jcr_q') or '')
        if rank is None or rank > jcr:
            return False
    if cas is not None:
        tier = _number(journal.get('cas_tier'))
        if tier is None or tier > cas:
            return False
    if xr is not None:
        tier = _number(journal.get('xr_tier'))
        if tier is None or tier > xr:
            return False
    if top and journal.get('cas_top') is not True:
        return False
    if xrtop and journal.get('xr_top') is not True:
        return False
    return True


def filter_journals(payload, q='', discipline='', jcr=None, cas=None, xr=None, top=False, xrtop=False):
    """按契约过滤，返回原始数据顺序的期刊列表。"""
    needle = (q or '').strip().casefold()
    discipline = (discipline or '').strip()
    return [journal for journal in payload.get('journals') or []
            if _match(journal, needle, discipline, jcr, cas, xr, top, xrtop)]


def sort_journals(journals, sort='if'):
    """按契约排序；空值（无 IF / 未收录分区）一律排最后。"""
    if sort not in SORTS:
        raise ValueError(f'未知排序方式：{sort}')
    if sort == 'name':
        return sorted(journals, key=lambda journal: (_name_key(journal), journal.get('id') or ''))
    if sort == 'jcr':
        def key(journal):
            rank = JCR_ORDER.get(journal.get('jcr_q') or '')
            return (rank is None, rank or 0, *_if_key(journal), _name_key(journal), journal.get('id') or '')
        return sorted(journals, key=key)
    field = {'if': 'jif', 'cas': 'cas_tier', 'xr': 'xr_tier'}[sort]

    def key(journal):
        value = _number(journal.get(field))
        if sort == 'if':
            return (*_if_key(journal), _name_key(journal), journal.get('id') or '')
        # 同分区内按 IF 降序；未收录该体系分区（null）排最后。
        return (value is None, value or 0, *_if_key(journal), _name_key(journal), journal.get('id') or '')
    return sorted(journals, key=key)


def query_journals(payload, q='', discipline='', jcr=None, cas=None, xr=None, top=False,
                   xrtop=False, sort='if', page=1, page_size=50):
    """过滤 + 排序 + 分页，返回接口响应体。"""
    if page < 1:
        raise ValueError('page 必须不小于 1')
    if not 1 <= page_size <= 200:
        raise ValueError('page_size 必须在 1–200 之间')
    items = sort_journals(filter_journals(payload, q=q, discipline=discipline, jcr=jcr, cas=cas,
                                          xr=xr, top=top, xrtop=xrtop), sort)
    total = len(items)
    pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    return {'total': total, 'page': page, 'page_size': page_size, 'pages': pages,
            'items': items[start:start + page_size], 'meta': payload.get('meta') or {}}


def register(app, connection=None):
    """注册英文期刊库只读接口（不需要数据库连接）。"""
    @app.get('/api/submissions/english-journals')
    def english_journals(
        q: str = Query('', max_length=200, description='刊名或 ISSN 关键字，不区分大小写'),
        discipline: str = Query('', max_length=120, description='中科院大类，精确匹配'),
        jcr: int | None = Query(None, ge=1, le=4, description='JCR 分区 n 区及以上'),
        cas: int | None = Query(None, ge=1, le=4, description='中科院分区 n 区及以上'),
        xr: int | None = Query(None, ge=1, le=4, description='新锐分区 n 区及以上'),
        top: bool = Query(False, description='只看中科院 Top'),
        xrtop: bool = Query(False, description='只看新锐 Top'),
        sort: str = Query('if', pattern='^(if|cas|jcr|xr|name)$'),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ):
        return query_journals(load(), q=q, discipline=discipline, jcr=jcr, cas=cas, xr=xr,
                              top=top, xrtop=xrtop, sort=sort, page=page, page_size=page_size)
