"""善本影印对照：把按卷切好的《古今合璧事类备要》宋刻本 PDF 挂到点校稿复核页。

点校项目里的“准备卷文本”已经把四库底本拆成 366 卷（前集/后集/续集/别集/外集），
每个工作单元标题形如“前集第一卷”。本模块只做两件事：

* 把工作单元的“集 + 卷次”解析出来，对应到 ``02_影印/按卷_v6`` 下的善本 PDF；
* 以只读方式把 PDF 交给复核页左栏（底稿原文、校订稿仍保存在工作单元里）。

PDF 目录可以用环境变量 ``SHIXUE_SIKU_PDF_DIR`` 覆盖；默认取项目目录下的
``中华再造善本_合璧事类备要/02_影印/按卷_v6``。卷表读取 ``卷表_切分清单.csv``。
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_PDF_DIR = PROJECT / '中华再造善本_合璧事类备要' / '02_影印' / '按卷_v6'
INDEX_NAME = '卷表_切分清单.csv'

JI_ORDER = ('前集', '後集', '續集', '別集', '外集')
JI_ALIASES = {
    '前集': '前集', '前': '前集',
    '后集': '後集', '後集': '後集', '后': '後集', '後': '後集',
    '续集': '續集', '續集': '續集', '绩集': '續集', '績集': '續集', '续': '續集', '續': '續集',
    '别集': '別集', '別集': '別集', '别': '別集', '別': '別集',
    '外集': '外集', '外': '外集',
}
DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '兩': 2, '三': 3, '四': 4,
          '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
UNITS = {'十': 10, '百': 100}
NUMBER_PATTERN = r'[0-9〇零一二三四五六七八九十百两兩]+'

# 形如“前集第一卷”“古今合璧事类备要前集卷一”“後集卷二十五”都能解析。
COLLECTION = re.compile(r'(前集|后集|後集|续集|續集|绩集|績集|别集|別集|外集)')
VOLUME_PATTERNS = (
    re.compile(r'(前集|后集|後集|续集|續集|绩集|績集|别集|別集|外集)\s*第?\s*(' + NUMBER_PATTERN + r')\s*[卷巻]'),
    re.compile(r'第\s*(' + NUMBER_PATTERN + r')\s*[卷巻]'),
    re.compile(r'[卷巻]\s*(?:之)?\s*(' + NUMBER_PATTERN + r')'),
)


def parse_number(value: str | None) -> int | None:
    """解析卷次数字：阿拉伯数字，或一至一百的中文数字（二十一、九十四等）。"""
    text = (value or '').strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    current = 0
    for char in text:
        if char in DIGITS:
            current = DIGITS[char]
        elif char in UNITS:
            total += (current or 1) * UNITS[char]
            current = 0
        else:
            return None
    if not total and not current:
        return None
    return total + current


def parse_volume_title(*values: str) -> tuple[str, int] | None:
    """从工作单元标题/层级里解析（集, 卷次）；识别不了时返回 None。"""
    text = ' '.join(str(value or '').strip() for value in values if str(value or '').strip())
    if not text:
        return None
    collection = COLLECTION.search(text)
    if not collection:
        return None
    ji = JI_ALIASES.get(collection.group(1))
    if not ji:
        return None
    for index, pattern in enumerate(VOLUME_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        number = parse_number(match.group(2) if index == 0 else match.group(1))
        if number:
            return ji, number
    return None


def pdf_dir() -> Path:
    override = os.environ.get('SHIXUE_SIKU_PDF_DIR')
    return Path(override).expanduser() if override else DEFAULT_PDF_DIR


_CACHE: dict[str, object] = {'key': None, 'volumes': []}


def volumes() -> list[dict]:
    """读取卷表；目录或卷表变化时自动重新加载（按路径 + 修改时间缓存）。"""
    directory = pdf_dir()
    index = directory / INDEX_NAME
    try:
        key = (str(directory.resolve()), index.stat().st_mtime_ns if index.is_file() else 0)
    except OSError:
        key = (str(directory), 0)
    if _CACHE.get('key') == key:
        return list(_CACHE.get('volumes') or [])  # type: ignore[arg-type]
    loaded: list[dict] = []
    if index.is_file():
        try:
            with index.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            rows = []
        for row in rows:
            try:
                seq = int((row.get('全书卷序') or '').strip())
            except ValueError:
                continue
            ji = JI_ALIASES.get((row.get('集') or '').strip(), '')
            juan = parse_number(row.get('卷次'))
            if not ji or not juan:
                continue
            try:
                pages = int((row.get('页数') or '0').strip() or 0)
            except ValueError:
                pages = 0
            loaded.append({'seq': seq, 'ji': ji, 'juan': juan,
                           'pages': pages, 'file': (row.get('文件') or '').strip()})
    loaded.sort(key=lambda item: item['seq'])
    _CACHE['key'] = key
    _CACHE['volumes'] = loaded
    return list(loaded)


def find_volume(ji: str, juan: int) -> dict | None:
    return next((volume for volume in volumes() if volume['ji'] == ji and volume['juan'] == juan), None)


def match_row(row: dict) -> dict:
    """把 research_units 的一行解析成善本 PDF 匹配结果（不访问文件系统之外的内容）。"""
    parsed = parse_volume_title(row.get('title'), row.get('volume'), row.get('category'))
    if not parsed:
        return {'available': False, 'pdf_dir': str(pdf_dir()), 'volume_count': len(volumes()),
                'reason': '工作单元标题里没有识别到“集 + 卷次”（如“前集第一卷”），可在左栏手动选择善本卷。'}
    ji, juan = parsed
    volume = find_volume(ji, juan)
    if not volume:
        return {'available': False, 'ji': ji, 'juan': juan, 'pdf_dir': str(pdf_dir()),
                'volume_count': len(volumes()), 'reason': f'卷表里没有“{ji}第{juan}卷”对应的善本 PDF。'}
    path = pdf_dir() / volume['file']
    if not volume['file'] or not path.is_file():
        return {'available': False, 'ji': ji, 'juan': juan, 'pdf_dir': str(pdf_dir()),
                'volume_count': len(volumes()), 'reason': f'善本 PDF 文件不存在：{volume["file"]}。'}
    return {'available': True, 'seq': volume['seq'], 'ji': ji, 'juan': juan,
            'pages': volume['pages'], 'file': volume['file'], 'matched_by': 'title',
            'pdf_dir': str(pdf_dir())}


def register(app, connection):
    """把善本影印接口挂到主应用；connection() 是 app.py 里的工作区 SQLite 连接。"""

    def unit_row(unit_id: str) -> dict:
        with connection() as db:
            row = db.execute('SELECT id,title,volume,category FROM research_units WHERE id=?',
                             (unit_id,)).fetchone()
        if not row:
            raise HTTPException(404, '找不到该工作单元')
        return dict(row)

    def pdf_response(filename: str) -> FileResponse:
        path = pdf_dir() / filename
        if not filename or not path.is_file():
            raise HTTPException(404, f'善本 PDF 不存在：{filename}')
        return FileResponse(path, media_type='application/pdf',
                            filename=filename, content_disposition_type='inline')

    @app.get('/api/siku/volumes')
    def siku_volumes():
        catalog = volumes()
        return {'available': bool(catalog), 'pdf_dir': str(pdf_dir()),
                'volume_count': len(catalog), 'volumes': catalog}

    @app.get('/api/siku/volumes/{seq}/file')
    def siku_volume_pdf(seq: int):
        volume = next((item for item in volumes() if item['seq'] == seq), None)
        if not volume:
            raise HTTPException(404, f'没有编号 {seq} 的善本卷')
        return pdf_response(volume['file'])

    @app.get('/api/units/{unit_id}/siku')
    def unit_siku(unit_id: str):
        return match_row(unit_row(unit_id))

    @app.get('/api/units/{unit_id}/siku/file')
    def unit_siku_pdf(unit_id: str):
        match = match_row(unit_row(unit_id))
        if not match.get('available'):
            raise HTTPException(404, match.get('reason') or '未匹配到善本 PDF')
        return pdf_response(str(match['file']))
