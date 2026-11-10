"""Word export for one collated volume.

The generator is deliberately deterministic: the point-collated draft is plain
text, so a small set of layout rules turns it into a Word manuscript.  It never
includes pending notes; those stay in the web workspace.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime
from itertools import groupby
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FONT_SONG = '宋体'
FONT_HEI = '黑体'
# 扩展 B 区（U+20000 以上）常用字体没有字形，指定 Windows 自带的 SimSun-ExtB 以免再显示方框。
FONT_EXT_B = 'SimSun-ExtB'
DEFAULT_MARGINS_CM = {'top': 2.5, 'bottom': 2.5, 'left': 3.0, 'right': 2.5}
LABELS = {'事类', '诗集', '詩集'}
PUA_RE = re.compile('[\ue000-\uf8ff]')
REPLACEMENT_RE = re.compile('[\ufffd□]')
ENTRY_RE = re.compile(r'^([^\s\u3000]{1,12})[ \u3000]+(.+)$')
PUNCTUATION_RE = re.compile(r'[，。；：？！、“”‘’《》]')
BOOK_TITLE_RE = re.compile(r'《([^》]{1,40})》')
UNRESOLVED_MARK = '□'
PUA_DATA = Path(__file__).resolve().parents[1] / 'data' / 'siku_pua.json'
_PUA_ENTRIES = None


def pua_entries() -> dict:
    """四库全书私用区码位映射（Wikisource Module:SKchar，CC BY-SA 4.0）。"""
    global _PUA_ENTRIES
    if _PUA_ENTRIES is None:
        try:
            with PUA_DATA.open(encoding='utf-8') as handle:
                _PUA_ENTRIES = json.load(handle).get('entries') or {}
        except (OSError, ValueError):
            _PUA_ENTRIES = {}
    return _PUA_ENTRIES


def resolve_pua(content: str) -> tuple[str, int, list]:
    """把可还原的私用区字替换成标准 Unicode 字；无法还原的统一显示为 □。

    返回 (替换后的正文, 已还原数量, 未还原项列表)。未还原项包含原码位、SK 编号、
    描述和上下文，供导出文档末尾/ZIP 清单让人工逐条处理。
    """
    entries = pua_entries()
    parts, last, mapped, unresolved = [], 0, 0, []
    for match in re.finditer('[\ue000-\uf8ff\ufffd]', content):
        char, start = match.group(), match.start()
        parts.append(content[last:start])
        if char == '\ufffd':
            entry = None
        else:
            entry = entries.get('%04X' % ord(char))
        if entry and entry.get('char'):
            parts.append(entry['char'])
            mapped += 1
        else:
            parts.append(UNRESOLVED_MARK)
            context = re.sub('[\ue000-\uf8ff\ufffd]', UNRESOLVED_MARK,
                             content[max(0, start - 14):start + 15]).replace('\r', ' ').replace('\n', ' ')
            unresolved.append({
                'offset': start,
                'codepoint': 'U+%04X' % ord(char),
                'sk': (entry or {}).get('sk'),
                'description': (entry or {}).get('description') or ('替换字符（缺字）' if char == '\ufffd' else '需要人工核对'),
                'context': context,
            })
        last = match.end()
    parts.append(content[last:])
    return ''.join(parts), mapped, unresolved


def _add_mixed_runs(paragraph, text, size, *, font=FONT_SONG, bold=False, color=None):
    """按字符分区加字体：扩展 B 区字符单独用 SimSun-ExtB，避免缺字方框。"""
    if not text:
        return
    for is_ext, chars in groupby(text, key=lambda char: ord(char) > 0xFFFF):
        run = paragraph.add_run(''.join(chars))
        _set_font(run, FONT_EXT_B if is_ext else font, size, bold=bold, color=color)
# 这些是“通用项目名”，不能当作书名；只有它们才回退到正文里找《书名》。
GENERIC_PROJECT_NAMES = {'', '我的研究', '研究工作', '研究项目', '未命名项目', '点校稿', '工作稿', '项目'}


def _clean(value) -> str:
    return str(value or '')


def _set_font(run, name=FONT_SONG, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None:
        fonts = OxmlElement('w:rFonts')
        rpr.append(fonts)
    fonts.set(qn('w:eastAsia'), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _set_style_font(style, name, size, bold=False):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.bold = bold
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None:
        fonts = OxmlElement('w:rFonts')
        rpr.append(fonts)
    for key in ('w:eastAsia', 'w:ascii', 'w:hAnsi'):
        fonts.set(qn(key), name)


def _first_line(paragraph, chars=2):
    ppr = paragraph._p.get_or_add_pPr()
    ind = ppr.find(qn('w:ind'))
    if ind is None:
        ind = OxmlElement('w:ind')
        ppr.append(ind)
    ind.set(qn('w:firstLineChars'), str(chars * 100))
    ind.set(qn('w:firstLine'), str(chars * 240))


def _page_field(paragraph):
    run = paragraph.add_run()
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = ' PAGE '
    separate = OxmlElement('w:fldChar')
    separate.set(qn('w:fldCharType'), 'separate')
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    for node in (begin, instr, separate, end):
        run._r.append(node)


def _lines(content: str):
    for raw in re.split(r'\r\n|\r|\n', _clean(content)):
        line = raw.rstrip(' \t\u3000')
        if line.strip():
            yield line


def detect_warnings(content: str) -> dict:
    _, mapped, unresolved = resolve_pua(content)
    pua = sum(1 for item in unresolved if item['codepoint'] != 'U+FFFD')
    return {'pua': pua, 'replacement': len(unresolved) - pua, 'mapped': mapped}


def _project_book_title(project_name: str) -> str:
    """项目名去掉“点校”后缀就是这部书的书名（批量导出必须一致）。"""
    return re.sub(r'[\s_-]?(?:点校|點校)$', '', _clean(project_name)).strip()


def _book_title(content: str, project_name: str, unit_title: str) -> str:
    """书名优先取项目名；通用项目名才回退到正文里第一个《书名》。

    旧逻辑无条件扫描正文，导致文件名变成该卷第一个引用的典籍名
    （如《左傳》《呂刑》《說文》），整批导出时书名忽大忽小、每卷不同。
    """
    project = _project_book_title(project_name)
    if project and project not in GENERIC_PROJECT_NAMES:
        return project
    for line in _lines(content):
        match = BOOK_TITLE_RE.search(line)
        if match:
            return match.group(1)
    return project or _clean(unit_title) or '工作稿'


def _safe_name(value: str, fallback: str = '点校稿') -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', _clean(value)).strip(' ._')
    return cleaned or fallback


def filename_for(project_name: str, unit_title: str, content: str) -> str:
    book = _safe_name(_book_title(content, project_name, unit_title), '点校稿')
    unit = _safe_name(unit_title, '工作稿')
    today = datetime.now().strftime('%Y-%m-%d')
    return f'{book}_{unit}_点校稿_{today}.docx'


def _contains_volume_title(line: str) -> bool:
    return bool(BOOK_TITLE_RE.search(line)) or ('卷' in line and len(line.strip()) <= 40)


def _is_author(line: str) -> bool:
    return '撰' in line and len(line.strip()) <= 40


def build(content: str, unit_title: str, project_name: str, *, status: str = '',
          include_unresolved_list: bool = False) -> tuple[bytes, dict]:
    """Return (docx bytes, warnings). The caller owns the response metadata.

    正文中的私用区字符先按四库全书僻字表还原成标准 Unicode；无法还原的显示为
    □，并在 warnings['unresolved'] 中返回位置/编码/上下文，供单卷文末清单和
    批量 ZIP 清单使用。
    """
    resolved, mapped, unresolved = resolve_pua(content)
    pua_unresolved = sum(1 for item in unresolved if item['codepoint'] != 'U+FFFD')
    warnings = {'pua': pua_unresolved, 'replacement': len(unresolved) - pua_unresolved,
                'mapped': mapped, 'unresolved': unresolved}
    book = _book_title(resolved, project_name, unit_title)
    document = Document()

    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(DEFAULT_MARGINS_CM['top']), Cm(DEFAULT_MARGINS_CM['bottom'])
    section.left_margin, section.right_margin = Cm(DEFAULT_MARGINS_CM['left']), Cm(DEFAULT_MARGINS_CM['right'])
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.75)
    section.different_first_page_header_footer = True

    normal = document.styles['Normal']
    _set_style_font(normal, FONT_SONG, 12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)

    h1 = document.styles['Heading 1']
    _set_style_font(h1, FONT_SONG, 22, bold=True)
    h1.font.color.rgb = RGBColor(0, 0, 0)
    h1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h1.paragraph_format.space_before = Pt(0)
    h1.paragraph_format.space_after = Pt(18)
    h1.paragraph_format.keep_with_next = True
    h2 = document.styles['Heading 2']
    _set_style_font(h2, FONT_HEI, 15, bold=True)
    h2.font.color.rgb = RGBColor(0, 0, 0)
    h2.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h2.paragraph_format.space_before = Pt(18)
    h2.paragraph_format.space_after = Pt(8)
    h2.paragraph_format.keep_with_next = True
    h3 = document.styles['Heading 3']
    _set_style_font(h3, FONT_HEI, 12, bold=True)
    h3.font.color.rgb = RGBColor(0, 0, 0)
    h3.paragraph_format.space_before = Pt(12)
    h3.paragraph_format.space_after = Pt(4)
    h3.paragraph_format.keep_with_next = True

    header = section.header
    header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_mixed_runs(header.paragraphs[0], f'《{book}》 · {unit_title}', 9, color=RGBColor(0x66, 0x66, 0x66))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _page_field(footer)
    for run in footer.runs:
        _set_font(run, FONT_SONG, 9)

    # 封面
    for _ in range(4):
        document.add_paragraph()
    cover = document.add_paragraph()
    cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = cover.add_run(f'《{book}》')
    _set_font(run, FONT_SONG, 36, bold=True)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_before = Pt(14)
    run = subtitle.add_run(f'{unit_title}　點校稿')
    _set_font(run, FONT_SONG, 20)
    for _ in range(6):
        document.add_paragraph()
    author_line = next((line.strip() for line in list(_lines(resolved))[:3] if _is_author(line)), '')
    author = document.add_paragraph()
    author.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_mixed_runs(author, author_line or '問津　點校', 14)
    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = note.add_run(datetime.now().strftime('%Y-%m-%d'))
    _set_font(run, FONT_SONG, 10, color=RGBColor(0x88, 0x88, 0x88))
    document.add_page_break()

    # 卷题
    document.add_heading(unit_title, level=1)
    lines = list(_lines(resolved))
    skip_title = False
    if lines and _contains_volume_title(lines[0]) and ('卷' in lines[0] or BOOK_TITLE_RE.search(lines[0])):
        skip_title = True

    for index, raw in enumerate(lines):
        if skip_title and index == 0:
            continue
        text = raw.lstrip('\u3000 ')
        if not text:
            continue
        if text in LABELS:
            p = document.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(4)
            _add_mixed_runs(p, text, 12, font=FONT_HEI, bold=True)
            continue
        if _is_author(text):
            p = document.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
            _add_mixed_runs(p, text, 14)
            continue
        if text.endswith('门') and len(text) <= 12:
            heading = document.add_paragraph(style='Heading 2')
            _add_mixed_runs(heading, text, 15, font=FONT_HEI, bold=True)
            continue
        if raw.startswith('\u3000\u3000') and len(text) <= 8 and not PUNCTUATION_RE.search(text):
            heading = document.add_paragraph(style='Heading 3')
            _add_mixed_runs(heading, text, 12, font=FONT_HEI, bold=True)
            continue
        match = ENTRY_RE.match(text)
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _first_line(p, 2)
        if match:
            _add_mixed_runs(p, match.group(1), 12, bold=True)
            _add_mixed_runs(p, '　' + match.group(2), 12)
        else:
            _add_mixed_runs(p, text, 12)

    if include_unresolved_list and unresolved:
        document.add_page_break()
        document.add_heading('附：未能自动还原的私用区字符', level=2)
        hint = document.add_paragraph()
        _add_mixed_runs(hint, f'共 {len(unresolved)} 处，导出的 Word 中以 □ 显示；请对照善本影印或原字表逐条补正。', 10,
                        color=RGBColor(0x66, 0x66, 0x66))
        for number, item in enumerate(unresolved, 1):
            detail = document.add_paragraph()
            line = f'{number}. {item["codepoint"]}'
            if item.get('sk'):
                line += f'（SK {item["sk"]}）'
            line += f' {item["description"]}｜上下文：{item["context"]}'
            _add_mixed_runs(detail, line, 9)

    buffer = io.BytesIO()
    document.core_properties.title = f'{book} {unit_title} 點校稿'
    document.core_properties.author = '問津'
    document.save(buffer)
    return buffer.getvalue(), warnings
