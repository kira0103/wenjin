"""生成“英文期刊库”数据文件 backend/data/english_journals.json.gz。

数据来自 ShowJCR 仓库（https://github.com/hitfyd/ShowJCR）附带的 jcr.db：
  FQBJCR2025（中科院分区表升级版 2025）、JCR2025（Clarivate 2025 版）、XR2026（新锐期刊分区表 2026）。

用法（先自行下载 jcr.db，例如解压 ShowJCR.7z）：

    python scripts/build_english_journals.py --db D:\\ShowJCR\\jcr.db
    python scripts/build_english_journals.py --db jcr.db --out backend/data/english_journals.json.gz

生成文件不提交仓库（见 .gitignore）；打包时由 package.json 的 --add-data "backend/data;backend/data" 带入。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'backend' / 'data' / 'english_journals.json.gz'


def norm(name: str) -> str:
    value = unicodedata.normalize('NFKC', name or '').upper().replace('&', ' AND ')
    value = re.sub(r'[^\w\s]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def cas_tier(raw):
    match = re.match(r'\s*(\d)', str(raw or ''))
    return int(match.group(1)) if match else None


def xr_tier(raw):
    match = re.search(r'(\d)\s*区', str(raw or ''))
    return int(match.group(1)) if match else None


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build(db_path: Path) -> dict:
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    records: dict[str, dict] = {}

    def bucket(name):
        key = norm(name)
        if not key:
            return None
        return records.setdefault(key, {})

    for row in cur.execute('SELECT * FROM FQBJCR2025'):
        item = bucket(row['Journal'])
        if item is None:
            continue
        smalls = []
        for i in range(1, 7):
            field, tier = row[f'小类{i}'], row[f'小类{i}分区']
            if field:
                smalls.append(f'{field} ({tier})' if tier else field)
        item['cas'] = {
            'name': row['Journal'], 'issn': row['ISSN/EISSN'] or '', 'wos': row['Web of Science'] or '',
            'cat': row['大类'] or '', 'tier': cas_tier(row['大类分区']),
            'top': (row['Top'] or '').strip() == '是', 'rank': row['大类分区'] or '',
            'small': '; '.join(smalls), 'review': (row['Review'] or '').strip() == '是',
            'oa': (row['Open Access'] or '').strip() == '是', 'flag': row['标注'] or '',
        }
    for row in cur.execute('SELECT * FROM JCR2025'):
        item = bucket(row['Journal'])
        if item is None:
            continue
        cats = []
        for i in range(1, 7):
            field, quartile = row[f'Category_{i}'], row[f'IF Quartile(2025)_{i}']
            if field:
                cats.append(f'{field} [{quartile}]' if quartile else field)
        issn = '/'.join(v for v in (row['ISSN'], row['EISSN']) if v and v != 'N/A')
        item['jcr'] = {
            'name': row['Journal'], 'issn': issn, 'wos': row['Web of Science'] or '',
            'if': number(row['IF(2025)']), 'q': row['IF Quartile(2025)_1'] or '',
            'cats': '; '.join(cats),
        }
    for row in cur.execute('SELECT * FROM XR2026'):
        item = bucket(row['Journal'])
        if item is None or 'xr' in item:
            continue
        smalls = []
        for i in range(1, 5):
            field, tier = row[f'小类{i}中文名'], row[f'小类{i}新锐分区']
            if field:
                smalls.append(f'{field} ({tier})' if tier else field)
        issn = '/'.join(v for v in (row['ISSN'], row['EISSN']) if v and v != '-')
        item['xr'] = {
            'name': row['Journal'], 'issn': issn, 'wos': row['数据库'] or '',
            'cat': row['大类中文名'] or '', 'tier': xr_tier(row['大类新锐分区']),
            'top': (row['Top'] or '').strip() == 'Top', 'small': '; '.join(smalls),
            'flag': row['预警标记'] or '',
        }
    con.close()

    journals = []
    for item in records.values():
        cas, jcr, xr = item.get('cas'), item.get('jcr'), item.get('xr')
        if not (cas or jcr or xr):
            continue
        journals.append({
            'name': (cas and cas['name']) or (jcr and jcr['name']) or (xr and xr['name']),
            'issn': (cas and cas['issn']) or (jcr and jcr['issn']) or (xr and xr['issn']) or '',
            'wos': (cas and cas['wos']) or (jcr and jcr['wos']) or (xr and xr['wos']) or '',
            'jif': jcr['if'] if jcr else None,
            'jcr_q': jcr['q'] if jcr else '',
            'jcr_cats': jcr['cats'] if jcr else '',
            'cas_cat': cas['cat'] if cas else '',
            'cas_tier': cas['tier'] if cas else None,
            'cas_top': bool(cas and cas['top']),
            'cas_rank': cas['rank'] if cas else '',
            'cas_small': cas['small'] if cas else '',
            'cas_review': bool(cas and cas['review']),
            'cas_oa': bool(cas and cas['oa']),
            'cas_flag': cas['flag'] if cas else '',
            'xr_cat': xr['cat'] if xr else '',
            'xr_tier': xr['tier'] if xr else None,
            'xr_top': bool(xr and xr['top']),
            'xr_small': xr['small'] if xr else '',
            'xr_flag': xr['flag'] if xr else '',
        })
    journals.sort(key=lambda entry: entry['name'].upper())
    for index, entry in enumerate(journals, 1):
        entry['id'] = f'en{index:05d}'

    counts: dict[str, int] = {}
    for entry in journals:
        discipline = entry['cas_cat'] or '未收录中科院分区'
        counts[discipline] = counts.get(discipline, 0) + 1
    disciplines = [{'name': name, 'count': count} for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {
        'meta': {
            'label': '英文期刊分区对照库',
            'generated_at': '2026-10-20',
            'count': len(journals),
            'sources': {
                'jcr': 'Clarivate Journal Citation Reports 2025 版（2026 年 6 月发布，含 IF 与四分位）',
                'cas': '中科院期刊分区表升级版 2025（advanced.fenqubiao.com，最后一版官方中科院分区）',
                'xr': '新锐期刊分区表 2026（xr-scholar.com，2026 年 3 月发布）',
            },
            'notes': [
                '中科院分区取大类分区；JCR 取最佳（最优）四分位，学科明细见 jcr_cats。',
                '新锐 2026 已把预警名单并入，预警刊标记 Under Review（xr_flag）。',
            ],
            'disciplines': disciplines,
        },
        'journals': journals,
    }


def main():
    parser = argparse.ArgumentParser(description='从 ShowJCR 的 jcr.db 生成英文期刊库数据')
    parser.add_argument('--db', required=True, type=Path, help='jcr.db 路径（ShowJCR 仓库或发行包内）')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT, help='输出 .json.gz 路径')
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f'找不到数据库：{args.db}')
    payload = build(args.db)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, 'wb', compresslevel=9) as handler:
        handler.write(raw)
    print(f"已生成 {args.out}：{len(payload['journals'])} 本期刊，{len(raw)} 字节（压缩后 {args.out.stat().st_size}）")


if __name__ == '__main__':
    main()
