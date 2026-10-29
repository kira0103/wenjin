from uuid import UUID

from fastapi import HTTPException
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


def register(app, state, connection):
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

    @app.post('/api/knowledge/quotes/export')
    def export_quotes(payload: QuotesExport):
        with r.database.transaction(state.root) as db:
            anchors = [r.one(db, 'SELECT quote,locator FROM kb_anchors WHERE id=:id', id=identifier) for identifier in payload.anchor_ids]
        return exports.quotes(anchors)
