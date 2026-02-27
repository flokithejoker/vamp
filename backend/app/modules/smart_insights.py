from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.customer_agent import CUSTOMER_AGENT_CONFIG
from app.providers.elevenlabs import get_conversation_details, list_agent_conversations
from app.providers.openai import OpenAIApiError, create_structured_chat_completion

TimelineKey = Literal["1d", "7d", "1m"]
CriterionKey = Literal["human_escalation", "intent_identification", "call_cancellation"]
CriterionState = Literal["pass", "fail", "unknown"]

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 200
MAX_CALLS = 500
SMART_INSIGHTS_DETAIL_CAP_ENV = "SMART_INSIGHTS_DETAIL_CAP"
SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY_ENV = "SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY"
SMART_INSIGHTS_DETAIL_CAP_DEFAULT = 200
SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY_DEFAULT = 8
REPORT_VERSION = 2

CRITERIA_KEYS: tuple[CriterionKey, ...] = (
    "human_escalation",
    "intent_identification",
    "call_cancellation",
)

CRITERION_LABELS: dict[CriterionKey, str] = {
    "human_escalation": "Human handoff needed",
    "intent_identification": "Intent misunderstood",
    "call_cancellation": "Call ended before completion",
}

DATA_FIELD_KEYS = (
    "hotel_location",
    "recommended_internal_action",
    "knowledge_gap_topic",
    "primary_friction_point",
    "user_intent",
    "resolution_status",
    "booking_stage",
    "topics",
)

SCALAR_FIELD_KEYS = (
    "hotel_location",
    "recommended_internal_action",
    "knowledge_gap_topic",
    "primary_friction_point",
    "user_intent",
    "resolution_status",
    "booking_stage",
)

DATA_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "hotel_location": ("hotel_location",),
    "recommended_internal_action": ("recommended_internal_action",),
    "knowledge_gap_topic": ("knowledge_gap_topic", "knowledge_gap"),
    "primary_friction_point": ("primary_friction_point", "friction_point"),
    "user_intent": ("user_intent",),
    "resolution_status": ("resolution_status",),
    "booking_stage": ("booking_stage",),
    "topics": ("topics",),
}

# Explicit taxonomy labels like none/other/no_action_needed are valid values and count as present.
MISSING_VALUE_SET = {"unknown", "not_applicable", "n_a", "na", "not_available", ""}
TOKEN_SANITIZER_PATTERN = re.compile(r"[^a-z0-9]+")


class SmartInsightsGenerationError(RuntimeError):
    """Raised when the Smart Insights report cannot be generated safely."""


@dataclass(frozen=True)
class WindowConfig:
    start_time_unix: int
    end_time_unix: int


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SmartInsightsMeta(StrictModel):
    reportVersion: Literal[2]
    timeline: TimelineKey
    generatedAtIso: str
    totalCalls: int = Field(ge=0)
    availableCalls: int = Field(ge=0)
    analyzedCalls: int = Field(ge=0)
    detailFetchCap: int = Field(ge=1)
    cappedByDetailCap: bool
    detailFetchFailures: int = Field(ge=0)
    dataCoveragePercent: float = Field(ge=0, le=100)


class SmartInsightsOverview(StrictModel):
    summary: str
    operationalStatus: Literal["stable", "watch", "at_risk"]
    topOpportunity: str


class SmartInsightsEvidence(StrictModel):
    calls: int = Field(ge=0)
    sharePercent: float = Field(ge=0, le=100)


class SmartInsightsKnowledgeGapInsight(StrictModel):
    knowledgeGapLabel: str
    primaryFrictionPointLabel: str
    recommendedInternalActionLabel: str
    conciseExplanation: str
    evidence: SmartInsightsEvidence


class SmartInsightsFailureTypeInsight(StrictModel):
    failureTypeLabel: str
    whyItHappens: str
    evidence: SmartInsightsEvidence
    relatedFriction: str
    relatedKnowledgeGap: str


class SmartInsightsPriorityActionItem(StrictModel):
    priority: int = Field(ge=1, le=3)
    actionTitle: str
    whyNow: str
    agentNextStep: str
    escalationTrigger: str
    appliesTo: str
    evidence: SmartInsightsEvidence


class SmartInsightsReport(StrictModel):
    meta: SmartInsightsMeta
    overview: SmartInsightsOverview
    knowledgeGapInsights: list[SmartInsightsKnowledgeGapInsight]
    failureTypeInsights: list[SmartInsightsFailureTypeInsight]
    priorityActionQueue: list[SmartInsightsPriorityActionItem]
    caveats: list[str]


SMART_INSIGHTS_RESPONSE_SCHEMA = SmartInsightsReport.model_json_schema()


def _read_path(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


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


def _pick_number(root: dict[str, Any], paths: list[str]) -> float | None:
    raw = _pick_raw_value(root, paths)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        parsed = float(raw)
        return parsed if math.isfinite(parsed) else None
    if isinstance(raw, str):
        try:
            parsed = float(raw)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _pick_string(root: dict[str, Any], paths: list[str]) -> str | None:
    value = _pick_raw_value(root, paths)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _to_camel_case(value: str) -> str:
    parts = value.split("_")
    if not parts:
        return value
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _normalize_token(value: str | None) -> str:
    if not isinstance(value, str):
        return "unknown"
    token = TOKEN_SANITIZER_PATTERN.sub("_", value.strip().lower()).strip("_")
    return token or "unknown"


def _humanize_token(value: str | None) -> str:
    token = _normalize_token(value)
    if token == "unknown":
        return "Unknown"
    return " ".join(part.capitalize() for part in token.split("_") if part)


def _coerce_string(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return str(value)
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [_coerce_string(item) for item in value]
        return [item for item in items if item]

    if isinstance(value, str):
        if not value.strip():
            return []
        parts = re.split(r"[,;|\n]", value)
        if len(parts) == 1:
            return [value.strip()]
        return [part.strip() for part in parts if part.strip()]

    return []


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return maximum
    return parsed


def _detail_fetch_cap() -> int:
    return _env_int(
        SMART_INSIGHTS_DETAIL_CAP_ENV,
        SMART_INSIGHTS_DETAIL_CAP_DEFAULT,
        minimum=1,
        maximum=MAX_CALLS,
    )


def _detail_fetch_concurrency() -> int:
    return _env_int(
        SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY_ENV,
        SMART_INSIGHTS_DETAIL_FETCH_CONCURRENCY_DEFAULT,
        minimum=1,
        maximum=32,
    )


def _data_field_candidates(field_key: str) -> tuple[str, ...]:
    return DATA_FIELD_ALIASES.get(field_key, (field_key,))


def _unwrap_data_collection_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "result", "status"):
            if key in value:
                return value[key]
    return value


def _extract_named_item_value(collection: Any, key: str) -> Any:
    normalized_key = _normalize_token(key)
    camel_key = _to_camel_case(key)

    if isinstance(collection, dict):
        for candidate in (key, camel_key):
            if candidate in collection:
                return collection.get(candidate)

        for item_key, item_value in collection.items():
            if _normalize_token(item_key) == normalized_key:
                return item_value

    if isinstance(collection, list):
        for item in collection:
            if not isinstance(item, dict):
                continue
            name = _pick_string(
                item,
                [
                    "name",
                    "key",
                    "id",
                    "slug",
                    "criterion",
                    "field",
                    "label",
                    "evaluation_criterion",
                    "evaluationCriterion",
                ],
            )
            if _normalize_token(name) != normalized_key:
                continue
            for value_key in (
                "value",
                "result",
                "status",
                "passed",
                "pass",
                "success",
                "successful",
                "is_successful",
                "isSuccessful",
                "score",
            ):
                if value_key in item:
                    return item[value_key]
            return item

    return None


def _data_collection_paths(field_key: str) -> list[str]:
    paths: list[str] = []
    for alias in _data_field_candidates(field_key):
        camel = _to_camel_case(alias)
        paths.extend(
            [
                f"analysis.data_collection_results.{alias}",
                f"analysis.dataCollectionResults.{alias}",
                f"analysis.data_collection_results.{camel}",
                f"analysis.dataCollectionResults.{camel}",
                f"data_collection_results.{alias}",
                f"dataCollectionResults.{alias}",
                f"metadata.data_collection_results.{alias}",
                f"metadata.dataCollectionResults.{alias}",
            ]
        )
    return paths


def _extract_data_field(conversation: dict[str, Any], field_key: str, *, multi: bool = False) -> str | list[str]:
    raw_value = _pick_raw_value(conversation, _data_collection_paths(field_key))

    if raw_value is None:
        for collection_path in (
            "analysis.data_collection_results",
            "analysis.dataCollectionResults",
            "data_collection_results",
            "dataCollectionResults",
            "metadata.data_collection_results",
            "metadata.dataCollectionResults",
        ):
            collection = _read_path(conversation, collection_path)
            for alias in _data_field_candidates(field_key):
                extracted = _extract_named_item_value(collection, alias)
                if extracted is None:
                    continue
                raw_value = extracted
                break
            if raw_value is not None:
                break

    raw_value = _unwrap_data_collection_value(raw_value)

    if multi:
        values = [_normalize_token(item) for item in _coerce_string_list(raw_value)]
        deduped: list[str] = []
        for value in values:
            if value in deduped:
                continue
            deduped.append(value)
        return deduped if deduped else ["unknown"]

    return _normalize_token(_coerce_string(raw_value))


def _criterion_aliases(criterion_key: CriterionKey) -> set[str]:
    if criterion_key == "human_escalation":
        return {
            "human_escalation",
            "human escalation",
            "escalation",
            "needs_human_escalation",
            "human_transfer",
        }
    if criterion_key == "intent_identification":
        return {
            "intent_identification",
            "intent identification",
            "intent_detection",
            "intent_detected",
            "intent_classification",
        }
    return {
        "call_cancellation",
        "call cancellation",
        "cancellation",
        "call_cancelled",
        "call_canceled",
        "call_cancelation",
    }


def _coerce_criterion_state(value: Any) -> CriterionState:
    if isinstance(value, bool):
        return "pass" if value else "fail"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        if not math.isfinite(parsed):
            return "unknown"
        return "pass" if parsed > 0 else "fail"

    if isinstance(value, str):
        normalized = _normalize_token(value)
        if normalized in {
            "pass",
            "passed",
            "true",
            "success",
            "successful",
            "succeeded",
            "yes",
            "met",
            "ok",
        }:
            return "pass"
        if normalized in {
            "fail",
            "failed",
            "false",
            "failure",
            "unsuccessful",
            "error",
            "no",
            "cancelled",
            "canceled",
            "not_met",
        }:
            return "fail"
        return "unknown"

    if isinstance(value, dict):
        for key in (
            "result",
            "status",
            "value",
            "passed",
            "pass",
            "success",
            "successful",
            "is_successful",
            "isSuccessful",
            "outcome",
            "score",
        ):
            if key in value:
                return _coerce_criterion_state(value[key])

    return "unknown"


def _extract_criterion_state(conversation: dict[str, Any], criterion_key: CriterionKey) -> CriterionState:
    aliases = _criterion_aliases(criterion_key)
    lookup_paths: list[str] = []
    for alias in aliases:
        normalized_alias = _normalize_token(alias)
        camel_alias = _to_camel_case(normalized_alias)
        lookup_paths.extend(
            [
                f"analysis.evaluation_criteria_results.{normalized_alias}",
                f"analysis.evaluation_criteria_results.{camel_alias}",
                f"analysis.evaluationCriteriaResults.{normalized_alias}",
                f"analysis.evaluationCriteriaResults.{camel_alias}",
                f"analysis.{normalized_alias}",
                f"analysis.{camel_alias}",
            ]
        )

    direct_value = _pick_raw_value(conversation, lookup_paths)
    direct_state = _coerce_criterion_state(direct_value)
    if direct_state != "unknown":
        return direct_state

    for collection_path in (
        "analysis.evaluation_criteria_results",
        "analysis.evaluationCriteriaResults",
        "analysis.evaluation_criteria",
        "analysis.evaluationCriteria",
        "metadata.evaluation_criteria_results",
        "metadata.evaluationCriteriaResults",
    ):
        collection = _read_path(conversation, collection_path)
        for alias in aliases:
            value = _extract_named_item_value(collection, alias)
            state = _coerce_criterion_state(value)
            if state != "unknown":
                return state

    return "unknown"


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


def _extract_conversation_id(conversation: dict[str, Any], index: int) -> str:
    conversation_id = _pick_string(
        conversation,
        [
            "conversation_id",
            "conversationId",
            "id",
        ],
    )
    if conversation_id:
        return conversation_id
    return f"unknown_{index + 1}"


def _extract_record(conversation: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "conversationId": _extract_conversation_id(conversation, index),
        "startTimeUnix": _extract_start_time_unix(conversation),
        "hotel_location": _extract_data_field(conversation, "hotel_location"),
        "recommended_internal_action": _extract_data_field(conversation, "recommended_internal_action"),
        "knowledge_gap_topic": _extract_data_field(conversation, "knowledge_gap_topic"),
        "primary_friction_point": _extract_data_field(conversation, "primary_friction_point"),
        "user_intent": _extract_data_field(conversation, "user_intent"),
        "resolution_status": _extract_data_field(conversation, "resolution_status"),
        "booking_stage": _extract_data_field(conversation, "booking_stage"),
        "topics": _extract_data_field(conversation, "topics", multi=True),
        "criteria": {
            "human_escalation": _extract_criterion_state(conversation, "human_escalation"),
            "intent_identification": _extract_criterion_state(conversation, "intent_identification"),
            "call_cancellation": _extract_criterion_state(conversation, "call_cancellation"),
        },
    }


def _sort_by_recency(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        conversations,
        key=lambda conversation: _extract_start_time_unix(conversation) or 0,
        reverse=True,
    )


async def _build_detail_records(
    conversations: list[dict[str, Any]],
    *,
    concurrency: int,
) -> tuple[list[dict[str, Any]], int]:
    semaphore = asyncio.Semaphore(concurrency)
    records: list[dict[str, Any] | None] = [None] * len(conversations)
    detail_fetch_failures = 0

    async def hydrate(index: int, summary_conversation: dict[str, Any]) -> None:
        nonlocal detail_fetch_failures

        fallback_record = _extract_record(summary_conversation, index)
        conversation_id = fallback_record.get("conversationId")
        if not isinstance(conversation_id, str) or not conversation_id or conversation_id.startswith("unknown_"):
            records[index] = fallback_record
            detail_fetch_failures += 1
            return

        try:
            async with semaphore:
                detail_payload = await get_conversation_details(conversation_id=conversation_id)
            detail_record = _extract_record(detail_payload.conversation, index)
            if (
                not isinstance(detail_record.get("startTimeUnix"), int)
                and isinstance(fallback_record.get("startTimeUnix"), int)
            ):
                detail_record["startTimeUnix"] = fallback_record["startTimeUnix"]
            if detail_record.get("conversationId", "").startswith("unknown_"):
                detail_record["conversationId"] = conversation_id
            records[index] = detail_record
        except Exception:
            detail_fetch_failures += 1
            records[index] = fallback_record

    await asyncio.gather(*(hydrate(index, conversation) for index, conversation in enumerate(conversations)))

    finalized = [record for record in records if isinstance(record, dict)]
    return finalized, detail_fetch_failures


def _resolve_window(timeline: TimelineKey, now_unix: int) -> WindowConfig:
    if timeline == "1d":
        return WindowConfig(start_time_unix=now_unix - 86400, end_time_unix=now_unix)
    if timeline == "7d":
        return WindowConfig(start_time_unix=now_unix - 7 * 86400, end_time_unix=now_unix)
    return WindowConfig(start_time_unix=now_unix - 30 * 86400, end_time_unix=now_unix)


def _is_missing_scalar(value: str) -> bool:
    return _normalize_token(value) in MISSING_VALUE_SET


def _is_missing_topics(topics: list[str]) -> bool:
    if not topics:
        return True
    return all(_normalize_token(topic) in MISSING_VALUE_SET for topic in topics)


def _percent(part: int | float, whole: int | float) -> float:
    if whole <= 0:
        return 0.0
    return round((float(part) / float(whole)) * 100.0, 1)


def _resolution_bucket(value: str) -> Literal["resolved", "unresolved", "unknown"]:
    normalized = _normalize_token(value)
    if normalized in {"resolved", "partially_resolved"}:
        return "resolved"
    if normalized in {"unresolved", "escalated"}:
        return "unresolved"
    return "unknown"


def _top_counter(counter: Counter[str], total_calls: int) -> dict[str, Any]:
    filtered = [(value, count) for value, count in counter.items() if not _is_missing_scalar(value)]
    if not filtered:
        return {"value": "unknown", "calls": 0, "sharePercent": 0.0}
    value, count = max(filtered, key=lambda item: (item[1], item[0]))
    return {
        "value": value,
        "calls": count,
        "sharePercent": _percent(count, total_calls),
    }


def _criteria_state_counts(records: list[dict[str, Any]]) -> dict[CriterionKey, dict[str, int]]:
    counts: dict[CriterionKey, dict[str, int]] = {
        "human_escalation": {"pass": 0, "fail": 0, "unknown": 0},
        "intent_identification": {"pass": 0, "fail": 0, "unknown": 0},
        "call_cancellation": {"pass": 0, "fail": 0, "unknown": 0},
    }
    for record in records:
        criteria = record.get("criteria")
        if not isinstance(criteria, dict):
            for criterion_key in CRITERIA_KEYS:
                counts[criterion_key]["unknown"] += 1
            continue
        for criterion_key in CRITERIA_KEYS:
            state = criteria.get(criterion_key)
            if state not in {"pass", "fail", "unknown"}:
                state = "unknown"
            counts[criterion_key][state] += 1
    return counts


def _build_missing_field_rates(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    total_calls = len(records)
    missing_rates: list[dict[str, Any]] = []

    if total_calls == 0:
        for field in (*DATA_FIELD_KEYS, *CRITERIA_KEYS):
            missing_rates.append({"field": field, "missingPercent": 100.0})
        return missing_rates, 0.0

    for field in SCALAR_FIELD_KEYS:
        missing_count = sum(1 for record in records if _is_missing_scalar(str(record.get(field, "unknown"))))
        missing_rates.append({"field": field, "missingPercent": _percent(missing_count, total_calls)})

    missing_topics = sum(1 for record in records if _is_missing_topics(record.get("topics", [])))
    missing_rates.append({"field": "topics", "missingPercent": _percent(missing_topics, total_calls)})

    for criterion_key in CRITERIA_KEYS:
        missing_count = 0
        for record in records:
            criteria = record.get("criteria")
            if not isinstance(criteria, dict):
                missing_count += 1
                continue
            if criteria.get(criterion_key) != "unknown":
                continue
            missing_count += 1
        missing_rates.append({"field": criterion_key, "missingPercent": _percent(missing_count, total_calls)})

    coverage_values = [100.0 - item["missingPercent"] for item in missing_rates]
    data_coverage = round(sum(coverage_values) / len(coverage_values), 1) if coverage_values else 0.0

    missing_rates.sort(key=lambda item: (-item["missingPercent"], item["field"]))
    return missing_rates, data_coverage


def _build_data_quality_caveats(
    *,
    total_calls: int,
    data_coverage_percent: float,
    criteria_unknown_rates: dict[CriterionKey, float],
    missing_field_rates: list[dict[str, Any]],
    truncated: bool,
    capped_by_detail_cap: bool,
    detail_fetch_failures: int,
) -> list[str]:
    caveats: list[str] = []

    if total_calls < 20:
        caveats.append("Low sample size for this period. Treat trends as directional, not final.")

    if data_coverage_percent < 70:
        caveats.append("Some call fields are often missing, so insights may miss part of the root cause.")

    if any(rate >= 40.0 for rate in criteria_unknown_rates.values()):
        caveats.append("Some call-quality checks are missing for many calls.")

    high_missing = [item for item in missing_field_rates if float(item.get("missingPercent", 0.0)) >= 30.0]
    if high_missing:
        top_fields = ", ".join(_humanize_token(str(item["field"])) for item in high_missing[:2])
        caveats.append(f"Missing data is most common in: {top_fields}.")

    if truncated:
        caveats.append("Data fetch reached the safety cap. A narrower timeline may improve precision.")

    if capped_by_detail_cap:
        caveats.append("Only the most recent calls were analyzed because of the detail analysis cap.")

    if detail_fetch_failures > 0:
        caveats.append(
            f"{detail_fetch_failures} call details could not be loaded, so summary data was used for those calls."
        )

    return caveats


def _resolution_stats(records: list[dict[str, Any]]) -> dict[str, float | int]:
    known_resolution = 0
    resolved_calls = 0
    unresolved_calls = 0

    for record in records:
        resolution_bucket = _resolution_bucket(str(record.get("resolution_status", "unknown")))
        if resolution_bucket in {"resolved", "unresolved"}:
            known_resolution += 1
            if resolution_bucket == "resolved":
                resolved_calls += 1
            else:
                unresolved_calls += 1

    return {
        "knownResolution": known_resolution,
        "resolvedCalls": resolved_calls,
        "unresolvedCalls": unresolved_calls,
        "resolutionRatePercent": _percent(resolved_calls, known_resolution),
        "unresolvedRatePercent": _percent(unresolved_calls, known_resolution),
    }


def _build_knowledge_gap_candidates(records: list[dict[str, Any]], total_calls: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        gap = str(record.get("knowledge_gap_topic", "unknown"))
        friction = str(record.get("primary_friction_point", "unknown"))
        action = str(record.get("recommended_internal_action", "unknown"))
        if _is_missing_scalar(gap) and _is_missing_scalar(friction) and _is_missing_scalar(action):
            continue
        grouped[(gap, friction, action)].append(record)

    candidates: list[dict[str, Any]] = []
    for (gap, friction, action), group_records in grouped.items():
        calls = len(group_records)
        known_resolution = 0
        unresolved_calls = 0
        for record in group_records:
            bucket = _resolution_bucket(str(record.get("resolution_status", "unknown")))
            if bucket in {"resolved", "unresolved"}:
                known_resolution += 1
                if bucket == "unresolved":
                    unresolved_calls += 1

        candidates.append(
            {
                "knowledgeGap": gap,
                "primaryFrictionPoint": friction,
                "recommendedInternalAction": action,
                "knowledgeGapLabel": _humanize_token(gap),
                "primaryFrictionPointLabel": _humanize_token(friction),
                "recommendedInternalActionLabel": _humanize_token(action),
                "calls": calls,
                "sharePercent": _percent(calls, total_calls),
                "unresolvedRatePercent": _percent(unresolved_calls, known_resolution),
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["calls"]),
            -float(item["unresolvedRatePercent"]),
            str(item["knowledgeGap"]),
            str(item["primaryFrictionPoint"]),
            str(item["recommendedInternalAction"]),
        )
    )
    return candidates


def _related_context(records: list[dict[str, Any]]) -> dict[str, str]:
    friction_counter: Counter[str] = Counter()
    gap_counter: Counter[str] = Counter()
    total = len(records)

    for record in records:
        friction = record.get("primary_friction_point")
        if isinstance(friction, str) and not _is_missing_scalar(friction):
            friction_counter[friction] += 1

        gap = record.get("knowledge_gap_topic")
        if isinstance(gap, str) and not _is_missing_scalar(gap):
            gap_counter[gap] += 1

    return {
        "relatedFriction": _humanize_token(_top_counter(friction_counter, total)["value"]),
        "relatedKnowledgeGap": _humanize_token(_top_counter(gap_counter, total)["value"]),
    }


def _build_failure_type_candidates(records: list[dict[str, Any]], total_calls: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for criterion_key in CRITERIA_KEYS:
        failed_records = [
            record
            for record in records
            if isinstance(record.get("criteria"), dict) and record["criteria"].get(criterion_key) == "fail"
        ]
        if not failed_records:
            continue

        context = _related_context(failed_records)
        candidates.append(
            {
                "failureTypeKey": criterion_key,
                "failureTypeLabel": CRITERION_LABELS[criterion_key],
                "calls": len(failed_records),
                "sharePercent": _percent(len(failed_records), total_calls),
                "relatedFriction": context["relatedFriction"],
                "relatedKnowledgeGap": context["relatedKnowledgeGap"],
                "unresolvedRatePercent": _resolution_stats(failed_records)["unresolvedRatePercent"],
            }
        )

    unresolved_records = [
        record for record in records if _resolution_bucket(str(record.get("resolution_status", "unknown"))) == "unresolved"
    ]
    if unresolved_records:
        context = _related_context(unresolved_records)
        candidates.append(
            {
                "failureTypeKey": "unresolved_outcome",
                "failureTypeLabel": "Issue remained unresolved",
                "calls": len(unresolved_records),
                "sharePercent": _percent(len(unresolved_records), total_calls),
                "relatedFriction": context["relatedFriction"],
                "relatedKnowledgeGap": context["relatedKnowledgeGap"],
                "unresolvedRatePercent": 100.0,
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["calls"]),
            -float(item["unresolvedRatePercent"]),
            str(item["failureTypeLabel"]),
        )
    )

    deduped: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for candidate in candidates:
        label = str(candidate["failureTypeLabel"])
        if label in seen_labels:
            continue
        seen_labels.add(label)
        deduped.append(candidate)

    return deduped


def _build_applies_to_label(records: list[dict[str, Any]]) -> str:
    counters: dict[str, Counter[str]] = {
        "user_intent": Counter(),
        "hotel_location": Counter(),
        "booking_stage": Counter(),
        "topics": Counter(),
    }

    for record in records:
        for field in ("user_intent", "hotel_location", "booking_stage"):
            value = record.get(field)
            if isinstance(value, str) and not _is_missing_scalar(value):
                counters[field][value] += 1

        topics = record.get("topics")
        if isinstance(topics, list):
            for topic in topics:
                if isinstance(topic, str) and not _is_missing_scalar(topic):
                    counters["topics"][topic] += 1

    options: list[tuple[str, str, int]] = []
    labels = {
        "user_intent": "customer intent",
        "hotel_location": "hotel location",
        "booking_stage": "booking stage",
        "topics": "topic",
    }

    for field, counter in counters.items():
        if not counter:
            continue
        value, count = max(counter.items(), key=lambda item: (item[1], item[0]))
        options.append((field, value, count))

    if not options:
        return "General customer calls"

    field, value, _ = max(options, key=lambda item: (item[2], item[0], item[1]))
    return f"Calls about {labels[field]} {_humanize_token(value)}"


def _most_common_failure_label(records: list[dict[str, Any]]) -> str:
    fail_counter: Counter[str] = Counter()
    for criterion_key in CRITERIA_KEYS:
        count = sum(
            1
            for record in records
            if isinstance(record.get("criteria"), dict) and record["criteria"].get(criterion_key) == "fail"
        )
        if count > 0:
            fail_counter[criterion_key] = count

    if not fail_counter:
        unresolved = sum(
            1
            for record in records
            if _resolution_bucket(str(record.get("resolution_status", "unknown"))) == "unresolved"
        )
        if unresolved > 0:
            return "Issue remained unresolved"
        return "No dominant failure type"

    top_key, _ = max(fail_counter.items(), key=lambda item: (item[1], item[0]))
    return CRITERION_LABELS[top_key]


def _build_priority_action_candidates(records: list[dict[str, Any]], total_calls: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        action = record.get("recommended_internal_action")
        if not isinstance(action, str) or _is_missing_scalar(action):
            continue
        grouped[action].append(record)

    candidates: list[dict[str, Any]] = []

    for action, action_records in grouped.items():
        calls = len(action_records)
        resolution_stats = _resolution_stats(action_records)
        failure_label = _most_common_failure_label(action_records)

        candidates.append(
            {
                "action": action,
                "actionTitle": _humanize_token(action),
                "calls": calls,
                "sharePercent": _percent(calls, total_calls),
                "unresolvedRatePercent": float(resolution_stats["unresolvedRatePercent"]),
                "appliesTo": _build_applies_to_label(action_records),
                "whyNowHint": (
                    f"This action appears in many calls and is frequently linked to {failure_label.lower()}."
                ),
                "agentNextStepHint": (
                    f"In the next similar call, follow the { _humanize_token(action) } guidance and confirm the customer outcome before ending the call."
                ),
                "escalationTriggerHint": "Escalate if the customer repeats the same issue after one clear solution attempt.",
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["calls"]),
            -float(item["unresolvedRatePercent"]),
            str(item["action"]),
        )
    )

    return candidates


def _determine_operational_status(resolution_rate_percent: float, unresolved_calls: int, total_calls: int) -> str:
    if total_calls == 0:
        return "stable"
    unresolved_share = _percent(unresolved_calls, total_calls)
    if resolution_rate_percent < 60.0 or unresolved_share >= 45.0:
        return "at_risk"
    if resolution_rate_percent < 80.0 or unresolved_share >= 25.0:
        return "watch"
    return "stable"


def _deterministic_overview(
    *,
    total_calls: int,
    resolution_rate_percent: float,
    unresolved_calls: int,
    top_failure_label: str,
    top_action_title: str,
) -> dict[str, str]:
    if total_calls == 0:
        return {
            "summary": "No calls were available for this timeline.",
            "operationalStatus": "stable",
            "topOpportunity": "Collect more calls before drawing operational conclusions.",
        }

    summary = (
        f"You handled {total_calls} calls in this period with a {round(resolution_rate_percent)}% resolution rate. "
        f"The most frequent failure pattern is {top_failure_label.lower()}."
    )
    opportunity = (
        f"Focus on {top_action_title.lower()} first to reduce repeated friction and unresolved calls."
        if top_action_title != "Unknown"
        else "Focus on the most repeated failure pattern first to reduce unresolved calls."
    )

    return {
        "summary": summary,
        "operationalStatus": _determine_operational_status(resolution_rate_percent, unresolved_calls, total_calls),
        "topOpportunity": opportunity,
    }


def _build_report_input(
    *,
    timeline: TimelineKey,
    generated_at_iso: str,
    total_calls: int,
    available_calls: int,
    analyzed_calls: int,
    detail_fetch_cap: int,
    capped_by_detail_cap: bool,
    detail_fetch_failures: int,
    data_coverage_percent: float,
    resolution_rate_percent: float,
    unresolved_calls: int,
    knowledge_gap_candidates: list[dict[str, Any]],
    failure_type_candidates: list[dict[str, Any]],
    action_candidates: list[dict[str, Any]],
    caveats: list[str],
) -> dict[str, Any]:
    top_failure = failure_type_candidates[0]["failureTypeLabel"] if failure_type_candidates else "No dominant failure type"
    top_action = action_candidates[0]["actionTitle"] if action_candidates else "Unknown"

    return {
        "meta": {
            "report_version": REPORT_VERSION,
            "timeline": timeline,
            "timezone": CUSTOMER_AGENT_CONFIG.timezone,
            "generated_at_iso": generated_at_iso,
            "total_calls": total_calls,
            "available_calls": available_calls,
            "analyzed_calls": analyzed_calls,
            "detail_fetch_cap": detail_fetch_cap,
            "capped_by_detail_cap": capped_by_detail_cap,
            "detail_fetch_failures": detail_fetch_failures,
            "data_coverage_percent": data_coverage_percent,
        },
        "overview_locked": {
            "resolution_rate_percent": resolution_rate_percent,
            "unresolved_calls": unresolved_calls,
            "top_failure_type": top_failure,
            "top_action": top_action,
        },
        "knowledge_gap_candidates": knowledge_gap_candidates[:8],
        "failure_type_candidates": failure_type_candidates[:8],
        "priority_action_candidates": action_candidates[:8],
        "caveats": caveats,
    }


SYSTEM_PROMPT = (
    "You are a support operations analyst writing reports for non-technical customer support agents. "
    "Return strictly valid JSON matching the schema. Do not output markdown. "
    "Use plain English. Avoid variable names, snake_case, and technical jargon. "
    "Do not mention weighted criteria, scoring formulas, or internal model mechanics. "
    "Keep explanations concise: one to two sentences each. "
    "Prioritize by frequency first, then unresolved impact. "
    "Action items must be practical and directly usable by frontline agents."
)


def _deterministic_fallback_knowledge_gap_insights(
    candidates: list[dict[str, Any]],
) -> list[SmartInsightsKnowledgeGapInsight]:
    insights: list[SmartInsightsKnowledgeGapInsight] = []

    for candidate in candidates[:3]:
        gap_label = str(candidate.get("knowledgeGapLabel", "Unknown"))
        friction_label = str(candidate.get("primaryFrictionPointLabel", "Unknown"))
        action_label = str(candidate.get("recommendedInternalActionLabel", "Unknown"))
        insights.append(
            SmartInsightsKnowledgeGapInsight.model_validate(
                {
                    "knowledgeGapLabel": gap_label,
                    "primaryFrictionPointLabel": friction_label,
                    "recommendedInternalActionLabel": action_label,
                    "conciseExplanation": (
                        f"Calls in this cluster often break down around {friction_label.lower()} when {gap_label.lower()} is not clear. "
                        f"Use {action_label.lower()} to reduce repeat confusion."
                    ),
                    "evidence": {
                        "calls": int(candidate.get("calls", 0)),
                        "sharePercent": float(candidate.get("sharePercent", 0.0)),
                    },
                }
            )
        )

    return insights


def _deterministic_fallback_failure_type_insights(
    candidates: list[dict[str, Any]],
) -> list[SmartInsightsFailureTypeInsight]:
    insights: list[SmartInsightsFailureTypeInsight] = []

    for candidate in candidates[:3]:
        failure_label = str(candidate.get("failureTypeLabel", "Unknown"))
        friction = str(candidate.get("relatedFriction", "Unknown"))
        gap = str(candidate.get("relatedKnowledgeGap", "Unknown"))
        insights.append(
            SmartInsightsFailureTypeInsight.model_validate(
                {
                    "failureTypeLabel": failure_label,
                    "whyItHappens": (
                        f"This issue appears repeatedly and is often tied to {friction.lower()} and gaps around {gap.lower()}."
                    ),
                    "evidence": {
                        "calls": int(candidate.get("calls", 0)),
                        "sharePercent": float(candidate.get("sharePercent", 0.0)),
                    },
                    "relatedFriction": friction,
                    "relatedKnowledgeGap": gap,
                }
            )
        )

    return insights


def _deterministic_fallback_priority_actions(
    candidates: list[dict[str, Any]],
) -> list[SmartInsightsPriorityActionItem]:
    actions: list[SmartInsightsPriorityActionItem] = []

    for index, candidate in enumerate(candidates[:3], start=1):
        actions.append(
            SmartInsightsPriorityActionItem.model_validate(
                {
                    "priority": index,
                    "actionTitle": str(candidate.get("actionTitle", "Follow standard playbook")),
                    "whyNow": str(
                        candidate.get(
                            "whyNowHint",
                            "This is one of the most repeated patterns in recent calls.",
                        )
                    ),
                    "agentNextStep": str(
                        candidate.get(
                            "agentNextStepHint",
                            "Apply the standard handling flow and confirm the customer is satisfied before ending.",
                        )
                    ),
                    "escalationTrigger": str(
                        candidate.get(
                            "escalationTriggerHint",
                            "Escalate if the customer repeats the issue after one clear solution attempt.",
                        )
                    ),
                    "appliesTo": str(candidate.get("appliesTo", "General customer calls")),
                    "evidence": {
                        "calls": int(candidate.get("calls", 0)),
                        "sharePercent": float(candidate.get("sharePercent", 0.0)),
                    },
                }
            )
        )

    return actions


async def _generate_llm_report(report_input: dict[str, Any]) -> SmartInsightsReport:
    payload = report_input
    last_error: ValidationError | None = None

    for attempt in range(2):
        raw_report = await create_structured_chat_completion(
            system_prompt=SYSTEM_PROMPT,
            user_payload=payload,
            json_schema=SMART_INSIGHTS_RESPONSE_SCHEMA,
            schema_name="smart_insights_report_v2",
            temperature=0.2,
        )

        try:
            return SmartInsightsReport.model_validate(raw_report)
        except ValidationError as exc:
            last_error = exc
            if attempt == 0:
                payload = {
                    **report_input,
                    "validation_feedback": (
                        "Your prior JSON did not validate. Fix schema compliance and output JSON only."
                    ),
                }
                continue
            raise

    if last_error is not None:
        raise last_error
    raise SmartInsightsGenerationError("Unexpected report validation failure.")


def _build_fallback_report(
    *,
    timeline: TimelineKey,
    generated_at_iso: str,
    total_calls: int,
    available_calls: int,
    analyzed_calls: int,
    detail_fetch_cap: int,
    capped_by_detail_cap: bool,
    detail_fetch_failures: int,
    data_coverage_percent: float,
    overview: dict[str, str],
    knowledge_gap_candidates: list[dict[str, Any]],
    failure_type_candidates: list[dict[str, Any]],
    action_candidates: list[dict[str, Any]],
    caveats: list[str],
) -> dict[str, Any]:
    report = SmartInsightsReport.model_validate(
        {
            "meta": {
                "reportVersion": REPORT_VERSION,
                "timeline": timeline,
                "generatedAtIso": generated_at_iso,
                "totalCalls": total_calls,
                "availableCalls": available_calls,
                "analyzedCalls": analyzed_calls,
                "detailFetchCap": detail_fetch_cap,
                "cappedByDetailCap": capped_by_detail_cap,
                "detailFetchFailures": detail_fetch_failures,
                "dataCoveragePercent": data_coverage_percent,
            },
            "overview": {
                "summary": overview["summary"],
                "operationalStatus": overview["operationalStatus"],
                "topOpportunity": overview["topOpportunity"],
            },
            "knowledgeGapInsights": [
                item.model_dump() for item in _deterministic_fallback_knowledge_gap_insights(knowledge_gap_candidates)
            ],
            "failureTypeInsights": [
                item.model_dump() for item in _deterministic_fallback_failure_type_insights(failure_type_candidates)
            ],
            "priorityActionQueue": [
                item.model_dump() for item in _deterministic_fallback_priority_actions(action_candidates)
            ],
            "caveats": caveats,
        }
    )
    return report.model_dump()


def _enforce_locked_fields(
    *,
    report: SmartInsightsReport,
    timeline: TimelineKey,
    generated_at_iso: str,
    total_calls: int,
    available_calls: int,
    analyzed_calls: int,
    detail_fetch_cap: int,
    capped_by_detail_cap: bool,
    detail_fetch_failures: int,
    data_coverage_percent: float,
    overview: dict[str, str],
    knowledge_gap_candidates: list[dict[str, Any]],
    failure_type_candidates: list[dict[str, Any]],
    action_candidates: list[dict[str, Any]],
    caveats: list[str],
) -> dict[str, Any]:
    report_data = report.model_dump()

    report_data["meta"] = {
        "reportVersion": REPORT_VERSION,
        "timeline": timeline,
        "generatedAtIso": generated_at_iso,
        "totalCalls": total_calls,
        "availableCalls": available_calls,
        "analyzedCalls": analyzed_calls,
        "detailFetchCap": detail_fetch_cap,
        "cappedByDetailCap": capped_by_detail_cap,
        "detailFetchFailures": detail_fetch_failures,
        "dataCoveragePercent": data_coverage_percent,
    }

    report_data["overview"] = {
        "summary": str(report_data.get("overview", {}).get("summary", "")).strip() or overview["summary"],
        "operationalStatus": report_data.get("overview", {}).get("operationalStatus") or overview["operationalStatus"],
        "topOpportunity": str(report_data.get("overview", {}).get("topOpportunity", "")).strip()
        or overview["topOpportunity"],
    }

    fallback_gap = _deterministic_fallback_knowledge_gap_insights(knowledge_gap_candidates)
    normalized_gap: list[SmartInsightsKnowledgeGapInsight] = []
    raw_gap = report_data.get("knowledgeGapInsights")
    if isinstance(raw_gap, list):
        for item in raw_gap:
            if not isinstance(item, dict):
                continue
            try:
                normalized_gap.append(SmartInsightsKnowledgeGapInsight.model_validate(item))
            except ValidationError:
                continue
    if len(normalized_gap) < 3:
        normalized_gap.extend(fallback_gap[: 3 - len(normalized_gap)])
    report_data["knowledgeGapInsights"] = [item.model_dump() for item in normalized_gap[:3]]

    fallback_failure = _deterministic_fallback_failure_type_insights(failure_type_candidates)
    normalized_failure: list[SmartInsightsFailureTypeInsight] = []
    raw_failure = report_data.get("failureTypeInsights")
    if isinstance(raw_failure, list):
        for item in raw_failure:
            if not isinstance(item, dict):
                continue
            try:
                normalized_failure.append(SmartInsightsFailureTypeInsight.model_validate(item))
            except ValidationError:
                continue
    if len(normalized_failure) < 3:
        normalized_failure.extend(fallback_failure[: 3 - len(normalized_failure)])
    report_data["failureTypeInsights"] = [item.model_dump() for item in normalized_failure[:3]]

    fallback_actions = _deterministic_fallback_priority_actions(action_candidates)
    normalized_actions: list[SmartInsightsPriorityActionItem] = []
    raw_actions = report_data.get("priorityActionQueue")
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            try:
                normalized_actions.append(SmartInsightsPriorityActionItem.model_validate(item))
            except ValidationError:
                continue
    if len(normalized_actions) < 3:
        normalized_actions.extend(fallback_actions[: 3 - len(normalized_actions)])

    normalized_actions.sort(
        key=lambda item: (
            int(item.priority),
            -int(item.evidence.calls),
            item.actionTitle,
        )
    )

    action_dump: list[dict[str, Any]] = []
    for index, action in enumerate(normalized_actions[:3], start=1):
        dumped = action.model_dump()
        dumped["priority"] = index
        action_dump.append(dumped)
    report_data["priorityActionQueue"] = action_dump

    model_caveats = report_data.get("caveats") if isinstance(report_data.get("caveats"), list) else []
    merged_caveats = [item for item in caveats if isinstance(item, str) and item.strip()]
    for caveat in model_caveats:
        if not isinstance(caveat, str):
            continue
        trimmed = caveat.strip()
        if not trimmed or trimmed in merged_caveats:
            continue
        merged_caveats.append(trimmed)
    if not merged_caveats:
        merged_caveats = ["No major data caveats were detected for this timeline."]
    report_data["caveats"] = merged_caveats

    return SmartInsightsReport.model_validate(report_data).model_dump()


def _empty_report(
    *,
    timeline: TimelineKey,
    generated_at_iso: str,
    data_coverage_percent: float,
    available_calls: int,
    analyzed_calls: int,
    detail_fetch_cap: int,
    capped_by_detail_cap: bool,
    detail_fetch_failures: int,
) -> dict[str, Any]:
    return SmartInsightsReport.model_validate(
        {
            "meta": {
                "reportVersion": REPORT_VERSION,
                "timeline": timeline,
                "generatedAtIso": generated_at_iso,
                "totalCalls": 0,
                "availableCalls": available_calls,
                "analyzedCalls": analyzed_calls,
                "detailFetchCap": detail_fetch_cap,
                "cappedByDetailCap": capped_by_detail_cap,
                "detailFetchFailures": detail_fetch_failures,
                "dataCoveragePercent": data_coverage_percent,
            },
            "overview": {
                "summary": "No calls were available for this timeline.",
                "operationalStatus": "stable",
                "topOpportunity": "Collect more calls before drawing operational conclusions.",
            },
            "knowledgeGapInsights": [],
            "failureTypeInsights": [],
            "priorityActionQueue": [],
            "caveats": ["No conversations available in this window."],
        }
    ).model_dump()


async def get_smart_insights_report(*, timeline: TimelineKey) -> dict[str, Any]:
    now_unix = int(datetime.now(tz=timezone.utc).timestamp())
    generated_at_iso = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    window = _resolve_window(timeline, now_unix)
    detail_fetch_cap = _detail_fetch_cap()
    detail_fetch_concurrency = _detail_fetch_concurrency()

    candidate_conversations: list[dict[str, Any]] = []
    cursor: str | None = None
    has_more = True
    pages = 0
    truncated = False

    while has_more and pages < MAX_PAGES and len(candidate_conversations) < MAX_CALLS:
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
            start_time = _extract_start_time_unix(conversation)
            if isinstance(start_time, int) and (start_time < window.start_time_unix or start_time > window.end_time_unix):
                continue
            candidate_conversations.append(conversation)
            if len(candidate_conversations) >= MAX_CALLS:
                truncated = True
                break

        has_more = payload.has_more
        cursor = payload.next_cursor

    if has_more:
        truncated = True

    available_calls = len(candidate_conversations)
    selected_conversations = _sort_by_recency(candidate_conversations)[:detail_fetch_cap]
    capped_by_detail_cap = available_calls > len(selected_conversations)

    if not selected_conversations:
        return _empty_report(
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            data_coverage_percent=0.0,
            available_calls=available_calls,
            analyzed_calls=0,
            detail_fetch_cap=detail_fetch_cap,
            capped_by_detail_cap=capped_by_detail_cap,
            detail_fetch_failures=0,
        )

    records, detail_fetch_failures = await _build_detail_records(
        selected_conversations,
        concurrency=detail_fetch_concurrency,
    )
    analyzed_calls = len(records)

    if not records:
        return _empty_report(
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            data_coverage_percent=0.0,
            available_calls=available_calls,
            analyzed_calls=analyzed_calls,
            detail_fetch_cap=detail_fetch_cap,
            capped_by_detail_cap=capped_by_detail_cap,
            detail_fetch_failures=detail_fetch_failures,
        )

    missing_field_rates, data_coverage_percent = _build_missing_field_rates(records)
    criteria_counts = _criteria_state_counts(records)
    criteria_unknown_rates: dict[CriterionKey, float] = {
        key: _percent(criteria_counts[key]["unknown"], len(records)) for key in CRITERIA_KEYS
    }
    resolution_stats = _resolution_stats(records)

    knowledge_gap_candidates = _build_knowledge_gap_candidates(records, len(records))
    failure_type_candidates = _build_failure_type_candidates(records, len(records))
    action_candidates = _build_priority_action_candidates(records, len(records))

    caveats = _build_data_quality_caveats(
        total_calls=len(records),
        data_coverage_percent=data_coverage_percent,
        criteria_unknown_rates=criteria_unknown_rates,
        missing_field_rates=missing_field_rates,
        truncated=truncated,
        capped_by_detail_cap=capped_by_detail_cap,
        detail_fetch_failures=detail_fetch_failures,
    )

    top_failure_label = (
        str(failure_type_candidates[0]["failureTypeLabel"]) if failure_type_candidates else "No dominant failure type"
    )
    top_action_title = str(action_candidates[0]["actionTitle"]) if action_candidates else "Unknown"

    overview = _deterministic_overview(
        total_calls=len(records),
        resolution_rate_percent=float(resolution_stats["resolutionRatePercent"]),
        unresolved_calls=int(resolution_stats["unresolvedCalls"]),
        top_failure_label=top_failure_label,
        top_action_title=top_action_title,
    )

    report_input = _build_report_input(
        timeline=timeline,
        generated_at_iso=generated_at_iso,
        total_calls=len(records),
        available_calls=available_calls,
        analyzed_calls=analyzed_calls,
        detail_fetch_cap=detail_fetch_cap,
        capped_by_detail_cap=capped_by_detail_cap,
        detail_fetch_failures=detail_fetch_failures,
        data_coverage_percent=data_coverage_percent,
        resolution_rate_percent=float(resolution_stats["resolutionRatePercent"]),
        unresolved_calls=int(resolution_stats["unresolvedCalls"]),
        knowledge_gap_candidates=knowledge_gap_candidates,
        failure_type_candidates=failure_type_candidates,
        action_candidates=action_candidates,
        caveats=caveats,
    )

    try:
        llm_report = await _generate_llm_report(report_input)
    except (OpenAIApiError, ValidationError):
        return _build_fallback_report(
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            total_calls=len(records),
            available_calls=available_calls,
            analyzed_calls=analyzed_calls,
            detail_fetch_cap=detail_fetch_cap,
            capped_by_detail_cap=capped_by_detail_cap,
            detail_fetch_failures=detail_fetch_failures,
            data_coverage_percent=data_coverage_percent,
            overview=overview,
            knowledge_gap_candidates=knowledge_gap_candidates,
            failure_type_candidates=failure_type_candidates,
            action_candidates=action_candidates,
            caveats=caveats,
        )

    try:
        return _enforce_locked_fields(
            report=llm_report,
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            total_calls=len(records),
            available_calls=available_calls,
            analyzed_calls=analyzed_calls,
            detail_fetch_cap=detail_fetch_cap,
            capped_by_detail_cap=capped_by_detail_cap,
            detail_fetch_failures=detail_fetch_failures,
            data_coverage_percent=data_coverage_percent,
            overview=overview,
            knowledge_gap_candidates=knowledge_gap_candidates,
            failure_type_candidates=failure_type_candidates,
            action_candidates=action_candidates,
            caveats=caveats,
        )
    except ValidationError as exc:
        raise SmartInsightsGenerationError("Failed to normalize Smart Insights report output.") from exc
