from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.modules.statistics import get_statistics_overview

router = APIRouter(prefix="/api/statistics", tags=["statistics"])


@router.get("/overview")
async def get_statistics_overview_route(
    timeline: Literal["1h", "1d", "7d", "1m", "total"] = Query(default="1d"),
    currency: str = Query(default="USD", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"),
):
    return await get_statistics_overview(timeline=timeline, currency=currency)
