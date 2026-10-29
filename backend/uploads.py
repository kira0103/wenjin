"""Resumable byte uploads; only server-owned paths enter document import services."""
import json
import os
import re
import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

try:
    from backend.knowledge.document_imports import DocumentImports, EXTENSIONS, MAX_FILE
    from backend.knowledge.document_library import relative_path
    from backend.knowledge.managed import DirectoryLease
except ModuleNotFoundError:
    from knowledge.document_imports import DocumentImports, EXTENSIONS, MAX_FILE
    from knowledge.document_library import relative_path
    from knowledge.managed import DirectoryLease

CHUNK_SIZE = 4 * 1024 * 1024
MAX_TOTAL = 20 * 1024**3


class UploadFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    relative_path: str = Field(min_length=1, max_length=2000)
    size: int = Field(gt=0, le=MAX_FILE)


class UploadBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['files', 'directory'] = 'files'
    label: str = Field(default='所选文件', max_length=200)
    files: list[UploadFile] = Field(min_length=1, max_length=50000)


def safe_name(value):
    name = relative_path(value)
    for part in name.split('/'):
        if part.endswith(('.', ' ')) or re.search(r'[<>"|?*\x00-\x1f]', part) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?', part, re.I):
            raise HTTPException(400, '文件名无法跨平台保存')
    return name


class UploadStore:
    def __init__(self, root):
        self.root = Path(root) / 'uploads'

    def folder(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise HTTPException(404, '找不到上传批次')
        folder = self.root / identifier
        if not (folder / 'manifest.json').is_file():
            raise HTTPException(404, '找不到上传批次')
        return folder

    def load(self, identifier):
        folder = self.folder(identifier)
        return folder, json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))

    def save(self, folder, manifest):
        temporary = folder / ('manifest-' + secrets.token_hex(8) + '.tmp')
        try:
            temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, folder / 'manifest.json')
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, payload):
        if payload.mode == 'files' and len(payload.files) > 5000:
            raise HTTPException(400, '每批最多选择 5000 个文件')
        if sum(item.size for item in payload.files) > MAX_TOTAL:
            raise HTTPException(400, '每批文件合计最多 20 GB')
        files = []
        names = set()
        for item in payload.files:
            name = safe_name(item.relative_path)
            if Path(name).suffix.lower() not in EXTENSIONS:
                raise HTTPException(400, '不支持的文献格式')
            if payload.mode == 'files' and '/' in name:
                raise HTTPException(400, '多文件导入不接受目录路径')
            key = name.casefold()
            if key in names:
                raise HTTPException(400, '文件名重复，请分批导入')
            names.add(key)
            files.append({'relative_path': name, 'size': item.size})
        if any('/'.join(name.split('/')[:i]) in names for name in names for i in range(1, len(name.split('/')))):
            raise HTTPException(400, '文件名与目录冲突')
        identifier = secrets.token_hex(16)
        folder = self.root / identifier
        (folder / 'files').mkdir(parents=True)
        manifest = {'id': identifier, 'mode': payload.mode, 'label': payload.label, 'files': files, 'preview': None}
        self.save(folder, manifest)
        return self.status(identifier)

    def status(self, identifier):
        folder, manifest = self.load(identifier)
        return {'id': identifier, 'chunk_size': CHUNK_SIZE, 'preview': manifest['preview'], 'files': [
            {**item, 'offset': (folder / 'files' / item['relative_path']).stat().st_size if (folder / 'files' / item['relative_path']).is_file() else 0}
            for item in manifest['files']]}

    def append(self, identifier, index, offset, data):
        folder, manifest = self.load(identifier)
        lease = DirectoryLease(folder / 'upload.lock')
        try:
            lease.acquire()
        except RuntimeError:
            raise HTTPException(409, '此上传批次正在写入，请稍后重试') from None
        try:
            _, manifest = self.load(identifier)
            if manifest['preview']:
                raise HTTPException(409, '上传已确认，无法继续修改')
            if index >= len(manifest['files']):
                raise HTTPException(404, '找不到上传文件')
            item = manifest['files'][index]
            target = folder / 'files' / item['relative_path']
            actual = target.stat().st_size if target.exists() else 0
            if actual != offset:
                raise HTTPException(409, '上传位置已变化，请读取最新上传进度')
            if not data or len(data) > CHUNK_SIZE or offset + len(data) > item['size']:
                raise HTTPException(400, '上传数据大小与清单不一致')
            target.parent.mkdir(parents=True, exist_ok=True)
            # A failed partial append is observable through status() and can be resumed.
            with target.open('ab') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return {'offset': offset + len(data)}
        finally:
            lease.release()

    def preview(self, identifier):
        folder, manifest = self.load(identifier)
        lease = DirectoryLease(folder / 'upload.lock')
        try:
            lease.acquire()
        except RuntimeError:
            raise HTTPException(409, '此上传批次正在处理，请稍后重试') from None
        try:
            _, manifest = self.load(identifier)
            if manifest['preview']:
                return manifest['preview']
            status = self.status(identifier)
            if any(item['offset'] != item['size'] for item in status['files']):
                raise HTTPException(409, '请先完成全部文件上传')
            paths = [str(folder / 'files')] if manifest['mode'] == 'directory' else [str(folder / 'files' / f['relative_path']) for f in manifest['files']]
            preview = DocumentImports(self.root.parent).preview(manifest['mode'], paths)
            preview['source_label'] = manifest['label'] or '所选文件'
            manifest['preview'] = preview
            self.save(folder, manifest)
            return preview
        finally:
            lease.release()


def register(app, state):
    router = APIRouter(prefix='/api/uploads', tags=['uploads'])

    def store():
        if state.root is None:
            raise HTTPException(503, '资料服务尚未初始化')
        return UploadStore(state.root)

    @router.post('')
    def create(payload: UploadBatch):
        return store().create(payload)

    @router.get('/{identifier}')
    def status(identifier: str):
        return store().status(identifier)

    @router.put('/{identifier}/files/{index}')
    async def upload(identifier: str, index: int, request: Request, offset: int = Query(ge=0)):
        if index < 0:
            raise HTTPException(404, '找不到上传文件')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > CHUNK_SIZE:
                raise HTTPException(413, '单次上传最多 4 MB')
        return await run_in_threadpool(store().append, identifier, index, offset, data)

    @router.post('/{identifier}/preview')
    def preview(identifier: str):
        return store().preview(identifier)

    app.include_router(router)
