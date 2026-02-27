from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.customer_agent import CUSTOMER_AGENT_CONFIG
from app.providers.elevenlabs import list_agent_conversations
from app.providers.openai import OpenAIApiError, create_structured_chat_completion

TimelineKey = Literal["1d", "7d", "1m"]
SegmentType = Literal["hotel_location", "user_intent", "booking_stage", "topics"]
CriterionKey = Literal["human_escalation", "intent_identification", "call_cancellation"]
CriterionState = Literal["pass", "fail", "unknown"]

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 200
MAX_CALLS = 500

CRITERION_WEIGHTS: dict[CriterionKey, float] = {
    "human_escalation": 0.5,
    "intent_identification": 0.3,
    "call_cancellation": 0.2,
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

CRITERIA_KEYS: tuple[CriterionKey, ...] = (
    "human_escalation",
    "intent_identification",
    "call_cancellation",
)

MISSING_VALUE_SET = {"unknown", "none", "not_applicable", "n_a", "na"}
TOKEN_SANITIZER_PATTERN = re.compile(r"[^a-z0-9]+")


class SmartInsightsGenerationError(RuntimeError):
    """Raised when the Smart Insights report cannot be generated safely."""


@dataclass(frozen=True)
class WindowConfig:
    start_time_unix: int
    end_time_unix: int


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopMetricItem(StrictModel):
    value: str
    calls: int = Field(ge=0)
    sharePercent: float = Field(ge=0, le=100)


class SmartInsightsMeta(StrictModel):
    timeline: TimelineKey
    generatedAtIso: str
    totalCalls: int = Field(ge=0)
    dataCoveragePercent: float = Field(ge=0, le=100)


class SmartInsightsOverview(StrictModel):
    summary: str
    operationalStatus: Literal["stable", "watch", "at_risk"]
    topOpportunity: str


class SmartInsightsKpis(StrictModel):
    resolutionRatePercent: float = Field(ge=0, le=100)
    unresolvedCalls: int = Field(ge=0)
    criteriaHealthScore: float = Field(ge=0, le=100)
    topIntent: TopMetricItem
    topFrictionPoint: TopMetricItem


class SmartInsightsCriteriaWeights(StrictModel):
    humanEscalation: Literal[0.5]
    intentIdentification: Literal[0.3]
    callCancellation: Literal[0.2]


class SmartInsightsCriteriaRates(StrictModel):
    humanEscalation: float = Field(ge=0, le=100)
    intentIdentification: float = Field(ge=0, le=100)
    callCancellation: float = Field(ge=0, le=100)


class SmartInsightsCriteria(StrictModel):
    weights: SmartInsightsCriteriaWeights
    passRates: SmartInsightsCriteriaRates
    unknownRates: SmartInsightsCriteriaRates
    keyCriterionIssue: Literal["human_escalation", "intent_identification", "call_cancellation", "none"]


class SmartInsightsHotspot(StrictModel):
    segmentType: SegmentType
    segmentValue: str
    calls: int = Field(ge=0)
    unresolvedRatePercent: float = Field(ge=0, le=100)
    weightedCriteriaFailRatePercent: float = Field(ge=0, le=100)
    primaryFrictionPoint: str
    knowledgeGapTopic: str
    confidence: Literal["low", "medium", "high"]


class SmartInsightsActionEvidence(StrictModel):
    calls: int = Field(ge=0)
    sharePercent: float = Field(ge=0, le=100)


class SmartInsightsActionQueueItem(StrictModel):
    priority: int = Field(ge=1, le=5)
    recommendedInternalAction: str
    targetSegment: str
    linkedCriterion: Literal["human_escalation", "intent_identification", "call_cancellation", "none"]
    why: str
    expectedImpact: Literal["low", "medium", "high"]
    evidence: SmartInsightsActionEvidence


class SmartInsightsMissingFieldRate(StrictModel):
    field: str
    missingPercent: float = Field(ge=0, le=100)


class SmartInsightsDataQuality(StrictModel):
    missingFieldRates: list[SmartInsightsMissingFieldRate]
    caveats: list[str]


class SmartInsightsReport(StrictModel):
    meta: SmartInsightsMeta
    overview: SmartInsightsOverview
    kpis: SmartInsightsKpis
    criteria: SmartInsightsCriteria
    hotspots: list[SmartInsightsHotspot]
    actionQueue: list[SmartInsightsActionQueueItem]
    dataQuality: SmartInsightsDataQuality


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
    if raw is None:
        return None
    if isinstance(raw, bool):
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
    camel = _to_camel_case(field_key)
    return [
        f"analysis.data_collection_results.{field_key}",
        f"analysis.dataCollectionResults.{field_key}",
        f"analysis.data_collection_results.{camel}",
        f"analysis.dataCollectionResults.{camel}",
        f"data_collection_results.{field_key}",
        f"dataCollectionResults.{field_key}",
        f"metadata.data_collection_results.{field_key}",
        f"metadata.dataCollectionResults.{field_key}",
    ]


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
            extracted = _extract_named_item_value(collection, field_key)
            if extracted is not None:
                raw_value = extracted
                break

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


def _top_counter(counter: Counter[str], total_calls: int) -> dict[str, Any]:
    filtered = [(value, count) for value, count in counter.items() if value not in MISSING_VALUE_SET]
    if not filtered:
        return {"value": "unknown", "calls": 0, "sharePercent": 0.0}
    value, count = max(filtered, key=lambda item: (item[1], item[0]))
    return {
        "value": value,
        "calls": count,
        "sharePercent": _percent(count, total_calls),
    }


def _resolution_bucket(value: str) -> Literal["resolved", "unresolved", "unknown"]:
    normalized = _normalize_token(value)
    if normalized in {"resolved", "partially_resolved"}:
        return "resolved"
    if normalized in {"unresolved", "escalated"}:
        return "unresolved"
    return "unknown"


def _criteria_rates(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[CriterionKey, dict[str, int]] = {
        "human_escalation": {"pass": 0, "fail": 0, "unknown": 0},
        "intent_identification": {"pass": 0, "fail": 0, "unknown": 0},
        "call_cancellation": {"pass": 0, "fail": 0, "unknown": 0},
    }

    for record in records:
        criteria = record.get("criteria")
        if not isinstance(criteria, dict):
            continue

        for criterion_key in CRITERIA_KEYS:
            state = criteria.get(criterion_key)
            if state not in {"pass", "fail", "unknown"}:
                state = "unknown"
            counts[criterion_key][state] += 1

    pass_rates: dict[CriterionKey, float] = {
        "human_escalation": 0.0,
        "intent_identification": 0.0,
        "call_cancellation": 0.0,
    }
    fail_rates: dict[CriterionKey, float] = {
        "human_escalation": 0.0,
        "intent_identification": 0.0,
        "call_cancellation": 0.0,
    }
    unknown_rates: dict[CriterionKey, float] = {
        "human_escalation": 0.0,
        "intent_identification": 0.0,
        "call_cancellation": 0.0,
    }

    total_calls = len(records)
    for criterion_key in CRITERIA_KEYS:
        known = counts[criterion_key]["pass"] + counts[criterion_key]["fail"]
        pass_rates[criterion_key] = _percent(counts[criterion_key]["pass"], known)
        fail_rates[criterion_key] = _percent(counts[criterion_key]["fail"], known)
        unknown_rates[criterion_key] = _percent(counts[criterion_key]["unknown"], total_calls)

    weighted_fail_rate = 0.0
    contributions: dict[CriterionKey, float] = {
        "human_escalation": 0.0,
        "intent_identification": 0.0,
        "call_cancellation": 0.0,
    }

    for criterion_key in CRITERIA_KEYS:
        contribution = CRITERION_WEIGHTS[criterion_key] * fail_rates[criterion_key]
        contributions[criterion_key] = contribution
        weighted_fail_rate += contribution

    criterion_issue = "none"
    if any(contributions.values()):
        criterion_issue = max(contributions.items(), key=lambda item: item[1])[0]

    criteria_health_score = max(0.0, round(100.0 - weighted_fail_rate, 1))

    return {
        "counts": counts,
        "passRates": pass_rates,
        "failRates": fail_rates,
        "unknownRates": unknown_rates,
        "weightedFailRatePercent": round(weighted_fail_rate, 1),
        "criteriaHealthScore": criteria_health_score,
        "keyCriterionIssue": criterion_issue,
    }


def _weighted_fail_rate_for_subset(records: list[dict[str, Any]]) -> float:
    subset_stats = _criteria_rates(records)
    return float(subset_stats["weightedFailRatePercent"])


def _segment_confidence(calls: int) -> Literal["low", "medium", "high"]:
    if calls >= 30:
        return "high"
    if calls >= 10:
        return "medium"
    return "low"


def _build_segments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[SegmentType, str], list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        for field in ("hotel_location", "user_intent", "booking_stage"):
            value = record.get(field)
            if not isinstance(value, str):
                continue
            if _is_missing_scalar(value):
                continue
            grouped[(field, value)].append(record)

        topics = record.get("topics")
        if isinstance(topics, list):
            for topic in topics:
                if not isinstance(topic, str) or _is_missing_scalar(topic):
                    continue
                grouped[("topics", topic)].append(record)

    segments: list[dict[str, Any]] = []

    for (segment_type, segment_value), segment_records in grouped.items():
        calls = len(segment_records)

        known_resolution = 0
        unresolved_calls = 0
        friction_counter: Counter[str] = Counter()
        gap_counter: Counter[str] = Counter()

        for record in segment_records:
            resolution_bucket = _resolution_bucket(str(record.get("resolution_status", "unknown")))
            if resolution_bucket in {"resolved", "unresolved"}:
                known_resolution += 1
                if resolution_bucket == "unresolved":
                    unresolved_calls += 1

            primary_friction = record.get("primary_friction_point")
            if isinstance(primary_friction, str) and not _is_missing_scalar(primary_friction):
                friction_counter[primary_friction] += 1

            knowledge_gap = record.get("knowledge_gap_topic")
            if isinstance(knowledge_gap, str) and not _is_missing_scalar(knowledge_gap):
                gap_counter[knowledge_gap] += 1

        unresolved_rate = _percent(unresolved_calls, known_resolution)
        weighted_fail_rate = _weighted_fail_rate_for_subset(segment_records)

        top_friction = _top_counter(friction_counter, calls)["value"]
        top_gap = _top_counter(gap_counter, calls)["value"]

        segments.append(
            {
                "segmentType": segment_type,
                "segmentValue": segment_value,
                "calls": calls,
                "unresolvedRatePercent": unresolved_rate,
                "weightedCriteriaFailRatePercent": weighted_fail_rate,
                "primaryFrictionPoint": top_friction,
                "knowledgeGapTopic": top_gap,
                "confidence": _segment_confidence(calls),
            }
        )

    segments.sort(
        key=lambda item: (
            -item["weightedCriteriaFailRatePercent"],
            -item["unresolvedRatePercent"],
            -item["calls"],
            item["segmentType"],
            item["segmentValue"],
        )
    )

    return segments


def _build_action_candidates(records: list[dict[str, Any]], total_calls: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        action = record.get("recommended_internal_action")
        if not isinstance(action, str) or _is_missing_scalar(action):
            continue
        grouped[action].append(record)

    action_candidates: list[dict[str, Any]] = []

    for action, action_records in grouped.items():
        calls = len(action_records)

        intent_counter = Counter(
            value
            for value in (record.get("user_intent") for record in action_records)
            if isinstance(value, str) and not _is_missing_scalar(value)
        )
        hotel_counter = Counter(
            value
            for value in (record.get("hotel_location") for record in action_records)
            if isinstance(value, str) and not _is_missing_scalar(value)
        )

        target_segment = "general"
        if intent_counter:
            top_intent, _ = max(intent_counter.items(), key=lambda item: (item[1], item[0]))
            target_segment = f"user_intent:{top_intent}"
        elif hotel_counter:
            top_hotel, _ = max(hotel_counter.items(), key=lambda item: (item[1], item[0]))
            target_segment = f"hotel_location:{top_hotel}"

        subset_stats = _criteria_rates(action_records)
        fail_rates = subset_stats["failRates"]
        contributions = {
            criterion_key: CRITERION_WEIGHTS[criterion_key] * fail_rates[criterion_key]
            for criterion_key in CRITERIA_KEYS
        }
        linked_criterion: str = "none"
        if any(contributions.values()):
            linked_criterion = max(contributions.items(), key=lambda item: item[1])[0]

        action_candidates.append(
            {
                "recommendedInternalAction": action,
                "calls": calls,
                "sharePercent": _percent(calls, total_calls),
                "targetSegment": target_segment,
                "linkedCriterion": linked_criterion,
                "weightedCriteriaFailRatePercent": subset_stats["weightedFailRatePercent"],
            }
        )

    action_candidates.sort(
        key=lambda item: (
            -item["calls"],
            -item["weightedCriteriaFailRatePercent"],
            item["recommendedInternalAction"],
        )
    )

    return action_candidates


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


def _build_locked_metrics(records: list[dict[str, Any]], criteria_stats: dict[str, Any]) -> dict[str, Any]:
    total_calls = len(records)

    known_resolution = 0
    resolved_calls = 0
    unresolved_calls = 0

    intent_counter: Counter[str] = Counter()
    friction_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    gap_counter: Counter[str] = Counter()

    for record in records:
        resolution_bucket = _resolution_bucket(str(record.get("resolution_status", "unknown")))
        if resolution_bucket in {"resolved", "unresolved"}:
            known_resolution += 1
            if resolution_bucket == "resolved":
                resolved_calls += 1
            else:
                unresolved_calls += 1

        user_intent = record.get("user_intent")
        if isinstance(user_intent, str) and not _is_missing_scalar(user_intent):
            intent_counter[user_intent] += 1

        friction = record.get("primary_friction_point")
        if isinstance(friction, str) and not _is_missing_scalar(friction):
            friction_counter[friction] += 1

        gap = record.get("knowledge_gap_topic")
        if isinstance(gap, str) and not _is_missing_scalar(gap):
            gap_counter[gap] += 1

        topics = record.get("topics")
        if isinstance(topics, list):
            for topic in topics:
                if isinstance(topic, str) and not _is_missing_scalar(topic):
                    topic_counter[topic] += 1

    return {
        "resolutionRatePercent": _percent(resolved_calls, known_resolution),
        "unresolvedCalls": unresolved_calls,
        "criteriaHealthScore": criteria_stats["criteriaHealthScore"],
        "topIntent": _top_counter(intent_counter, total_calls),
        "topFrictionPoint": _top_counter(friction_counter, total_calls),
        "topTopic": _top_counter(topic_counter, total_calls),
        "topKnowledgeGap": _top_counter(gap_counter, total_calls),
        "criteriaPassRates": criteria_stats["passRates"],
        "criteriaUnknownRates": criteria_stats["unknownRates"],
        "keyCriterionIssue": criteria_stats["keyCriterionIssue"],
    }


def _build_data_quality_caveats(
    *,
    total_calls: int,
    data_coverage_percent: float,
    criteria_unknown_rates: dict[CriterionKey, float],
    truncated: bool,
) -> list[str]:
    caveats: list[str] = []

    if total_calls < 20:
        caveats.append("Low sample size for the selected period; treat trends as directional.")
    if data_coverage_percent < 70:
        caveats.append("Several extracted fields are missing frequently; insights may underrepresent root causes.")
    if any(rate >= 40.0 for rate in criteria_unknown_rates.values()):
        caveats.append("Evaluation criteria coverage is incomplete for many calls.")
    if truncated:
        caveats.append("Data fetch reached safety cap; consider a narrower timeline for full precision.")

    return caveats


def _build_report_input(
    *,
    timeline: TimelineKey,
    generated_at_iso: str,
    total_calls: int,
    data_coverage_percent: float,
    locked_metrics: dict[str, Any],
    segments: list[dict[str, Any]],
    action_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "meta": {
            "timeline": timeline,
            "timezone": CUSTOMER_AGENT_CONFIG.timezone,
            "generated_at_iso": generated_at_iso,
            "total_calls": total_calls,
            "data_coverage_percent": data_coverage_percent,
        },
        "weights": {
            "human_escalation": CRITERION_WEIGHTS["human_escalation"],
            "intent_identification": CRITERION_WEIGHTS["intent_identification"],
            "call_cancellation": CRITERION_WEIGHTS["call_cancellation"],
        },
        "locked_metrics": {
            "resolution_rate_percent": locked_metrics["resolutionRatePercent"],
            "unresolved_calls": locked_metrics["unresolvedCalls"],
            "criteria_health_score": locked_metrics["criteriaHealthScore"],
            "top_intent": locked_metrics["topIntent"],
            "top_friction_point": locked_metrics["topFrictionPoint"],
            "top_topic": locked_metrics["topTopic"],
            "top_knowledge_gap": locked_metrics["topKnowledgeGap"],
            "criteria_pass_rates": locked_metrics["criteriaPassRates"],
            "criteria_unknown_rates": locked_metrics["criteriaUnknownRates"],
            "key_criterion_issue": locked_metrics["keyCriterionIssue"],
        },
        "hotspot_candidates": segments[:15],
        "action_candidates": action_candidates[:10],
    }


SYSTEM_PROMPT = (
    "You are a support operations analyst for customer support agents. "
    "Produce strictly valid JSON matching the provided schema. "
    "Do not output markdown. "
    "Use only values from the input payload and do not invent categories or segments. "
    "Every hotspot and action must include numeric evidence. "
    "Prioritize recommendations using impact multiplied by frequency, with weighted criteria emphasis. "
    "If evidence is weak due to sample size or missing fields, set confidence to low and mention caveats."
)


def _deterministic_fallback_hotspots(segments: list[dict[str, Any]]) -> list[SmartInsightsHotspot]:
    return [SmartInsightsHotspot.model_validate(item) for item in segments[:8]]


def _deterministic_fallback_actions(action_candidates: list[dict[str, Any]]) -> list[SmartInsightsActionQueueItem]:
    impact_map = {
        "high": 60.0,
        "medium": 30.0,
    }
    actions: list[SmartInsightsActionQueueItem] = []
    for index, item in enumerate(action_candidates[:5], start=1):
        weighted_fail_rate = float(item["weightedCriteriaFailRatePercent"])
        expected_impact = "low"
        if weighted_fail_rate >= impact_map["high"]:
            expected_impact = "high"
        elif weighted_fail_rate >= impact_map["medium"]:
            expected_impact = "medium"

        actions.append(
            SmartInsightsActionQueueItem.model_validate(
                {
                    "priority": index,
                    "recommendedInternalAction": item["recommendedInternalAction"],
                    "targetSegment": item["targetSegment"],
                    "linkedCriterion": item["linkedCriterion"],
                    "why": "High recurrence and elevated criteria failure in this segment.",
                    "expectedImpact": expected_impact,
                    "evidence": {
                        "calls": item["calls"],
                        "sharePercent": item["sharePercent"],
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
            schema_name="smart_insights_report",
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


def _enforce_locked_fields(
    *,
    report: SmartInsightsReport,
    timeline: TimelineKey,
    generated_at_iso: str,
    total_calls: int,
    data_coverage_percent: float,
    locked_metrics: dict[str, Any],
    missing_field_rates: list[dict[str, Any]],
    caveats: list[str],
    segments: list[dict[str, Any]],
    action_candidates: list[dict[str, Any]],
) -> SmartInsightsReport:
    report_data = report.model_dump()

    report_data["meta"] = {
        "timeline": timeline,
        "generatedAtIso": generated_at_iso,
        "totalCalls": total_calls,
        "dataCoveragePercent": data_coverage_percent,
    }

    report_data["kpis"] = {
        "resolutionRatePercent": locked_metrics["resolutionRatePercent"],
        "unresolvedCalls": locked_metrics["unresolvedCalls"],
        "criteriaHealthScore": locked_metrics["criteriaHealthScore"],
        "topIntent": locked_metrics["topIntent"],
        "topFrictionPoint": locked_metrics["topFrictionPoint"],
    }

    report_data["criteria"] = {
        "weights": {
            "humanEscalation": CRITERION_WEIGHTS["human_escalation"],
            "intentIdentification": CRITERION_WEIGHTS["intent_identification"],
            "callCancellation": CRITERION_WEIGHTS["call_cancellation"],
        },
        "passRates": {
            "humanEscalation": locked_metrics["criteriaPassRates"]["human_escalation"],
            "intentIdentification": locked_metrics["criteriaPassRates"]["intent_identification"],
            "callCancellation": locked_metrics["criteriaPassRates"]["call_cancellation"],
        },
        "unknownRates": {
            "humanEscalation": locked_metrics["criteriaUnknownRates"]["human_escalation"],
            "intentIdentification": locked_metrics["criteriaUnknownRates"]["intent_identification"],
            "callCancellation": locked_metrics["criteriaUnknownRates"]["call_cancellation"],
        },
        "keyCriterionIssue": locked_metrics["keyCriterionIssue"],
    }

    if not report_data.get("hotspots"):
        report_data["hotspots"] = [item.model_dump() for item in _deterministic_fallback_hotspots(segments)]

    if not report_data.get("actionQueue"):
        report_data["actionQueue"] = [item.model_dump() for item in _deterministic_fallback_actions(action_candidates)]

    normalized_actions: list[dict[str, Any]] = []
    raw_actions = report_data.get("actionQueue")
    if isinstance(raw_actions, list):
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                continue
            normalized_actions.append(raw_action)

    normalized_actions.sort(
        key=lambda item: (
            int(item.get("priority", 99)),
            -int(_read_path(item, "evidence.calls") or 0),
            str(item.get("recommendedInternalAction", "")),
        )
    )
    for index, action in enumerate(normalized_actions[:5], start=1):
        action["priority"] = index
    report_data["actionQueue"] = normalized_actions[:5]

    raw_hotspots = report_data.get("hotspots")
    normalized_hotspots: list[dict[str, Any]] = []
    if isinstance(raw_hotspots, list):
        for raw_hotspot in raw_hotspots:
            if isinstance(raw_hotspot, dict):
                normalized_hotspots.append(raw_hotspot)

    normalized_hotspots.sort(
        key=lambda item: (
            -float(item.get("weightedCriteriaFailRatePercent", 0.0)),
            -float(item.get("unresolvedRatePercent", 0.0)),
            -int(item.get("calls", 0)),
        )
    )
    report_data["hotspots"] = normalized_hotspots[:12]

    model_caveats = report_data.get("dataQuality", {}).get("caveats") if isinstance(report_data.get("dataQuality"), dict) else None
    merged_caveats = [caveat for caveat in caveats]
    if isinstance(model_caveats, list):
        for caveat in model_caveats:
            if not isinstance(caveat, str):
                continue
            trimmed = caveat.strip()
            if not trimmed or trimmed in merged_caveats:
                continue
            merged_caveats.append(trimmed)

    report_data["dataQuality"] = {
        "missingFieldRates": missing_field_rates,
        "caveats": merged_caveats,
    }

    return SmartInsightsReport.model_validate(report_data)


def _empty_report(
    *,
    timeline: TimelineKey,
    generated_at_iso: str,
    missing_field_rates: list[dict[str, Any]],
    data_coverage_percent: float,
) -> SmartInsightsReport:
    return SmartInsightsReport.model_validate(
        {
            "meta": {
                "timeline": timeline,
                "generatedAtIso": generated_at_iso,
                "totalCalls": 0,
                "dataCoveragePercent": data_coverage_percent,
            },
            "overview": {
                "summary": "No calls found for the selected timeline.",
                "operationalStatus": "stable",
                "topOpportunity": "Collect more calls before drawing operational conclusions.",
            },
            "kpis": {
                "resolutionRatePercent": 0.0,
                "unresolvedCalls": 0,
                "criteriaHealthScore": 100.0,
                "topIntent": {"value": "unknown", "calls": 0, "sharePercent": 0.0},
                "topFrictionPoint": {"value": "unknown", "calls": 0, "sharePercent": 0.0},
            },
            "criteria": {
                "weights": {
                    "humanEscalation": 0.5,
                    "intentIdentification": 0.3,
                    "callCancellation": 0.2,
                },
                "passRates": {
                    "humanEscalation": 0.0,
                    "intentIdentification": 0.0,
                    "callCancellation": 0.0,
                },
                "unknownRates": {
                    "humanEscalation": 100.0,
                    "intentIdentification": 100.0,
                    "callCancellation": 100.0,
                },
                "keyCriterionIssue": "none",
            },
            "hotspots": [],
            "actionQueue": [],
            "dataQuality": {
                "missingFieldRates": missing_field_rates,
                "caveats": ["No conversations available in this window."],
            },
        }
    )


async def get_smart_insights_report(*, timeline: TimelineKey) -> dict[str, Any]:
    now_unix = int(datetime.now(tz=timezone.utc).timestamp())
    generated_at_iso = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    window = _resolve_window(timeline, now_unix)

    records: list[dict[str, Any]] = []
    cursor: str | None = None
    has_more = True
    pages = 0
    truncated = False

    while has_more and pages < MAX_PAGES and len(records) < MAX_CALLS:
        payload = await list_agent_conversations(
            agent_id=CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id,
            page_size=DEFAULT_PAGE_SIZE,
            cursor=cursor,
            search=None,
            start_time_unix=window.start_time_unix,
            end_time_unix=window.end_time_unix,
        )
        pages += 1

        for index, conversation in enumerate(payload.conversations):
            record = _extract_record(conversation, index)
            start_time = record.get("startTimeUnix")
            if isinstance(start_time, int) and (start_time < window.start_time_unix or start_time > window.end_time_unix):
                continue
            records.append(record)
            if len(records) >= MAX_CALLS:
                truncated = True
                break

        has_more = payload.has_more
        cursor = payload.next_cursor

    if has_more:
        truncated = True

    missing_field_rates, data_coverage_percent = _build_missing_field_rates(records)

    if not records:
        return _empty_report(
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            missing_field_rates=missing_field_rates,
            data_coverage_percent=data_coverage_percent,
        ).model_dump()

    criteria_stats = _criteria_rates(records)
    locked_metrics = _build_locked_metrics(records, criteria_stats)
    segments = _build_segments(records)
    action_candidates = _build_action_candidates(records, len(records))

    caveats = _build_data_quality_caveats(
        total_calls=len(records),
        data_coverage_percent=data_coverage_percent,
        criteria_unknown_rates=criteria_stats["unknownRates"],
        truncated=truncated,
    )

    report_input = _build_report_input(
        timeline=timeline,
        generated_at_iso=generated_at_iso,
        total_calls=len(records),
        data_coverage_percent=data_coverage_percent,
        locked_metrics=locked_metrics,
        segments=segments,
        action_candidates=action_candidates,
    )

    try:
        llm_report = await _generate_llm_report(report_input)
    except (OpenAIApiError, ValidationError) as exc:
        raise SmartInsightsGenerationError("Failed to generate structured Smart Insights report.") from exc

    try:
        finalized_report = _enforce_locked_fields(
            report=llm_report,
            timeline=timeline,
            generated_at_iso=generated_at_iso,
            total_calls=len(records),
            data_coverage_percent=data_coverage_percent,
            locked_metrics=locked_metrics,
            missing_field_rates=missing_field_rates,
            caveats=caveats,
            segments=segments,
            action_candidates=action_candidates,
        )
    except ValidationError as exc:
        raise SmartInsightsGenerationError("Failed to normalize Smart Insights report output.") from exc

    return finalized_report.model_dump()
