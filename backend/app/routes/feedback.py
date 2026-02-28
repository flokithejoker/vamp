from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.storage.feedback_store import (
    get_call_feedback,
    list_call_feedback,
    submit_call_feedback,
    submit_call_rating,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class SubmitCallRatingRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=256)
    rating: int = Field(ge=1, le=5)


class SubmitCallFeedbackRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=256)
    comment: str = Field(min_length=1, max_length=2000)


def _normalize_call_id(raw_call_id: str) -> str:
    normalized_call_id = raw_call_id.strip()
    if normalized_call_id:
        return normalized_call_id
    raise HTTPException(status_code=422, detail="call_id must not be blank.")


@router.post("/submit_call_rating")
async def submit_call_rating_route(payload: SubmitCallRatingRequest) -> dict[str, object]:
    normalized_call_id = _normalize_call_id(payload.call_id)
    record = submit_call_rating(call_id=normalized_call_id, rating=payload.rating)

    return {
        "ok": True,
        "call_id": record["callId"],
        "rating": record["rating"],
        "updated_at": record["updatedAt"],
    }


@router.post("/submit_call_feedback")
async def submit_call_feedback_route(payload: SubmitCallFeedbackRequest) -> dict[str, object]:
    normalized_call_id = _normalize_call_id(payload.call_id)
    normalized_comment = payload.comment.strip()
    if not normalized_comment:
        raise HTTPException(status_code=422, detail="comment must not be blank.")

    record = submit_call_feedback(call_id=normalized_call_id, comment=normalized_comment)
    return {
        "ok": True,
        "call_id": record["callId"],
        "comment": record["comment"],
        "updated_at": record["updatedAt"],
    }


@router.get("/calls")
async def list_call_feedback_route(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    return {"items": list_call_feedback(limit=limit, offset=offset)}


@router.get("/calls/{callId}")
async def get_call_feedback_route(
    callId: str = Path(min_length=1, max_length=256),
) -> dict[str, object]:
    normalized_call_id = _normalize_call_id(callId)
    return {"item": get_call_feedback(call_id=normalized_call_id)}

