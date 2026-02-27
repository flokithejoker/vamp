from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
REQUEST_TIMEOUT_SECONDS = 25.0
MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRY_BACKOFF_SECONDS = (0.3, 0.8)


class OpenAIConfigurationError(RuntimeError):
    """Raised when required OpenAI configuration is missing."""


class OpenAIApiError(RuntimeError):
    """Raised when OpenAI request fails."""

    def __init__(self, status_code: int, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


def _api_key() -> str:
    api_key = os.getenv(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        raise OpenAIConfigurationError(
            f"Missing {OPENAI_API_KEY_ENV}. Add it to your environment or backend/.env."
        )
    return api_key


def _model_name() -> str:
    model = os.getenv(OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL).strip()
    return model or DEFAULT_OPENAI_MODEL


def _extract_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return fallback


def _extract_content(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)

    return None


async def create_structured_chat_completion(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    json_schema: dict[str, Any],
    schema_name: str,
    temperature: float = 0.2,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    body = {
        "model": _model_name(),
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            },
        },
    }

    last_error: OpenAIApiError | None = None

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                response = await client.post(f"{OPENAI_BASE_URL}/chat/completions", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last_error = OpenAIApiError(504, "OpenAI request timed out.")
                if attempt < MAX_REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = OpenAIApiError(502, "Failed to contact OpenAI.")
                if attempt < MAX_REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_error from exc

            payload: Any
            try:
                payload = response.json()
            except ValueError:
                payload = response.text

            if response.status_code >= 400:
                message = _extract_message(payload, f"OpenAI request failed with status {response.status_code}.")
                last_error = OpenAIApiError(response.status_code, message, payload)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_error

            if not isinstance(payload, dict):
                raise OpenAIApiError(502, "OpenAI returned a non-JSON response.", payload)

            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise OpenAIApiError(502, "OpenAI response did not include choices.", payload)

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise OpenAIApiError(502, "OpenAI response choice is invalid.", payload)

            message = first_choice.get("message")
            if not isinstance(message, dict):
                raise OpenAIApiError(502, "OpenAI response message is missing.", payload)

            refusal = message.get("refusal")
            if isinstance(refusal, str) and refusal.strip():
                raise OpenAIApiError(422, "OpenAI refused to produce the report.", {"refusal": refusal})

            content = _extract_content(message)
            if not content:
                raise OpenAIApiError(502, "OpenAI response content was empty.", payload)

            try:
                parsed = json.loads(content)
            except ValueError as exc:
                raise OpenAIApiError(502, "OpenAI returned invalid JSON content.", {"content": content}) from exc

            if not isinstance(parsed, dict):
                raise OpenAIApiError(502, "OpenAI JSON content must be an object.", parsed)

            return parsed

    if last_error is not None:
        raise last_error
    raise OpenAIApiError(502, "Failed to contact OpenAI.")
