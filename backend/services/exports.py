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


def manuscript_docx(db, identifier, snapshot):
    """Clean single-volume Word manuscript; pending notes are intentionally omitted."""
    unit = db.execute('SELECT * FROM research_units WHERE id=?', (identifier,)).fetchone()
    if not unit:
        raise LookupError('找不到工作稿')
    if unit['revision'] != snapshot.expected_revision:
        raise ExportConflict('工作稿已由其他客户端更新，请重新打开后导出')
    project = db.execute('SELECT name FROM projects WHERE id=?', (unit['project_id'],)).fetchone()
    from backend.services import export_docx
    data, warnings = export_docx.build(
        snapshot.content or '', unit['title'], project['name'] if project else '',
        status=snapshot.status, include_unresolved_list=True,
    )
    return {
        'filename': export_docx.filename_for(project['name'] if project else '', unit['title'], snapshot.content or ''),
        'data': data,
        'warnings': warnings,
    }


def manuscripts_docx(db, identifiers):
    """Batch export: one clean Word file per selected unit, packed as a zip."""
    import io
    import zipfile
    from backend.services import export_docx

    archive = io.BytesIO()
    warnings = {'pua': 0, 'replacement': 0, 'mapped': 0}
    unresolved_files = []
    used = set()
    project_name = ''
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for identifier in identifiers:
            unit = db.execute('SELECT * FROM research_units WHERE id=?', (identifier,)).fetchone()
            if not unit:
                raise LookupError('找不到工作稿')
            project = db.execute('SELECT name FROM projects WHERE id=?', (unit['project_id'],)).fetchone()
            name = project['name'] if project else ''
            project_name = project_name or name
            data, item_warnings = export_docx.build(unit['content'] or '', unit['title'], name)
            warnings['pua'] += item_warnings['pua']
            warnings['replacement'] += item_warnings['replacement']
            warnings['mapped'] += item_warnings.get('mapped', 0)
            if item_warnings.get('unresolved'):
                unresolved_files.append((unit['title'], item_warnings['unresolved']))
            filename = export_docx.filename_for(name, unit['title'], unit['content'] or '')
            stem, dot, suffix = filename.rpartition('.')
            candidate = filename
            counter = 2
            while candidate in used:
                candidate = f'{stem}_{counter}.{suffix}'
                counter += 1
            used.add(candidate)
            bundle.writestr(candidate, data)
        if unresolved_files:
            by_code = {}
            for _, items in unresolved_files:
                for item in items:
                    key = (item['codepoint'], item.get('sk'), item['description'])
                    by_code[key] = by_code.get(key, 0) + 1
            report = ['私用区字符处理清单（导出 Word 时自动生成）', '',
                      f'已自动还原为标准 Unicode：{warnings["mapped"]} 处',
                      f'待人工处理：{warnings["pua"] + warnings["replacement"]} 处，共 {len(by_code)} 种码位（Word 中以 □ 显示）', '']
            report.append('待人工处理码位汇总（按出现次数排序）：')
            for (code, sk, description), count in sorted(by_code.items(), key=lambda item: -item[1]):
                extra = f'（SK {sk}）' if sk else ''
                report.append(f'  {code}{extra} {description}：{count} 处')
            report.append('')
            report.append('逐卷明细：')
            for title, items in unresolved_files:
                report.append('===== ' + title + ' =====')
                for number, item in enumerate(items, 1):
                    extra = f'（SK {item["sk"]}）' if item.get('sk') else ''
                    report.append(f'{number}. {item["codepoint"]}{extra} {item["description"]}｜上下文：{item["context"]}')
                report.append('')
            bundle.writestr('_私用区待人工处理清单.txt', '\n'.join(report))
    if not project_name:
        project_name = '点校稿'
    book = export_docx._safe_name(export_docx._project_book_title(project_name), '点校稿')
    today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    archive_name = f'{book}_选中{len(identifiers)}卷_点校稿_{today}.zip'
    return {'filename': re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', archive_name), 'data': archive.getvalue(), 'warnings': warnings}




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
