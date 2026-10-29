"""Research exports use authoritative metadata and explicit draft revisions."""
import json
import re

STATUSES = {'working': '整理中', 'review': '待复核', 'approved': '已审定'}
NOTE_KINDS = {'variant': '异文 / 校记', 'citation': '引文溯源', 'question': '疑难问题', 'ai': 'AI 建议'}


class ExportConflict(ValueError):
    pass


def manuscript(db, identifier, snapshot):
    unit = db.execute('SELECT * FROM research_units WHERE id=?', (identifier,)).fetchone()
    if not unit:
        raise LookupError('找不到工作稿')
    if unit['revision'] != snapshot.expected_revision:
        raise ExportConflict('工作稿已由其他客户端更新，请重新打开后导出')
    project = db.execute('SELECT name FROM projects WHERE id=?', (unit['project_id'],)).fetchone()
    notes = [note.model_dump() for note in snapshot.notes]
    dirty = snapshot.content != unit['content'] or notes != json.loads(unit['notes_json']) or snapshot.status != unit['status']
    status = 'review' if dirty and snapshot.status == 'approved' else snapshot.status
    preprocess = json.loads(unit['preprocess_json'])
    note_text = '\n'.join(f"- {NOTE_KINDS[n['kind']]} / {n['location']} / {n['status']}\n  底本：{n['base_reading']}\n  参校读法：{n['witness_reading']}\n  处理：{n['decision']}\n  依据：{n['evidence']}" for n in notes)
    location = unit['location_label'] or f"{unit['page_from']}–{unit['page_to']}"
    content = (f"# {unit['title']}\n\n项目：{project['name']}\n范围：{unit['volume']} / {unit['category']} / {location}\n"
               f"预处理来源：{preprocess.get('run_id') or '直接录文'} / {preprocess.get('recipe_name', '')} / 字符 {preprocess.get('source_start', '')}–{preprocess.get('source_end', '')}\n"
               f"工作稿版本：{unit['revision']}{'（含未保存修改）' if dirty else ''}\n状态：{STATUSES[status]}\n\n{snapshot.content}\n\n## 校记与复核记录\n\n{note_text}")
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', unit['title']) + '.md'
    return {'filename': filename, 'content': content}


def quotes(anchors):
    parts = []
    for anchor in anchors:
        locator = anchor['locator']
        quote = anchor['quote'].replace('\n', '\n> ')
        parts.append(f"> {quote}\n\n出处：{locator['title']} · {locator['edition_label']} · 第 {locator['page_index']} 页／段")
    return {'filename': '原文引文与出处.md', 'content': '\n\n---\n\n'.join(parts)}


def artifact(db, identifier):
    row = db.execute('SELECT title,body FROM artifacts WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise LookupError('找不到研究成果')
    try:
        parsed = json.loads(row['body'])
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        body = {}
    context = body.get('source_context')
    context = context if isinstance(context, dict) else {}
    parts = [f"# {row['title']}"]
    for key, label in [('material_title', '材料'), ('selection', '处理范围'), ('model', '模型')]:
        if context.get(key):
            parts.append(f'{label}：{context[key]}')
    if context.get('missing_pages'):
        parts.append('未提取到文字的页码：' + '、'.join(map(str, context['missing_pages'])))
    content = body.get('content')
    parts.extend(['请对照原文复核 AI 结果。', content if isinstance(content, str) else row['body']])
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', row['title']) or '研究成果'
    return {'filename': filename + '.md', 'content': '\n\n'.join(parts)}
