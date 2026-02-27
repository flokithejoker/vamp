from __future__ import annotations

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from app.modules.monitoring import (
    get_monitoring_conversation_audio,
    get_monitoring_conversation_detail,
    list_monitoring_conversations,
)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/conversations")
async def get_conversations(
    cursor: str | None = Query(default=None, max_length=1024),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=50),
    search: str | None = Query(default=None, max_length=256),
):
    return await list_monitoring_conversations(cursor=cursor, page_size=page_size, search=search)


@router.get("/conversations/{conversationId}")
async def get_conversation_detail(
    conversationId: str = Path(min_length=1, max_length=256),
):
    return await get_monitoring_conversation_detail(conversation_id=conversationId)


@router.get("/conversations/{conversationId}/audio")
async def get_conversation_audio_stream(
    conversationId: str = Path(min_length=1, max_length=256),
) -> Response:
    payload = await get_monitoring_conversation_audio(conversation_id=conversationId)

    headers: dict[str, str] = {}
    if payload.content_disposition:
        headers["Content-Disposition"] = payload.content_disposition

    return Response(content=payload.content, media_type=payload.content_type, headers=headers)
