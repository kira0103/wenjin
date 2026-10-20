"""Read original documents without pretending text segments are printed pages."""
from __future__ import annotations

import base64
import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf as fitz
from fastapi import HTTPException

WORD_NAMESPACE = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def segments(text: str, size: int = 6000) -> list[tuple[int, str]]:
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\x07', '\t')
    chunks, current = [], ''
    for line in text.splitlines(keepends=True):
        while line:
            room = size - len(current)
            current += line[:room]
            line = line[room:]
            if len(current) == size:
                chunks.append(current)
                current = ''
        if len(current) > size * .8:
            chunks.append(current)
            current = ''
    if current or not chunks:
        chunks.append(current)
    return [(i + 1, chunk) for i, chunk in enumerate(chunks)]


def read_txt(data: bytes) -> tuple[str, str]:
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        codecs = ['utf-16']
    elif data.startswith(b'\xef\xbb\xbf'):
        codecs = ['utf-8-sig']
    else:
        codecs = ['utf-8', 'gb18030']
    for encoding in codecs:
        try:
            text = data.decode(encoding)
            if '\x00' in text or sum(ord(c) < 32 and c not in '\r\n\t\f' for c in text) > 2:
                continue
            return text, encoding
        except UnicodeError:
            pass
    raise HTTPException(400, '无法识别 TXT 编码，请保存为 UTF-8、带 BOM 的 UTF-16 或 GB18030 后导入')


def read_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 250 * 1024 * 1024:
                raise HTTPException(400, 'Word 解压后超过 250 MB')
            files = ['word/document.xml'] + [n for n in ('word/footnotes.xml', 'word/endnotes.xml') if n in archive.namelist()]
            parts = []
            for filename in files:
                root = ET.fromstring(archive.read(filename))
                paragraphs = []
                for p in root.iter(WORD_NAMESPACE + 'p'):
                    text = ''.join(node.text or '' if node.tag == WORD_NAMESPACE + 't' else '\t' if node.tag == WORD_NAMESPACE + 'tab' else '\n' if node.tag in {WORD_NAMESPACE + 'br', WORD_NAMESPACE + 'cr'} else '' for node in p.iter())
                    if text:
                        paragraphs.append(text)
                if filename != 'word/document.xml':
                    paragraphs.insert(0, '\n【脚注】' if 'footnotes' in filename else '\n【尾注】')
                parts.append('\n'.join(paragraphs))
            return '\n\n'.join(parts)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError) as exc:
        raise HTTPException(400, '无法读取 Word 文件；请使用未加密的 .docx 文件') from exc


def read_legacy_doc(data: bytes) -> str:
    # Runs in its own process and creates its own Word COM application. Never
    # attaches to an active document; macros are disabled and the file is read-only.
    if os.name != 'nt':
        raise HTTPException(400, '.doc 需要 Windows 上安装 Microsoft Word；也可另存为 .docx 导入')
    script = r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$word=$null; $document=$null
$saveNone=0
try {
  $word=New-Object -ComObject Word.Application
  $word.Visible=$false; $word.DisplayAlerts=0; $word.AutomationSecurity=3
  $inputFile=$env:SHIXUE_DOC_INPUT; $confirm=$false; $readOnly=$true; $recent=$false
  $document=$word.Documents.Open([ref]$inputFile,[ref]$confirm,[ref]$readOnly,[ref]$recent)
  [IO.File]::WriteAllText($env:SHIXUE_DOC_OUTPUT,$document.Content.Text,[Text.UTF8Encoding]::new($false))
} finally {
  try { if($document){$document.Close([ref]$saveNone); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)} }
  finally { if($word){$word.Quit([ref]$saveNone); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)} }
}
'''
    with tempfile.TemporaryDirectory(prefix='shixue-word-') as folder:
        source, result = Path(folder) / 'source.doc', Path(folder) / 'text.txt'
        source.write_bytes(data)
        env = {**os.environ, 'SHIXUE_DOC_INPUT': str(source), 'SHIXUE_DOC_OUTPUT': str(result)}
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode('ascii')], env=env, capture_output=True, timeout=90, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return result.read_text(encoding='utf-8')
        except (subprocess.SubprocessError, OSError) as exc:
            raise HTTPException(400, '无法读取 .doc；请确认已安装 Microsoft Word，或另存为 .docx 后导入') from exc


def parse_document(filename: str, data: bytes) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.doc', '.txt'}:
        raise HTTPException(400, '支持 PDF、Word（.docx / .doc）和 TXT 文件')
    if not data or len(data) > 250 * 1024 * 1024:
        raise HTTPException(400, '文件为空或超过 250 MB')
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise HTTPException(400, 'PDF 文件无效')
        try:
            with fitz.open(stream=data, filetype='pdf') as pdf:
                if pdf.needs_pass:
                    raise HTTPException(400, '请先解除 PDF 密码保护后导入')
                if pdf.page_count > 5000:
                    raise HTTPException(400, '单份 PDF 最多处理 5000 页')
                pages = [(i + 1, pdf.load_page(i).get_text('text')) for i in range(pdf.page_count)]
            return {'pages': pages, 'format': 'pdf', 'position_kind': 'page', 'encoding': ''}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, '无法读取这份 PDF') from exc
    encoding = ''
    if suffix == '.txt':
        text, encoding = read_txt(data)
    elif suffix == '.docx':
        text = read_docx(data)
    else:
        text = read_legacy_doc(data)
    if not text.strip():
        raise HTTPException(400, '文件中没有可读取的正文；图片型 Word 请先提供录文')
    return {'pages': segments(text), 'format': suffix[1:], 'position_kind': 'section', 'encoding': encoding}
