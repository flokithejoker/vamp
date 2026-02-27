from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
ELEVENLABS_API_KEY_ENV = "ELEVENLABS_API_KEY"
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
RETRY_BACKOFF_SECONDS = (0.2, 0.5)
DETAIL_404_RETRY_DELAY_SECONDS = 0.35


class BackendConfigurationError(RuntimeError):
    """Raised when required backend configuration is missing."""


class ElevenLabsApiError(RuntimeError):
    """Raised when ElevenLabs request fails."""

    def __init__(self, status_code: int, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


@dataclass(frozen=True)
class ElevenLabsConversationListPayload:
    conversations: list[dict[str, Any]]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True)
class ElevenLabsConversationDetailPayload:
    conversation: dict[str, Any]


@dataclass(frozen=True)
class ElevenLabsConversationAudioPayload:
    content: bytes
    content_type: str
    content_disposition: str | None


def _api_key() -> str:
    api_key = os.getenv(ELEVENLABS_API_KEY_ENV, "").strip()
    if not api_key:
        raise BackendConfigurationError(
            f"Missing {ELEVENLABS_API_KEY_ENV}. Add it to your environment or backend/.env."
        )
    return api_key


def _extract_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


async def _perform_get(
    *,
    path: str,
    params: dict[str, str | int] | None = None,
    accept: str = "application/json",
    retry_not_found_once: bool = False,
) -> httpx.Response:
    headers = {
        "Accept": accept,
        "xi-api-key": _api_key(),
    }

    last_error: ElevenLabsApiError | None = None

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                response = await client.get(f"{ELEVENLABS_BASE_URL}{path}", params=params, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = ElevenLabsApiError(504, "ElevenLabs request timed out.")
                if attempt < MAX_REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = ElevenLabsApiError(502, "Failed to contact ElevenLabs.")
                if attempt < MAX_REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_error from exc

            if response.status_code < 400:
                return response

            payload: Any
            try:
                payload = response.json()
            except ValueError:
                payload = response.text

            if retry_not_found_once and response.status_code == 404 and attempt == 1:
                await asyncio.sleep(DETAIL_404_RETRY_DELAY_SECONDS)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_REQUEST_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue

            raise ElevenLabsApiError(
                response.status_code,
                _extract_message(payload, f"ElevenLabs request failed with status {response.status_code}."),
                payload,
            )

    if last_error is not None:
        raise last_error
    raise ElevenLabsApiError(502, "Failed to contact ElevenLabs.")


async def list_agent_conversations(
    *,
    agent_id: str,
    page_size: int,
    cursor: str | None,
    search: str | None,
    start_time_unix: int | None = None,
    end_time_unix: int | None = None,
    call_successful: bool | None = None,
) -> ElevenLabsConversationListPayload:
    params: dict[str, str | int] = {
        "agent_id": agent_id,
        "page_size": page_size,
    }
    if cursor:
        params["cursor"] = cursor
    if search:
        params["search"] = search
    if start_time_unix is not None:
        params["start_time_unix"] = start_time_unix
    if end_time_unix is not None:
        params["end_time_unix"] = end_time_unix
    if call_successful is not None:
        params["call_successful"] = "true" if call_successful else "false"

    response = await _perform_get(path="/v1/convai/conversations", params=params)

    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = None

    record = payload if isinstance(payload, dict) else {}

    raw_conversations = record.get("conversations")
    if not isinstance(raw_conversations, list):
        raw_conversations = record.get("items")
    if not isinstance(raw_conversations, list):
        raw_conversations = []

    conversations = [item for item in raw_conversations if isinstance(item, dict)]

    has_more_raw = record.get("has_more")
    if not isinstance(has_more_raw, bool):
        has_more_raw = record.get("hasMore")
    has_more = bool(has_more_raw) if isinstance(has_more_raw, bool) else False

    next_cursor_raw = record.get("next_cursor")
    if not isinstance(next_cursor_raw, str):
        next_cursor_raw = record.get("nextCursor")
    next_cursor = next_cursor_raw if isinstance(next_cursor_raw, str) and next_cursor_raw.strip() else None

    return ElevenLabsConversationListPayload(
        conversations=conversations,
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def get_conversation_details(*, conversation_id: str) -> ElevenLabsConversationDetailPayload:
    response = await _perform_get(
        path=f"/v1/convai/conversations/{conversation_id}",
        retry_not_found_once=True,
    )

    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    conversation = payload if isinstance(payload, dict) else {}

    return ElevenLabsConversationDetailPayload(conversation=conversation)


async def get_conversation_audio(*, conversation_id: str) -> ElevenLabsConversationAudioPayload:
    response = await _perform_get(
        path=f"/v1/convai/conversations/{conversation_id}/audio",
        accept="*/*",
    )

    content_type = response.headers.get("content-type", "audio/mpeg")
    content_disposition = response.headers.get("content-disposition")

    return ElevenLabsConversationAudioPayload(
        content=response.content,
        content_type=content_type,
        content_disposition=content_disposition,
    )
