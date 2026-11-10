from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

try:
    from backend.research import UnitSave
    from backend.services import exports
    from backend.knowledge import repository as r
except ModuleNotFoundError:
    from research import UnitSave
    from services import exports
    from knowledge import repository as r


class QuotesExport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    anchor_ids: list[UUID] = Field(min_length=1, max_length=1000)


class UnitBatchExport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # 一次导出整书全部卷（例如《合璧事類備要》367 卷）是常规操作：
    # 逐卷生成 docx 并打包的时间约 30 秒，远低于 240 秒的请求超时。
    unit_ids: list[str] = Field(min_length=1, max_length=5000)


def register(app, state, connection):
    @app.post('/api/exports/units/docx')
    def export_units_docx(payload: UnitBatchExport):
        try:
            with connection() as db:
                value = exports.manuscripts_docx(db, payload.unit_ids)
        except LookupError:
            raise HTTPException(404, '找不到工作稿') from None
        warnings = value.get('warnings') or {}
        pua = int(warnings.get('pua') or 0)
        replacement = int(warnings.get('replacement') or 0)
        headers = {
            'Content-Disposition': "attachment; filename=\"manuscripts.zip\"; filename*=UTF-8''" + quote(value['filename']),
            'X-Wenjin-Export-Pua': str(pua),
            'X-Wenjin-Export-Replacement': str(replacement),
            'X-Wenjin-Export-Warnings': str(pua + replacement),
            'Cache-Control': 'no-store',
        }
        return Response(content=value['data'], media_type='application/zip', headers=headers)

    @app.get('/api/artifacts/{identifier}/export')
    def export_artifact(identifier: str):
        try:
            with connection() as db:
                return exports.artifact(db, identifier)
        except LookupError:
            raise HTTPException(404, '找不到研究成果') from None

    @app.post('/api/units/{identifier}/export')
    def export_manuscript(identifier: str, payload: UnitSave):
        try:
            with connection() as db:
                return exports.manuscript(db, identifier, payload)
        except exports.ExportConflict as error:
            raise HTTPException(409, str(error)) from None
        except LookupError:
            raise HTTPException(404, '找不到工作稿') from None

    @app.post('/api/units/{identifier}/export/docx')
    def export_manuscript_docx(identifier: str, payload: UnitSave):
        try:
            with connection() as db:
                value = exports.manuscript_docx(db, identifier, payload)
        except exports.ExportConflict as error:
            raise HTTPException(409, str(error)) from None
        except LookupError:
            raise HTTPException(404, '找不到工作稿') from None
        warnings = value.get('warnings') or {}
        pua = int(warnings.get('pua') or 0)
        replacement = int(warnings.get('replacement') or 0)
        headers = {
            'Content-Disposition': "attachment; filename=\"manuscript.docx\"; filename*=UTF-8''" + quote(value['filename']),
            'X-Wenjin-Export-Pua': str(pua),
            'X-Wenjin-Export-Replacement': str(replacement),
            'X-Wenjin-Export-Warnings': str(pua + replacement),
            'Cache-Control': 'no-store',
        }
        return Response(content=value['data'],
                        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers=headers)


    @app.post('/api/knowledge/quotes/export')
    def export_quotes(payload: QuotesExport):
        with r.database.transaction(state.root) as db:
            anchors = [r.one(db, 'SELECT quote,locator FROM kb_anchors WHERE id=:id', id=identifier) for identifier in payload.anchor_ids]
        return exports.quotes(anchors)
