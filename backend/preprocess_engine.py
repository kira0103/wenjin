"""Declarative text preparation. Model output is data, never executable code."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ENGINE_VERSION = '1.4'

# The user's parser treats these as paragraph labels, not volume boundaries.
GENERAL_LABELS = ('古今源流', '古今姓纂', '古今始末', '姓纂', '始末', '格物总论', '格物丛话', '格物藂话',
                  '歴代沿革', '厯代沿革', '历代沿革', '歴代防革', '厯代防革', '歴代源流', '厯代源流',
                  '事理发挥', '事理?挥', '礼经考索', '舆地提纲', '器物丛谈', '器物总论', '身章撮要',
                  '刑法总论', '财货源流', '宫室举要', '音乐防归', '平粜总论')
_TRADITIONAL = str.maketrans('类诗总论丛话发挥礼经舆纲谈财宫乐归货举历粜', '類詩總論叢話發揮禮經輿綱談財宮樂歸貨舉歷糶')
HEBI_PARAGRAPH_LABELS = list(dict.fromkeys(label for value in ('事理', '事类', '诗集', *GENERAL_LABELS)
                                         for label in (value, value.translate(_TRADITIONAL))))


class HeadingRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=30)
    level: int = Field(ge=1, le=6)
    match: Literal['prefix', 'suffix', 'exact', 'contains', 'short_line', 'volume', 'hebi_volume', 'numbered_title']
    value: str = Field(default='', max_length=200)
    max_length: int = Field(default=60, ge=1, le=300)
    split: bool = False
    require_next: list[str] = Field(default=[], max_length=8)
    exclude_next: list[str] = Field(default=[], max_length=8)


class Recipe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    rules: list[HeadingRule] = Field(default=[], max_length=20)
    restore_repeat: bool = False
    repeat_mode: Literal['exact_count', 'by_headword'] = 'exact_count'
    split_notes: bool = False
    remove_note_brackets: bool = False
    quote_spacing: bool = False
    paragraph_labels: list[str] = Field(default=[], max_length=80)
    merge_repeated_titles: bool = True

    @model_validator(mode='after')
    def require_values(self):
        if not self.name.strip():
            raise ValueError('方案名称不能为空')
        if any(not value.strip() or len(value) > 100 or any(c in value for c in '\r\n【】') for value in self.paragraph_labels):
            raise ValueError('段落小标题需为 1–100 字的非空单行文字，不能包含引文括号')
        for rule in self.rules:
            if rule.match in {'prefix', 'suffix', 'exact', 'contains', 'numbered_title'} and not rule.value.strip():
                raise ValueError('文字匹配规则需要填写匹配内容')
            if any(not value.strip() or len(value) > 100 for value in rule.require_next + rule.exclude_next):
                raise ValueError('相邻行条件需为 1–100 字的非空关键词')
        return self


BUILTINS = [Recipe(name='保留原文（不识别层级）', description='保留全部录文，可让 Agent 根据实际内容提出卷文本拆分规则。'), Recipe(
    name='古今合璧事类备要', description='按实际卷题拆分，一卷一份完整文本，例如“别集第五卷”。识别独立卷号和书名内卷号，区分重复卷尾；序跋、四库题名、作者及元数据完整保留。',
    rules=[HeadingRule(label='卷', level=1, match='volume', split=True), HeadingRule(label='卷', level=1, match='hebi_volume', split=True)],
    split_notes=True, remove_note_brackets=True, quote_spacing=True, paragraph_labels=HEBI_PARAGRAPH_LABELS)]

NUMBER = r'[一二三四五六七八九十百千万零〇两兩\d]+'
VOLUME = re.compile(rf'^[卷巻]\s*{NUMBER}\s*$')
HEBI = re.compile(rf'^古今合[璧壁]事[类類]?[备備偹]要\s*(?:前集|后集|後集|续集|續集|别集|別集|外集)?\s*[卷巻]?\s*{NUMBER}\s*$')
HEBI_CANDIDATE = re.compile(rf'^古今合.{{4,30}}[卷巻]\s*{NUMBER}\s*$')
DECORATION = re.compile(r'^(?:钦定四库[全合]书.*|欽定四庫全書.*|古今合[璧壁]事[类類]备要\s+类书类|宋\s*谢维新\s*撰|宋\s*謝維新\s*撰|<.*>)\s*$')


def digest(value) -> str:
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def signature(source: str, recipe: Recipe, positions: list) -> str:
    return digest({'source': digest(source), 'recipe': recipe.model_dump(), 'positions': positions, 'engine': ENGINE_VERSION})


def samples(source: str) -> str:
    # Sampling includes the middle and end, as well as short heading candidates.
    starts = sorted({0, max(0, len(source)//2-1400), max(0, len(source)-2800)})
    values = [f'【原文字符位置 {n}，样例】\n{source[n:n+2800]}' for n in starts]
    candidates = []
    offset = 0
    for line in source.splitlines(keepends=True):
        text = line.strip()
        if 0 < len(text) <= 50 and '【' not in text:
            candidates.append(f'{offset}: {text}')
        offset += len(line)
    if len(candidates) > 65:
        candidates = [candidates[i*len(candidates)//65] for i in range(65)]
    return '\n\n'.join(values) + '\n\n【分散的短行候选，可能是正文或卷尾】\n' + '\n'.join(candidates)


def analyze(source: str, recipe: Recipe) -> dict:
    lines, offset = [], 0
    for line in source.splitlines(keepends=True):
        lines.append((offset, offset + len(line), line.strip()))
        offset += len(line)
    headings, warnings = [], []
    previous_nonempty = ''
    following = [''] * len(lines)
    next_nonempty = ''
    for i in range(len(lines)-1, -1, -1):
        following[i] = next_nonempty
        if lines[i][2]:
            next_nonempty = lines[i][2]
    hebi_mode = any(r.match == 'hebi_volume' for r in recipe.rules)
    for i, (start, _end, text) in enumerate(lines):
        if not text:
            continue
        prior = previous_nonempty
        previous_nonempty = text
        if DECORATION.fullmatch(text):
            continue
        for rule in recipe.rules:
            if len(text) > rule.max_length:
                continue
            if rule.require_next and not any(following[i].startswith(value) for value in rule.require_next):
                continue
            if rule.exclude_next and any(following[i].startswith(value) for value in rule.exclude_next):
                continue
            matched = {'prefix': lambda: text.startswith(rule.value), 'suffix': lambda: text.endswith(rule.value),
                       'exact': lambda: text == rule.value, 'contains': lambda: rule.value in text,
                       'numbered_title': lambda: bool(re.search(rf'{re.escape(rule.value)}\s*第?\s*{NUMBER}|第?\s*{NUMBER}\s*{re.escape(rule.value)}', text)),
                       'volume': lambda: bool(VOLUME.fullmatch(text)), 'hebi_volume': lambda: bool(HEBI.fullmatch(text)) or bool(HEBI_CANDIDATE.fullmatch(text) and re.match(r'^宋\s*[谢謝]维?維?新\s*撰', following[i])),
                       'short_line': lambda: not any(c in text for c in '【】。，：；！？丨□<>') and not text.startswith(('事类', '诗集', *GENERAL_LABELS))}[rule.match]()
            if not matched:
                continue
            if hebi_mode and (HEBI.fullmatch(text) or HEBI_CANDIDATE.fullmatch(text)):
                after = following[i]
                if after.startswith(('<', '钦定四库', '欽定四庫')) or not after:
                    break  # Repeated footer remains in the raw text.
                if VOLUME.fullmatch(prior):
                    break  # Standalone volume already established this boundary.
                if not re.match(r'^宋\s*[谢謝]维?維?新\s*撰', after):
                    warnings.append(f'字符 {start}：卷题未紧接作者行，需检查“{text}”是否为卷首。')
                if not HEBI.fullmatch(text):
                    warnings.append(f'字符 {start}：卷题有异体、疑字或集名异常；据作者行识别边界，保留原题“{text}”，请核对。')
                if not re.search(r'(前|后|後|续|續|别|別|外)集', text):
                    warnings.append(f'字符 {start}：未能确认集名，未自动继承上卷集名；请核对“{text}”。')
            headings.append({'id': f'n-{start}', 'title': text, 'label': rule.label, 'level': rule.level, 'start': start, 'end': len(source), 'parent': '', 'path': '', 'split': rule.split})
            break
    if not headings:
        warnings.append('未识别到标题层级；全部文字保存在“未分类文本”。请调整规则或让 Agent 识别格式。')
    if any(rule.match == 'short_line' for rule in recipe.rules):
        warnings.append('短行标题使用启发式识别；短正文、目录条目可能混入，请结合标题候选与原文检查。')
    nodes, stack = [], []
    if not headings or headings[0]['start'] > 0:
        nodes.append({'id': 'n-front', 'title': '未分类文本 / 卷首材料', 'label': '原文', 'level': 1, 'start': 0,
                      'end': headings[0]['start'] if headings else len(source), 'parent': '', 'path': '未分类文本 / 卷首材料'})
    for node in headings:
        while stack and stack[-1]['level'] >= node['level']:
            stack.pop()['end'] = node['start']
        node['parent'] = stack[-1]['id'] if stack else ''
        node['path'] = ' / '.join([n['title'] for n in stack] + [node['title']])
        nodes.append(node)
        stack.append(node)
    texts, split_warnings = split_texts(source, nodes, recipe)
    warnings.extend(split_warnings)
    covered = bool(texts) and texts[0]['start'] == 0 and texts[-1]['end'] == len(source) and all(a['end'] == b['start'] for a, b in zip(texts, texts[1:]))
    edits = []
    bracket_stack = []
    for match in re.finditer(r'【|】|□|[\ue000-\uf8ff]', source):
        pos, char = match.start(), match.group()
        if char == '【':
            if bracket_stack:
                edits.append(_edit(pos, char, char, 'bracket', '嵌套括号，不能按普通词头注释自动恢复', 'pending', source))
            bracket_stack.append(pos)
        elif char == '】':
            if bracket_stack:
                bracket_stack.pop()
            else:
                edits.append(_edit(pos, char, char, 'bracket', '多出的右括号；原文保留', 'pending', source))
        else:
            edits.append(_edit(pos, char, char, 'lacuna' if char == '□' else 'glyph', '缺字占位，需查底本' if char == '□' else '私用区字形，需查底本', 'pending', source))
    for pos in bracket_stack:
        edits.append(_edit(pos, '【', '【', 'bracket', '未闭合的左括号；原文保留', 'pending', source))
    if recipe.restore_repeat:
        seen_marks = set()
        head_prefixes = sorted(set(HEBI_PARAGRAPH_LABELS + recipe.paragraph_labels), key=lambda value: (-len(value), value))
        # Per head【note】, never across lines/units. Fixed basic processing follows the user's sequential headword restoration; legacy recipes retain exact-count behavior.
        for line_start, line_end, text in lines:
            actual = source[line_start:line_end].rstrip('\r\n')
            if '丨' not in actual:
                continue
            depth, nested = 0, False
            for char in actual:
                if char == '【':
                    depth += 1
                    nested = nested or depth > 1
                elif char == '】':
                    depth -= 1
            if nested or depth != 0:
                continue
            # Preserve offsets, including leading whitespace; match on the original line.
            for unit in re.finditer(r'([^【】]*)【([^【】]*)】', actual):
                head, note = unit.group(1).strip(), unit.group(2)
                for prefix in head_prefixes:
                    if head.startswith(prefix):
                        head = head[len(prefix):].strip()
                        break
                marks = [m.start() for m in re.finditer('丨', note)]
                if not marks:
                    continue
                safe_head = bool(head) and len(head) <= 30 and not any(c in head for c in '。，：；！？丨□?') and not any('\ue000' <= c <= '\uf8ff' for c in head)
                high = safe_head and len(marks) == len(head)
                for k, local in enumerate(marks):
                    pos = line_start + unit.start(2) + local
                    seen_marks.add(pos)
                    replacement = head[k % len(head)] if safe_head else '丨'
                    apply = high or safe_head and recipe.repeat_mode == 'by_headword'
                    reason = '数量相等，按词头顺序恢复' if high else '按词头顺序循环恢复，数量不等需点校核对' if apply else '对应关系不确定，需人工核对'
                    edit = _edit(pos, '丨', replacement, 'repeat', f'重文号对应词头“{head[:30]}”；'+reason, 'applied' if apply else 'pending', source)
                    if apply and not high:
                        edit['needs_review'] = True
                    edits.append(edit)
        for match in re.finditer('丨', source):
            if match.start() not in seen_marks:
                edits.append(_edit(match.start(), '丨', '丨', 'repeat', '无法确定重文号所属词头，保留原文待核', 'pending', source))
    edits.sort(key=lambda e: e['start'])
    return {'nodes': nodes, 'texts': texts, 'text_count': len(texts), 'split_count': sum(t['kind'] != 'frontmatter' for t in texts), 'engine_version': ENGINE_VERSION,
            'edits': edits, 'warnings': warnings[:200], 'warning_count': len(warnings), 'coverage_ok': covered,
            'source_chars': len(source), 'heading_count': len(headings), 'pending_count': sum(e['status'] == 'pending' for e in edits),
            'applied_count': sum(e['status'] == 'applied' for e in edits)}


def split_texts(source: str, nodes: list, recipe: Recipe) -> tuple[list, list]:
    legacy = not any('split' in node for node in nodes)
    # Legacy recipes infer boundaries from actual volume candidates, never child entries.
    candidates = [n for n in nodes if n.get('split') or legacy and n.get('label') == '卷']
    boundaries, warnings = [], []
    for node in candidates:
        if recipe.merge_repeated_titles and boundaries and re.sub(r'\s+', '', node['title']) == re.sub(r'\s+', '', boundaries[-1]['title']):
            warnings.append(f'字符 {node["start"]}：重复拆分题名“{node["title"]}”保留在当前文本中，没有另拆一份。')
            continue
        boundaries.append(node)
    texts = []
    if not boundaries:
        warnings.append('未识别到拆分边界；保留为一份完整文本。请检查关键词与样例，或让 Agent 重新识别。')
        return [_text(0, len(source), '完整文本（未识别拆分边界）', '', 'text')], warnings
    if boundaries[0]['start'] > 0:
        texts.append(_text(0, boundaries[0]['start'], '卷首材料', '', 'frontmatter'))
    for i, node in enumerate(boundaries):
        title = node['title']
        if HEBI.fullmatch(title) or HEBI_CANDIDATE.fullmatch(title):
            collection = re.search(r'(前集|后集|後集|续集|續集|别集|別集|外集)', title)
            number = re.search(rf'[卷巻]\s*({NUMBER})\s*$', title)
            if collection and number:
                title = collection.group().translate(str.maketrans('後續別', '后续别')) + '第' + number.group(1) + '卷'
        texts.append(_text(node['start'], boundaries[i+1]['start'] if i+1 < len(boundaries) else len(source), title, node['title'], 'volume' if node['label'] == '卷' else 'text'))
    return texts, warnings


def _text(start, end, title, source_title, kind):
    return {'id': f's-{start}', 'title': title, 'source_title': source_title, 'kind': kind, 'label': '卷首材料' if kind == 'frontmatter' else '卷文本' if kind == 'volume' else '拆分文本',
            'start': start, 'end': end, 'level': 1, 'parent': '', 'path': title}


def _edit(pos, original, replacement, kind, reason, status, source):
    return {'id': f'e-{pos}', 'start': pos, 'end': pos+len(original), 'original': original, 'replacement': replacement,
            'kind': kind, 'reason': reason, 'status': status, 'context': source[max(0, pos-25):pos+len(original)+25]}


def prepared(source: str, edits: list, start=0, end=None) -> str:
    end = len(source) if end is None else end
    output, cursor = [], start
    for edit in edits:
        if edit['status'] != 'applied' or not (start <= edit['start'] < end):
            continue
        if edit['start'] < cursor or edit['end'] > end or source[edit['start']:edit['end']] != edit['original']:
            raise ValueError('修改位置重叠或与原文不一致，无法生成文本')
        output.extend((source[cursor:edit['start']], edit['replacement']))
        cursor = edit['end']
    output.append(source[cursor:end])
    return ''.join(output)


def paragraph_lines(text: str, labels: list[str]) -> str:
    if not labels:
        return text
    # Match only line starts or the boundary after a closed quotation. Longest
    # labels first avoids splitting e.g. 事理发挥 into 事理 + 发挥.
    values = sorted({value.strip() for value in labels}, key=lambda value: (-len(value), value))
    pattern = re.compile(r'(?m)(?:^[^\S\r\n]*|(?<=】)[^\S\r\n]*)(?P<label>' + '|'.join(re.escape(v) for v in values) + ')')
    horizontal = re.compile(r'[^\S\r\n]*')
    depth, cursor, insertions = 0, 0, set()
    for match in pattern.finditer(text):
        start, end = match.span('label')
        for bracket in re.finditer('[【】]', text[cursor:start]):
            depth = depth+1 if bracket.group() == '【' else max(0, depth-1)
        cursor = end
        if depth:
            continue  # A quoted line beginning with a label is still quotation.
        if match.start() and text[match.start()-1] == '】':
            insertions.add(match.start())
        next_char = horizontal.match(text, end).end()
        if next_char < len(text) and text[next_char] not in '\r\n':
            insertions.add(end)
    parts, cursor = [], 0
    for pos in sorted(insertions):
        parts.extend((text[cursor:pos], '\n'))
        cursor = pos
    parts.append(text[cursor:])
    return ''.join(parts)


def formatted(text: str, recipe: Recipe) -> str:
    # Interpret annotation boundaries before removing them. Raw source offsets
    # and reviewed character edits remain tied to the immutable source snapshot.
    text = paragraph_lines(text, recipe.paragraph_labels)
    if recipe.split_notes:
        text = re.sub(r'】(?=[^【】\r\n]+【)', '】\n', text)
    if recipe.remove_note_brackets:
        if recipe.quote_spacing:
            def separator(match):
                before, after = match.start()-1, match.end()
                return ' ' if before >= 0 and after < len(text) and not text[before].isspace() and not text[after].isspace() else ''
            text = re.sub('[【】]+', separator, text)
        else:
            text = text.translate(str.maketrans('', '', '【】'))
    return text


def punctuation_error(source: str, result: str) -> str:
    allowed = set('。，、；：？！“”‘’《》〈〉·—…,.!?;:\"\'（）()')
    i = 0
    for j, char in enumerate(result):
        if i < len(source) and char == source[i]:
            i += 1
        elif char not in allowed:
            expected = repr(source[i]) if i < len(source) else '正文结束'
            before = source[max(0, i-12):min(len(source), i+13)]
            after = result[max(0, j-12):min(len(result), j+13)]
            return (f'标点结果未通过原字校验：原文字符位置 {i}（本片段从 0 计数）出现改字、增删或重排。'
                    f'原文应为 {expected}，模型返回 {char!r}。原文附近：{before!r}；模型附近：{after!r}。')
    if i == len(source):
        return ''
    return (f'标点结果未通过原字校验：原文字符位置 {i}（本片段从 0 计数）之后有文字遗漏。'
            f'原文应为 {source[i]!r}，模型已结束。遗漏起始：{source[i:i+25]!r}。')
