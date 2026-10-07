"""TASK-005-C Legacy Mastery 与 KnowledgeState 的离线 Shadow 对比。"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import LearningEventAnomalyRecord, LearningEventRecord
from app.schemas.knowledge_state import LearningEvent
from app.services.knowledge_state import replay_learning_events

LEGACY_CONFIDENCE = {"diagnostic": 0.45, "quiz": 0.85}
BUCKETS = ("unknown", "low", "learning", "proficient", "mastered")


def _bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < 0.5:
        return "low"
    if score < 0.7:
        return "learning"
    if score < 0.8:
        return "proficient"
    return "mastered"


def _legacy(events: list[LearningEvent]) -> tuple[float | None, float | None]:
    """复现现有覆盖口径：仅诊断/Quiz 写 Mastery，最后一条能力证据获胜。"""
    capability = [item for item in events if item.event_type.value in LEGACY_CONFIDENCE]
    if not capability:
        return None, None
    latest = max(capability, key=lambda item: (item.timestamp, item.event_id))
    return latest.score, LEGACY_CONFIDENCE[latest.event_type.value]


def evaluate_shadow(events: Iterable[LearningEvent]) -> dict[str, Any]:
    """对同一批历史同时计算 Legacy 与 KnowledgeState，返回节点差异和分布迁移。"""
    unique: dict[str, LearningEvent] = {}
    for event in events:
        previous = unique.get(event.event_id)
        if previous is not None and previous != event:
            raise ValueError(f"event_id 冲突: {event.event_id}")
        unique[event.event_id] = event

    grouped: dict[tuple[str, str], list[LearningEvent]] = defaultdict(list)
    for event in unique.values():
        grouped[(event.user_id, event.knowledge_id)].append(event)

    nodes: list[dict[str, Any]] = []
    legacy_distribution: Counter[str] = Counter()
    shadow_distribution: Counter[str] = Counter()
    mastery_diffs: list[float] = []
    confidence_diffs: list[float] = []
    for (user_id, knowledge_id), history in sorted(grouped.items()):
        legacy_mastery, legacy_confidence = _legacy(history)
        shadow = replay_learning_events(history)
        mastery_diff = (
            shadow.mastery_score - legacy_mastery
            if legacy_mastery is not None
            else None
        )
        confidence_diff = (
            shadow.confidence - legacy_confidence
            if legacy_confidence is not None
            else None
        )
        if mastery_diff is not None:
            mastery_diffs.append(mastery_diff)
        if confidence_diff is not None:
            confidence_diffs.append(confidence_diff)
        legacy_bucket = _bucket(legacy_mastery)
        shadow_bucket = _bucket(shadow.mastery_score)
        legacy_distribution[legacy_bucket] += 1
        shadow_distribution[shadow_bucket] += 1
        nodes.append(
            {
                "userId": user_id,
                "knowledgeId": knowledge_id,
                "eventCount": len(history),
                "legacyMastery": legacy_mastery,
                "knowledgeStateMastery": shadow.mastery_score,
                "masteryDifference": mastery_diff,
                "legacyConfidence": legacy_confidence,
                "knowledgeStateConfidence": shadow.confidence,
                "confidenceDifference": confidence_diff,
                "legacyBucket": legacy_bucket,
                "knowledgeStateBucket": shadow_bucket,
            }
        )

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "mode": "shadow",
        "userCount": len({event.user_id for event in unique.values()}),
        "eventCount": len(unique),
        "nodeCount": len(nodes),
        "comparableNodeCount": len(mastery_diffs),
        "meanMasteryDifference": mean(mastery_diffs),
        "meanAbsoluteMasteryDifference": mean([abs(v) for v in mastery_diffs]),
        "meanConfidenceDifference": mean(confidence_diffs),
        "distributionChange": {
            bucket: {
                "legacy": legacy_distribution[bucket],
                "knowledgeState": shadow_distribution[bucket],
                "delta": shadow_distribution[bucket] - legacy_distribution[bucket],
            }
            for bucket in BUCKETS
        },
        "nodes": nodes,
    }


def accumulated_shadow_statistics(db: Session) -> dict[str, Any]:
    """汇总真实旁路累计量、来源分布、状态差异与异常治理结果。"""
    rows = db.query(LearningEventRecord).order_by(
        LearningEventRecord.timestamp, LearningEventRecord.event_id
    ).all()
    events = [
        LearningEvent(
            event_id=row.event_id,
            user_id=row.user_id,
            knowledge_id=row.knowledge_id,
            event_type=row.event_type,
            source_type=row.source_type,
            source_id=row.source_id,
            algorithm_version=row.algorithm_version,
            score=row.score,
            timestamp=row.timestamp.replace(tzinfo=timezone.utc),
        )
        for row in rows
    ]
    report = evaluate_shadow(events) if events else {
        "mode": "shadow",
        "userCount": 0,
        "eventCount": 0,
        "nodeCount": 0,
        "comparableNodeCount": 0,
        "meanMasteryDifference": None,
        "meanAbsoluteMasteryDifference": None,
        "meanConfidenceDifference": None,
        "distributionChange": {},
        "nodes": [],
    }
    source_counts = Counter(row.source_type for row in rows)
    anomaly_counts = Counter(
        row.anomaly_type for row in db.query(LearningEventAnomalyRecord).all()
    )
    report["sourceCounts"] = dict(sorted(source_counts.items()))
    report["anomalyCounts"] = dict(sorted(anomaly_counts.items()))
    report["anomalyCount"] = sum(anomaly_counts.values())
    return report
