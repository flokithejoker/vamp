from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Literal

from app.config.customer_agent import CUSTOMER_AGENT_CONFIG
from app.providers.elevenlabs import list_agent_conversations

TimelineKey = Literal["1h", "1d", "7d", "1m", "total"]

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 200


@dataclass(frozen=True)
class WindowConfig:
    start_time_unix: int | None
    end_time_unix: int
    bucket_seconds: int
    label_mode: Literal["time", "date"]


def _read_path(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _pick_string(root: dict[str, Any], paths: list[str]) -> str | None:
    for path in paths:
        value = _read_path(root, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pick_number(root: dict[str, Any], paths: list[str]) -> float | None:
    for path in paths:
        value = _read_path(root, path)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
            except ValueError:
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _pick_bool(root: dict[str, Any], paths: list[str]) -> bool | None:
    for path in paths:
        value = _read_path(root, path)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1", "success", "successful", "succeeded"}:
                return True
            if normalized in {"false", "no", "0", "failure", "failed", "unsuccessful", "error"}:
                return False
    return None


def _pick_raw_value(root: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _read_path(root, path)
        if value is None:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
            continue
        return value
    return None


def _coerce_cost_value(value: Any) -> int | float | str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _coerce_float(value: int | float | str | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _pick_cost_fields(root: dict[str, Any]) -> tuple[int | float | str | None, str | None]:
    amount_from_charging = _coerce_cost_value(
        _pick_raw_value(
            root,
            [
                "metadata.charging.total_cost.amount",
                "metadata.charging.provider_cost.amount",
                "metadata.total_cost.amount",
                "total_cost.amount",
            ],
        )
    )
    currency_from_charging = _pick_string(
        root,
        [
            "metadata.charging.total_cost.currency",
            "metadata.charging.provider_cost.currency",
            "metadata.charging.currency",
            "metadata.total_cost.currency",
            "total_cost.currency",
        ],
    )
    if amount_from_charging is not None:
        return amount_from_charging, currency_from_charging

    amount_usd = _coerce_cost_value(
        _pick_raw_value(
            root,
            [
                "metadata.charging.total_cost_usd",
                "metadata.charging.provider_cost_usd",
                "metadata.usage.total_cost_usd",
                "metadata.total_cost_usd",
                "total_cost_usd",
            ],
        )
    )
    if amount_usd is not None:
        return amount_usd, "USD"

    amount_fallback = _coerce_cost_value(
        _pick_raw_value(
            root,
            [
                "metadata.total_cost",
                "metadata.call_cost",
                "metadata.usage.total_cost",
                "metadata.usage.cost",
                "total_cost",
                "call_cost",
            ],
        )
    )
    currency_fallback = _pick_string(root, ["metadata.currency", "currency"])
    return amount_fallback, currency_fallback


def _extract_start_time_unix(conversation: dict[str, Any]) -> int | None:
    value = _pick_number(
        conversation,
        [
            "metadata.start_time_unix_secs",
            "metadata.startTimeUnixSecs",
            "start_time_unix_secs",
            "startTimeUnixSecs",
            "call_start_unix_secs",
        ],
    )
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _extract_duration_seconds(conversation: dict[str, Any]) -> int | None:
    value = _pick_number(
        conversation,
        [
            "metadata.call_duration_secs",
            "metadata.callDurationSecs",
            "call_duration_secs",
            "callDurationSecs",
            "duration_secs",
            "durationSeconds",
        ],
    )
    if value is None:
        return None
    parsed = int(round(value))
    if parsed < 0:
        return None
    return parsed


def _extract_rating(conversation: dict[str, Any]) -> float | None:
    value = _pick_number(
        conversation,
        [
            "metadata.feedback.rating",
            "metadata.feedback.score",
            "feedback.rating",
            "feedback.score",
            "metadata.rating",
            "rating",
            "call_rating",
            "analysis.feedback.rating",
        ],
    )
    if value is None:
        return None
    if value < 0:
        return None
    return value


def _extract_success(conversation: dict[str, Any]) -> bool | None:
    explicit_success = _pick_bool(
        conversation,
        [
            "analysis.call_successful",
            "analysis.callSuccessful",
            "metadata.call_successful",
            "metadata.callSuccessful",
            "call_successful",
            "callSuccessful",
        ],
    )
    if explicit_success is not None:
        return explicit_success

    status = _normalize_status(
        _pick_string(
            conversation,
            [
                "status",
                "call_status",
                "callStatus",
                "metadata.status",
                "metadata.call_status",
                "metadata.callStatus",
            ],
        )
    )
    if status == "failed":
        return False
    if status == "done":
        return True
    return None


def _normalize_status(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {
        "done",
        "completed",
        "succeeded",
        "success",
        "successful",
        "finished",
        "ended",
    }:
        return "done"
    if normalized in {"failed", "error", "failure", "unsuccessful", "aborted", "cancelled", "canceled"}:
        return "failed"
    return "processing"


def _resolve_window(timeline: TimelineKey, now_unix: int) -> WindowConfig:
    if timeline == "1h":
        return WindowConfig(
            start_time_unix=now_unix - 3600,
            end_time_unix=now_unix,
            bucket_seconds=300,
            label_mode="time",
        )
    if timeline == "1d":
        return WindowConfig(
            start_time_unix=now_unix - 86400,
            end_time_unix=now_unix,
            bucket_seconds=3600,
            label_mode="time",
        )
    if timeline == "7d":
        return WindowConfig(
            start_time_unix=now_unix - 7 * 86400,
            end_time_unix=now_unix,
            bucket_seconds=86400,
            label_mode="date",
        )
    if timeline == "1m":
        return WindowConfig(
            start_time_unix=now_unix - 30 * 86400,
            end_time_unix=now_unix,
            bucket_seconds=86400,
            label_mode="date",
        )

    # total
    return WindowConfig(
        start_time_unix=None,
        end_time_unix=now_unix,
        bucket_seconds=7 * 86400,
        label_mode="date",
    )


def _bucket_label(bucket_start_unix: int, label_mode: Literal["time", "date"]) -> str:
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(CUSTOMER_AGENT_CONFIG.timezone)
        dt = datetime.fromtimestamp(bucket_start_unix, tz=zone)
    except Exception:
        dt = datetime.fromtimestamp(bucket_start_unix, tz=timezone.utc)

    if label_mode == "time":
        return dt.strftime("%H:%M")
    return dt.strftime("%d.%m")


def _build_series(
    *,
    calls: list[dict[str, Any]],
    window: WindowConfig,
    timeline: TimelineKey,
) -> tuple[list[dict[str, Any]], int]:
    with_start_times = [call["startTimeUnix"] for call in calls if isinstance(call.get("startTimeUnix"), int)]

    if timeline == "total":
        if with_start_times:
            series_start = min(with_start_times)
        else:
            series_start = window.end_time_unix - window.bucket_seconds
    else:
        series_start = window.start_time_unix or (window.end_time_unix - window.bucket_seconds)

    if series_start > window.end_time_unix:
        series_start = window.end_time_unix - window.bucket_seconds

    first_bucket_start = series_start - (series_start % window.bucket_seconds)
    last_bucket_start = window.end_time_unix - (window.end_time_unix % window.bucket_seconds)

    bucket_starts: list[int] = []
    cursor = first_bucket_start
    while cursor <= last_bucket_start:
        bucket_starts.append(cursor)
        cursor += window.bucket_seconds

    counts = {bucket_start: 0 for bucket_start in bucket_starts}
    for call in calls:
        start_time = call.get("startTimeUnix")
        if not isinstance(start_time, int):
            continue
        if start_time < series_start or start_time > window.end_time_unix:
            continue
        bucket_start = start_time - (start_time % window.bucket_seconds)
        if bucket_start in counts:
            counts[bucket_start] += 1

    series = [
        {
            "bucketStartUnix": bucket_start,
            "bucketLabel": _bucket_label(bucket_start, window.label_mode),
            "callCount": counts[bucket_start],
        }
        for bucket_start in bucket_starts
    ]

    return series, series_start


async def get_statistics_overview(*, timeline: TimelineKey, currency: str) -> dict[str, Any]:
    normalized_currency = currency.upper()
    now_unix = int(datetime.now(tz=timezone.utc).timestamp())
    window = _resolve_window(timeline, now_unix)

    records: list[dict[str, Any]] = []
    cursor: str | None = None
    has_more = True
    pages = 0
    truncated = False

    while has_more and pages < MAX_PAGES:
        payload = await list_agent_conversations(
            agent_id=CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id,
            page_size=DEFAULT_PAGE_SIZE,
            cursor=cursor,
            search=None,
            start_time_unix=window.start_time_unix,
            end_time_unix=window.end_time_unix,
        )
        pages += 1

        for conversation in payload.conversations:
            start_time_unix = _extract_start_time_unix(conversation)

            if window.start_time_unix is not None:
                if start_time_unix is None:
                    continue
                if start_time_unix < window.start_time_unix or start_time_unix > window.end_time_unix:
                    continue

            cost_amount, cost_currency = _pick_cost_fields(conversation)
            records.append(
                {
                    "startTimeUnix": start_time_unix,
                    "durationSeconds": _extract_duration_seconds(conversation),
                    "rating": _extract_rating(conversation),
                    "success": _extract_success(conversation),
                    "costAmount": cost_amount,
                    "costCurrency": cost_currency.upper() if isinstance(cost_currency, str) else None,
                }
            )

        has_more = payload.has_more
        cursor = payload.next_cursor

    if has_more:
        truncated = True

    calls_series, computed_start_time_unix = _build_series(calls=records, window=window, timeline=timeline)

    total_calls = len(records)

    total_cost_amount = 0.0
    included_cost_calls = 0
    excluded_no_currency = 0
    excluded_other_currency = 0

    for record in records:
        amount = _coerce_float(record.get("costAmount"))
        cost_currency = record.get("costCurrency")

        if amount is None:
            continue
        if not isinstance(cost_currency, str) or not cost_currency:
            excluded_no_currency += 1
            continue
        if cost_currency != normalized_currency:
            excluded_other_currency += 1
            continue

        total_cost_amount += amount
        included_cost_calls += 1

    average_cost_per_call = (
        (total_cost_amount / included_cost_calls) if included_cost_calls > 0 else None
    )

    duration_values = [
        duration
        for duration in (record.get("durationSeconds") for record in records)
        if isinstance(duration, int) and duration >= 0
    ]
    duration_included_calls = len(duration_values)
    average_duration_seconds = (
        int(round(sum(duration_values) / duration_included_calls))
        if duration_included_calls > 0
        else None
    )

    rating_values = [
        rating
        for rating in (record.get("rating") for record in records)
        if isinstance(rating, (int, float)) and math.isfinite(float(rating))
    ]
    rated_calls = len(rating_values)
    average_rating = (sum(rating_values) / rated_calls) if rated_calls > 0 else None

    success_count = 0
    failure_count = 0
    unknown_count = 0
    for record in records:
        success = record.get("success")
        if success is True:
            success_count += 1
        elif success is False:
            failure_count += 1
        else:
            unknown_count += 1

    success_known_calls = success_count + failure_count
    success_rate_percent = (
        int(round((success_count / success_known_calls) * 100.0))
        if success_known_calls > 0
        else None
    )

    return {
        "timeline": timeline,
        "currency": normalized_currency,
        "window": {
            "startTimeUnix": computed_start_time_unix if timeline == "total" else window.start_time_unix,
            "endTimeUnix": window.end_time_unix,
            "timezone": CUSTOMER_AGENT_CONFIG.timezone,
        },
        "callsSeries": calls_series,
        "metrics": {
            "totalCalls": total_calls,
            "totalCost": {
                "amount": round(total_cost_amount, 2),
                "currency": normalized_currency,
                "includedCalls": included_cost_calls,
                "excludedNoCurrency": excluded_no_currency,
                "excludedOtherCurrency": excluded_other_currency,
            },
            "averageCostPerCall": {
                "amount": round(average_cost_per_call, 4) if average_cost_per_call is not None else None,
                "currency": normalized_currency,
                "includedCalls": included_cost_calls,
            },
            "averageDurationSeconds": average_duration_seconds,
            "durationIncludedCalls": duration_included_calls,
            "averageRating": round(average_rating, 1) if average_rating is not None else None,
            "ratedCalls": rated_calls,
            "successRatePercent": success_rate_percent,
            "successKnownCalls": success_known_calls,
            "successUnknownCalls": unknown_count,
        },
        "diagnostics": {
            "truncated": truncated,
            "fetchedCalls": total_calls,
        },
    }
