from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any

from app.config.customer_agent import CUSTOMER_AGENT_CONFIG
from app.providers.elevenlabs import (
    ElevenLabsApiError,
    ElevenLabsConversationAudioPayload,
    get_conversation_audio,
    get_conversation_details,
    list_agent_conversations,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


class MonitoringConversationNotFoundError(RuntimeError):
    """Raised when a requested conversation is not available for the configured agent."""


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


def _pick_string_list(root: dict[str, Any], paths: list[str]) -> list[str]:
    for path in paths:
        value = _read_path(root, path)
        if isinstance(value, list):
            strings = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            if strings:
                return strings
    return []


def _pick_raw_value(root: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _read_path(root, path)
        if value is None:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                continue
            return trimmed
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
    currency_fallback = _pick_string(
        root,
        [
            "metadata.currency",
            "currency",
        ],
    )
    return amount_fallback, currency_fallback


def _format_time_label(start_time_unix: int) -> str:
    if start_time_unix <= 0:
        return "-"

    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(CUSTOMER_AGENT_CONFIG.timezone)
        dt = datetime.fromtimestamp(start_time_unix, tz=zone)
    except Exception:
        dt = datetime.utcfromtimestamp(start_time_unix)

    return dt.strftime("%H:%M - %d.%m.%y")


def _format_duration(duration_seconds: int | None) -> str:
    if duration_seconds is None or duration_seconds < 0:
        return "-"

    hours = duration_seconds // 3600
    minutes = (duration_seconds % 3600) // 60
    seconds = duration_seconds % 60

    if hours > 0:
        return f"{hours}h{minutes}m{seconds}s"
    if minutes > 0:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def _format_call_offset(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _format_cost_value(cost_raw: int | float | str | None) -> str:
    if cost_raw is None:
        return "-"
    if isinstance(cost_raw, int):
        return str(cost_raw)
    if isinstance(cost_raw, float):
        if cost_raw.is_integer():
            return str(int(cost_raw))
        return f"{cost_raw:.8f}".rstrip("0").rstrip(".")
    return cost_raw.strip() or "-"


def _format_cost(cost_raw: int | float | str | None, currency: str | None) -> str:
    value = _format_cost_value(cost_raw)
    if value == "-":
        return value

    if not currency:
        return "-"

    normalized_currency = currency.strip()
    if not normalized_currency:
        return "-"

    currency_lower = normalized_currency.lower()
    if currency_lower == "usd":
        numeric = _pick_number({"value": cost_raw}, ["value"])
        if numeric is not None:
            return f"${numeric:.2f}"
        return f"{value} USD"
    if currency_lower == "eur":
        numeric = _pick_number({"value": cost_raw}, ["value"])
        if numeric is not None:
            return f"€{numeric:.2f}"
        return f"{value} EUR"
    if currency_lower == "gbp":
        numeric = _pick_number({"value": cost_raw}, ["value"])
        if numeric is not None:
            return f"£{numeric:.2f}"
        return f"{value} GBP"
    return f"{value} {normalized_currency}"


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


def _extract_status(conversation: dict[str, Any]) -> str:
    return _normalize_status(
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


def _extract_success(conversation: dict[str, Any], normalized_status: str | None = None) -> bool | None:
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

    status = normalized_status or _extract_status(conversation)
    if status == "failed":
        return False
    if status == "done":
        return True
    return None


def _normalize_role(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"user", "caller", "human", "customer"}:
        return "user"
    if normalized in {"assistant", "agent", "ai", "bot"}:
        return "agent"
    if normalized in {"tool", "function"}:
        return "tool"
    return "system"


def _safe_tool_name(value: str | None) -> str:
    if value and value.strip():
        return value.strip()
    return "Tool"


def _truncate_text(text: str, max_length: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def _extract_raw_transcript(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    raw_transcript = _read_path(conversation, "transcript")
    if not isinstance(raw_transcript, list):
        raw_transcript = _read_path(conversation, "conversation_transcript")
    if not isinstance(raw_transcript, list):
        raw_transcript = _read_path(conversation, "messages")
    if not isinstance(raw_transcript, list):
        raw_transcript = []

    return [turn for turn in raw_transcript if isinstance(turn, dict)]


def _extract_first_message_excerpt(conversation: dict[str, Any]) -> str | None:
    transcript = _extract_raw_transcript(conversation)
    fallback_message: str | None = None

    for turn in transcript:
        message = _pick_string(turn, ["message", "text", "content", "transcript"])
        if not message:
            continue
        role = _normalize_role(_pick_string(turn, ["role", "speaker", "source"]))
        excerpt = _truncate_text(message, max_length=80)
        if role == "user":
            return excerpt
        if fallback_message is None:
            fallback_message = excerpt

    return fallback_message


def _derive_title(conversation: dict[str, Any], conversation_id: str) -> str:
    title_candidate = _pick_string(
        conversation,
        [
            "title",
            "call_summary_title",
            "callSummaryTitle",
            "metadata.call_summary_title",
        ],
    )
    if title_candidate:
        return _truncate_text(title_candidate, max_length=90)

    return f"Conversation {conversation_id[:8]}"


def _build_tool_event(
    *,
    raw: dict[str, Any],
    kind: str,
    fallback_id: str,
) -> dict[str, Any]:
    name = _safe_tool_name(
        _pick_string(
            raw,
            [
                "tool_name",
                "toolName",
                "name",
            ],
        )
    )
    event_id = (
        _pick_string(
            raw,
            [
                "tool_call_id",
                "toolCallId",
                "id",
            ],
        )
        or fallback_id
    )

    payload = _read_path(raw, "parameters")
    if payload is None:
        payload = _read_path(raw, "arguments")
    if payload is None:
        payload = _read_path(raw, "result")
    if payload is None:
        payload = _read_path(raw, "output")
    if payload is None:
        payload = raw

    return {
        "id": event_id,
        "kind": kind,
        "name": name,
        "payload": payload,
    }


def _extract_tool_events(turn: dict[str, Any], index: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    tool_calls = _read_path(turn, "tool_calls")
    if isinstance(tool_calls, list):
        for event_index, raw_call in enumerate(tool_calls):
            if not isinstance(raw_call, dict):
                continue
            events.append(
                _build_tool_event(
                    raw=raw_call,
                    kind="call",
                    fallback_id=f"call-{index}-{event_index}",
                )
            )

    tool_results = _read_path(turn, "tool_results")
    if isinstance(tool_results, list):
        for event_index, raw_result in enumerate(tool_results):
            if not isinstance(raw_result, dict):
                continue
            events.append(
                _build_tool_event(
                    raw=raw_result,
                    kind="result",
                    fallback_id=f"result-{index}-{event_index}",
                )
            )

    return events


def _map_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    conversation_id = _pick_string(conversation, ["conversation_id", "conversationId", "id"]) or "unknown"
    title = _derive_title(conversation, conversation_id)
    status = _extract_status(conversation)

    start_time_value = _pick_number(
        conversation,
        [
            "metadata.start_time_unix_secs",
            "metadata.startTimeUnixSecs",
            "start_time_unix_secs",
            "startTimeUnixSecs",
            "call_start_unix_secs",
        ],
    )
    start_time_unix = int(start_time_value) if start_time_value is not None else 0

    duration_value = _pick_number(
        conversation,
        [
            "metadata.call_duration_secs",
            "metadata.callDurationSecs",
            "call_duration_secs",
            "duration_secs",
            "duration_seconds",
        ],
    )
    duration_seconds = int(round(duration_value)) if duration_value is not None else None

    cost_raw, cost_currency = _pick_cost_fields(conversation)

    return {
        "conversationId": conversation_id,
        "title": title,
        "status": status,
        "callSuccessful": _extract_success(conversation, status),
        "startTimeUnix": start_time_unix,
        "startTimeLabel": _format_time_label(start_time_unix),
        "durationSeconds": duration_seconds,
        "durationLabel": _format_duration(duration_seconds),
        "costRaw": cost_raw,
        "costCurrency": cost_currency,
        "costLabel": _format_cost(cost_raw, cost_currency),
        "toolNames": _pick_string_list(
            conversation,
            ["tool_names", "toolNames", "metadata.tool_names", "metadata.toolNames"],
        ),
    }


def _normalize_page_size(raw_page_size: int | None) -> int:
    if raw_page_size is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(raw_page_size, MAX_PAGE_SIZE))


def _normalize_search(raw_search: str | None) -> str | None:
    if raw_search is None:
        return None
    search = raw_search.strip()
    return search or None


def _conversation_matches_search(conversation: dict[str, Any], search: str) -> bool:
    needle = search.casefold()

    candidates: list[str] = []
    for path in [
        "conversation_id",
        "conversationId",
        "id",
        "call_summary_title",
        "callSummaryTitle",
        "metadata.call_summary_title",
        "analysis.transcript_summary",
        "call_summary",
        "metadata.phone_number",
        "metadata.phoneNumber",
    ]:
        value = _read_path(conversation, path)
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    if any(needle in candidate.casefold() for candidate in candidates):
        return True

    try:
        serialized = json.dumps(conversation, ensure_ascii=False)
    except (TypeError, ValueError):
        return False

    return needle in serialized.casefold()


def _extract_transcript(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    raw_transcript = _extract_raw_transcript(conversation)

    transcript: list[dict[str, Any]] = []
    for index, raw_turn in enumerate(raw_transcript):
        turn_id = _pick_string(raw_turn, ["message_id", "messageId", "id"]) or f"turn-{index}"
        role = _normalize_role(_pick_string(raw_turn, ["role", "speaker", "source"]))
        message = _pick_string(raw_turn, ["message", "text", "content", "transcript"]) or "-"

        time_value = _pick_number(
            raw_turn,
            [
                "time_in_call_secs",
                "timeInCallSecs",
                "offset_secs",
                "offsetSecs",
                "start_offset_secs",
                "timestamp_secs",
            ],
        )
        time_in_call_seconds = int(round(time_value)) if time_value is not None and time_value >= 0 else None
        tool_events = _extract_tool_events(raw_turn, index)

        transcript.append(
            {
                "id": turn_id,
                "role": role,
                "message": message,
                "timeInCallSeconds": time_in_call_seconds,
                "timeLabel": _format_call_offset(time_in_call_seconds),
                "toolEvents": tool_events,
            }
        )

    return transcript


def _aggregate_tools_used(conversation: dict[str, Any], transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}

    for name in _pick_string_list(conversation, ["tool_names", "toolNames", "metadata.tool_names", "metadata.toolNames"]):
        clean_name = _safe_tool_name(name)
        counts[clean_name] = counts.get(clean_name, 0) + 1

    for turn in transcript:
        tool_events = turn.get("toolEvents")
        if not isinstance(tool_events, list):
            continue
        for event in tool_events:
            if not isinstance(event, dict):
                continue
            clean_name = _safe_tool_name(_pick_string(event, ["name"]))
            counts[clean_name] = counts.get(clean_name, 0) + 1

    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]


async def _fetch_conversation_for_agent(conversation_id: str) -> dict[str, Any]:
    try:
        detail_payload = await get_conversation_details(conversation_id=conversation_id)
    except ElevenLabsApiError as exc:
        if exc.status_code == 404:
            raise MonitoringConversationNotFoundError("Conversation not found.") from exc
        raise

    conversation = detail_payload.conversation
    if not isinstance(conversation, dict) or not conversation:
        raise MonitoringConversationNotFoundError("Conversation not found.")

    conversation_agent_id = _pick_string(
        conversation,
        ["agent_id", "agentId", "metadata.agent_id", "metadata.agentId"],
    )
    if conversation_agent_id and conversation_agent_id != CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id:
        raise MonitoringConversationNotFoundError("Conversation not found for configured agent.")

    return conversation


async def list_monitoring_conversations(
    *,
    cursor: str | None,
    page_size: int | None,
    search: str | None,
) -> dict[str, Any]:
    normalized_page_size = _normalize_page_size(page_size)
    normalized_search = _normalize_search(search)
    payload = await list_agent_conversations(
        agent_id=CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id,
        cursor=cursor,
        page_size=normalized_page_size,
        search=normalized_search,
    )

    conversations = payload.conversations
    has_more = payload.has_more
    next_cursor = payload.next_cursor

    # ElevenLabs search can miss partial prefixes (e.g. "dorme" vs "dormero").
    # Fallback to local contains-matching on the current page when zero results are returned.
    if normalized_search and not conversations:
        fallback_payload = await list_agent_conversations(
            agent_id=CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id,
            cursor=cursor,
            page_size=normalized_page_size,
            search=None,
        )
        conversations = [
            conversation
            for conversation in fallback_payload.conversations
            if _conversation_matches_search(conversation, normalized_search)
        ]
        has_more = fallback_payload.has_more
        next_cursor = fallback_payload.next_cursor

    items = [_map_conversation(conversation) for conversation in conversations]
    items.sort(key=lambda item: item["startTimeUnix"], reverse=True)

    return {
        "items": items,
        "hasMore": has_more,
        "nextCursor": next_cursor,
    }


async def get_monitoring_conversation_detail(*, conversation_id: str) -> dict[str, Any]:
    conversation = await _fetch_conversation_for_agent(conversation_id)
    mapped = _map_conversation(conversation)
    transcript = _extract_transcript(conversation)

    summary = _pick_string(
        conversation,
        [
            "analysis.transcript_summary",
            "analysis.summary",
            "transcript_summary",
            "call_summary",
        ],
    )

    has_audio = _pick_bool(conversation, ["has_audio", "hasAudio", "metadata.has_audio", "metadata.hasAudio"])
    has_user_audio = _pick_bool(
        conversation,
        ["has_user_audio", "hasUserAudio", "metadata.has_user_audio", "metadata.hasUserAudio"],
    )
    has_response_audio = _pick_bool(
        conversation,
        ["has_response_audio", "hasResponseAudio", "metadata.has_response_audio", "metadata.hasResponseAudio"],
    )

    if has_audio is None:
        has_audio = bool(has_user_audio or has_response_audio)

    item = {
        **mapped,
        "summary": summary or "-",
        "callSuccessful": mapped.get("callSuccessful"),
        "hasAudio": bool(has_audio),
        "hasUserAudio": bool(has_user_audio),
        "hasResponseAudio": bool(has_response_audio),
        "transcript": transcript,
        "toolsUsed": _aggregate_tools_used(conversation, transcript),
    }

    return {
        "item": item,
        "raw": conversation,
    }


async def get_monitoring_conversation_audio(*, conversation_id: str) -> ElevenLabsConversationAudioPayload:
    await _fetch_conversation_for_agent(conversation_id)

    try:
        return await get_conversation_audio(conversation_id=conversation_id)
    except ElevenLabsApiError as exc:
        if exc.status_code == 404:
            raise MonitoringConversationNotFoundError("Audio not available for this conversation.") from exc
        raise
