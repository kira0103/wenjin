"""Word export for one collated volume.

The generator is deliberately deterministic: the point-collated draft is plain
text, so a small set of layout rules turns it into a Word manuscript.  It never
includes pending notes; those stay in the web workspace.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FONT_SONG = '宋体'
FONT_HEI = '黑体'
DEFAULT_MARGINS_CM = {'top': 2.5, 'bottom': 2.5, 'left': 3.0, 'right': 2.5}
LABELS = {'事类', '诗集', '詩集'}
PUA_RE = re.compile('[\ue000-\uf8ff]')
REPLACEMENT_RE = re.compile('[\ufffd□]')
ENTRY_RE = re.compile(r'^([^\s\u3000]{1,12})[ \u3000]+(.+)$')
PUNCTUATION_RE = re.compile(r'[，。；：？！、“”‘’《》]')
BOOK_TITLE_RE = re.compile(r'《([^》]{1,40})》')


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
    text = _clean(content)
    return {'pua': len(PUA_RE.findall(text)), 'replacement': len(REPLACEMENT_RE.findall(text))}


def _book_title(content: str, project_name: str, unit_title: str) -> str:
    for line in _lines(content):
        match = BOOK_TITLE_RE.search(line)
        if match:
            return match.group(1)
    project = re.sub(r'[_-]?点校$', '', _clean(project_name)).strip()
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


def build(content: str, unit_title: str, project_name: str, *, status: str = '') -> tuple[bytes, dict]:
    """Return (docx bytes, warnings). The caller owns the response metadata."""
    warnings = detect_warnings(content)
    book = _book_title(content, project_name, unit_title)
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
    run = header.paragraphs[0].add_run(f'《{book}》 · {unit_title}')
    _set_font(run, FONT_SONG, 9, color=RGBColor(0x66, 0x66, 0x66))
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
    author_line = next((line.strip() for line in list(_lines(content))[:3] if _is_author(line)), '')
    author = document.add_paragraph()
    author.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = author.add_run(author_line or '問津　點校')
    _set_font(run, FONT_SONG, 14)
    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = note.add_run(datetime.now().strftime('%Y-%m-%d'))
    _set_font(run, FONT_SONG, 10, color=RGBColor(0x88, 0x88, 0x88))
    document.add_page_break()

    # 卷题
    document.add_heading(unit_title, level=1)
    lines = list(_lines(content))
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
            run = p.add_run(text)
            _set_font(run, FONT_HEI, 12, bold=True)
            continue
        if _is_author(text):
            p = document.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
            run = p.add_run(text)
            _set_font(run, FONT_SONG, 14)
            continue
        if text.endswith('门') and len(text) <= 12:
            document.add_heading(text, level=2)
            continue
        if raw.startswith('\u3000\u3000') and len(text) <= 8 and not PUNCTUATION_RE.search(text):
            document.add_heading(text, level=3)
            continue
        match = ENTRY_RE.match(text)
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _first_line(p, 2)
        if match:
            lead = p.add_run(match.group(1))
            _set_font(lead, FONT_SONG, 12, bold=True)
            body = p.add_run('　' + match.group(2))
            _set_font(body, FONT_SONG, 12)
        else:
            run = p.add_run(text)
            _set_font(run, FONT_SONG, 12)

    buffer = io.BytesIO()
    document.core_properties.title = f'{book} {unit_title} 點校稿'
    document.core_properties.author = '問津'
    document.save(buffer)
    return buffer.getvalue(), warnings
