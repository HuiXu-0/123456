"""TASK-005-E 知识状态 Shadow 的离线观测、参数回放与准入门槛。

该模块只读取 LearningEvent 并在内存中回放候选策略，不写 Mastery、路径或 API 数据。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.knowledge_state import LearningEvent
from app.services.knowledge_state import (
    CONFLICT_CONFIDENCE_FACTOR,
    CONFLICT_SCORE_GAP,
    CONFLICT_WINDOW_HOURS,
    EVENT_WEIGHTS,
    NEUTRAL_MASTERY,
    _advance_values,
    is_conflicting_evidence,
)
from app.services.knowledge_state_shadow import BUCKETS, _bucket, _legacy

OBSERVATION_WINDOWS = (7, 30)
REQUIRED_SOURCES = frozenset({"quiz_result", "diagnostic", "feynman", "learning_step"})


@dataclass(frozen=True)
class ReplayPolicy:
    """只供离线候选回放使用；默认值严格等于线上 Shadow v1.1。"""

    name: str = "baseline"
    session_weight_caps: Mapping[str, float] = field(default_factory=dict)
    conflict_window_hours: float = CONFLICT_WINDOW_HOURS
    conflict_score_gap: float = CONFLICT_SCORE_GAP
    conflict_confidence_factor: float = CONFLICT_CONFIDENCE_FACTOR


DEFAULT_CAPPED_POLICY = ReplayPolicy(
    name="session-cap-v1-candidate",
    session_weight_caps={"diagnostic": 1.10, "feynman": 1.40, "learning_step": 0.45},
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _session_key(event: LearningEvent) -> tuple[str, str]:
    """相关证据按会话封顶；缺少会话语义的来源使用独立事件或 UTC 日。"""
    parts = event.source_id.split(":")
    if event.source_type.value in {"diagnostic", "feynman"} and len(parts) >= 3:
        return event.source_type.value, parts[1]
    if event.source_type.value == "learning_step":
        return event.source_type.value, event.timestamp.astimezone(timezone.utc).date().isoformat()
    return event.source_type.value, event.event_id


def replay_with_policy(events: Iterable[LearningEvent], policy: ReplayPolicy) -> dict[str, float]:
    ordered = sorted(events, key=lambda item: (item.timestamp, item.event_id))
    if not ordered:
        raise ValueError("LearningEvent 历史不能为空")
    owner = (ordered[0].user_id, ordered[0].knowledge_id)
    if any((item.user_id, item.knowledge_id) != owner for item in ordered):
        raise ValueError("一次回放只能包含同一用户、同一知识节点")

    mastery, confidence = NEUTRAL_MASTERY, 0.0
    previous_at: datetime | None = None
    prior: list[tuple[float, datetime]] = []
    consumed: defaultdict[tuple[str, str], float] = defaultdict(float)
    suppressed_weight = 0.0
    conflict_count = 0
    for event in ordered:
        occurred_at = _aware(event.timestamp).replace(tzinfo=None)
        base_weight = EVENT_WEIGHTS[event.event_type.value]
        cap = policy.session_weight_caps.get(event.source_type.value, math.inf)
        key = _session_key(event)
        effective_weight = max(0.0, min(base_weight, cap - consumed[key]))
        consumed[key] += effective_weight
        suppressed_weight += base_weight - effective_weight
        mastery, confidence = _advance_values(
            mastery,
            confidence,
            previous_at,
            event.event_type.value,
            event.score,
            occurred_at,
            event_weight=effective_weight,
        )
        conflicting = any(
            is_conflicting_evidence(
                old_score,
                old_at,
                event.score,
                occurred_at,
                window_hours=policy.conflict_window_hours,
                score_gap=policy.conflict_score_gap,
            )
            for old_score, old_at in prior
        )
        if conflicting:
            confidence *= policy.conflict_confidence_factor
            conflict_count += 1
        previous_at = occurred_at
        prior.append((event.score, occurred_at))
    return {
        "mastery": mastery,
        "confidence": confidence,
        "suppressedWeight": suppressed_weight,
        "conflictCount": float(conflict_count),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _activity_tier(count: int) -> str:
    if count <= 2:
        return "low"
    if count <= 9:
        return "medium"
    return "high"


def _policy_report(events: list[LearningEvent], policy: ReplayPolicy) -> dict[str, Any]:
    grouped: defaultdict[tuple[str, str], list[LearningEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.user_id, event.knowledge_id)].append(event)
    diffs: list[float] = []
    crossings = 0
    suppressed = 0.0
    conflicts = 0
    for history in grouped.values():
        legacy, _ = _legacy(history)
        replay = replay_with_policy(history, policy)
        suppressed += replay["suppressedWeight"]
        conflicts += int(replay["conflictCount"])
        if legacy is not None:
            diffs.append(abs(replay["mastery"] - legacy))
            crossings += int(_bucket(legacy) != _bucket(replay["mastery"]))
    return {
        "name": policy.name,
        "comparableNodeCount": len(diffs),
        "meanAbsoluteMasteryDifference": _mean(diffs),
        "thresholdMigrationRate": crossings / len(diffs) if diffs else None,
        "suppressedWeight": suppressed,
        "conflictCount": conflicts,
    }


def _window_report(events: list[LearningEvent], days: int, as_of: datetime) -> dict[str, Any]:
    start = as_of - timedelta(days=days)
    scoped = [event for event in events if start < _aware(event.timestamp) <= as_of]
    user_counts = Counter(event.user_id for event in scoped)
    source_counts = Counter(event.source_type.value for event in scoped)
    source_scores: defaultdict[str, list[float]] = defaultdict(list)
    node_counts = Counter(event.knowledge_id for event in scoped)
    for event in scoped:
        source_scores[event.source_type.value].append(event.score)
    baseline = _policy_report(scoped, ReplayPolicy()) if scoped else {
        "comparableNodeCount": 0,
        "meanAbsoluteMasteryDifference": None,
        "thresholdMigrationRate": None,
    }
    activity = Counter(_activity_tier(count) for count in user_counts.values())
    return {
        "days": days,
        "start": start.isoformat(),
        "end": as_of.isoformat(),
        "userCount": len(user_counts),
        "nodeCount": len({(event.user_id, event.knowledge_id) for event in scoped}),
        "eventCount": len(scoped),
        "sourceCoverage": sorted(source_counts),
        "sourceStrata": {
            source: {"eventCount": source_counts[source], "meanScore": _mean(source_scores[source])}
            for source in sorted(source_counts)
        },
        "nodeStrata": [
            {"knowledgeId": node, "eventCount": count}
            for node, count in sorted(node_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "activityStrata": {tier: activity[tier] for tier in ("low", "medium", "high")},
        "comparableNodeCount": baseline["comparableNodeCount"],
        "meanAbsoluteMasteryDifference": baseline["meanAbsoluteMasteryDifference"],
        "thresholdMigrationRate": baseline["thresholdMigrationRate"],
    }


def _manual_samples(events: list[LearningEvent], limit: int) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str], list[LearningEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.user_id, event.knowledge_id)].append(event)
    samples: list[dict[str, Any]] = []
    for (user_id, knowledge_id), history in grouped.items():
        legacy, _ = _legacy(history)
        if legacy is None:
            continue
        replay = replay_with_policy(history, ReplayPolicy())
        if _bucket(legacy) == _bucket(replay["mastery"]):
            continue
        samples.append({
            "userId": user_id,
            "knowledgeId": knowledge_id,
            "eventCount": len(history),
            "legacyMastery": legacy,
            "shadowMastery": replay["mastery"],
            "legacyBucket": _bucket(legacy),
            "shadowBucket": _bucket(replay["mastery"]),
            "absoluteDifference": abs(replay["mastery"] - legacy),
            "reviewStatus": "pending",
        })
    return sorted(samples, key=lambda item: (-item["absoluteDifference"], item["userId"], item["knowledgeId"]))[:limit]


def build_calibration_report(
    events: Iterable[LearningEvent],
    *,
    as_of: datetime,
    anomaly_counts: Mapping[str, int] | None = None,
    manual_sample_limit: int = 30,
) -> dict[str, Any]:
    """生成确定性 7/30 天观测、候选参数对比和 fail-closed 准入结果。"""
    as_of = _aware(as_of)
    unique = {event.event_id: event for event in events}
    ordered = sorted(unique.values(), key=lambda item: (item.timestamp, item.event_id))
    windows = {days: _window_report(ordered, days, as_of) for days in OBSERVATION_WINDOWS}
    recent = [event for event in ordered if as_of - timedelta(days=30) < _aware(event.timestamp) <= as_of]
    policies = [ReplayPolicy(), DEFAULT_CAPPED_POLICY]
    for hours in (12.0, 48.0):
        policies.append(ReplayPolicy(name=f"conflict-window-{int(hours)}h", conflict_window_hours=hours))
    for gap in (0.30, 0.50):
        policies.append(ReplayPolicy(name=f"conflict-gap-{gap:.2f}", conflict_score_gap=gap))
    for factor in (0.65, 0.85):
        policies.append(ReplayPolicy(name=f"conflict-factor-{factor:.2f}", conflict_confidence_factor=factor))
    candidate_reports = [_policy_report(recent, policy) for policy in policies] if recent else []
    samples = _manual_samples(recent, manual_sample_limit)
    anomalies = Counter(anomaly_counts or {})
    event_count = windows[30]["eventCount"]
    checks = {
        "minimum30DayEvents": event_count >= 200,
        "minimum30DayUsers": windows[30]["userCount"] >= 20,
        "minimumComparableNodes": windows[30]["comparableNodeCount"] >= 50,
        "requiredSourceCoverage": REQUIRED_SOURCES.issubset(windows[30]["sourceCoverage"]),
        "masteryMaeAtMost015": windows[30]["meanAbsoluteMasteryDifference"] is not None
        and windows[30]["meanAbsoluteMasteryDifference"] <= 0.15,
        "thresholdMigrationAtMost020": windows[30]["thresholdMigrationRate"] is not None
        and windows[30]["thresholdMigrationRate"] <= 0.20,
        "conflictRateAtMost010": event_count > 0 and anomalies["evidence_conflict"] / event_count <= 0.10,
        "duplicateRateAtMost005": event_count > 0 and anomalies["duplicate"] / event_count <= 0.05,
        "manualCrossThresholdReviewComplete": not samples,
    }
    return {
        "mode": "shadow-calibration",
        "asOf": as_of.isoformat(),
        "windows": {str(days): windows[days] for days in OBSERVATION_WINDOWS},
        "drift7dVs30d": {
            "eventCountRatio": windows[7]["eventCount"] / event_count if event_count else None,
            "masteryMaeDelta": (
                windows[7]["meanAbsoluteMasteryDifference"] - windows[30]["meanAbsoluteMasteryDifference"]
                if windows[7]["meanAbsoluteMasteryDifference"] is not None
                and windows[30]["meanAbsoluteMasteryDifference"] is not None else None
            ),
            "thresholdMigrationRateDelta": (
                windows[7]["thresholdMigrationRate"] - windows[30]["thresholdMigrationRate"]
                if windows[7]["thresholdMigrationRate"] is not None
                and windows[30]["thresholdMigrationRate"] is not None else None
            ),
        },
        "anomalyCounts": dict(sorted(anomalies.items())),
        "candidatePolicies": candidate_reports,
        "manualReviewSamples": samples,
        "admission": {"decision": "GO" if all(checks.values()) else "BLOCK", "checks": checks},
        "writesLegacyMastery": False,
    }
