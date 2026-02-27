from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.modules.smart_insights import get_smart_insights_report

router = APIRouter(prefix="/api/smart-insights", tags=["smart_insights"])


@router.get("/report")
async def get_smart_insights_report_route(
    timeline: Literal["1d", "7d", "1m"] = Query(default="7d"),
):
    return await get_smart_insights_report(timeline=timeline)
