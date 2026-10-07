"""Retrieval Confidence离线校准统计（TASK-003-D2-B）。"""
from __future__ import annotations

from typing import Any

from app.rag.protocol import CalibrationProfile

CONFIDENCE_BUCKETS = (
    (0.0, 0.2, "0-0.2"),
    (0.2, 0.4, "0.2-0.4"),
    (0.4, 0.6, "0.4-0.6"),
    (0.6, 0.8, "0.6-0.8"),
    (0.8, 1.0, "0.8-1.0"),
)


def _bucket_label(confidence: float) -> str:
    for lower, upper, label in CONFIDENCE_BUCKETS:
        if lower <= confidence < upper or (upper == 1.0 and confidence == 1.0):
            return label
    raise ValueError(f"confidence必须位于0到1：{confidence}")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def confidence_bucket_analysis(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """固定五桶统计；case数量含无答案，排序指标忽略不可计算值。"""
    grouped = {label: [] for _, _, label in CONFIDENCE_BUCKETS}
    for record in records:
        confidence = float(record["confidence"])
        grouped[_bucket_label(confidence)].append(record)
    output = []
    for lower, upper, label in CONFIDENCE_BUCKETS:
        rows = grouped[label]
        output.append(
            {
                "bucket": label,
                "lower": lower,
                "upper": upper,
                "caseCount": len(rows),
                "hitRateAt5": _mean(
                    [float(row["hit_rate@5"]) for row in rows if row.get("hit_rate@5") is not None]
                ),
                "recallAt5": _mean(
                    [float(row["recall@5"]) for row in rows if row.get("recall@5") is not None]
                ),
                "mrr": _mean(
                    [float(row["mrr"]) for row in rows if row.get("mrr") is not None]
                ),
                "meanConfidence": _mean([float(row["confidence"]) for row in rows]),
                "positiveRate": _mean([float(row["target"]) for row in rows]),
            }
        )
    return output


def expected_calibration_error(records: list[dict[str, Any]]) -> float:
    """以Hit@5/无答案=0为目标的五桶ECE；仅作可靠性诊断。"""
    if not records:
        return 0.0
    buckets = confidence_bucket_analysis(records)
    total = len(records)
    return sum(
        (bucket["caseCount"] / total)
        * abs(float(bucket["meanConfidence"]) - float(bucket["positiveRate"]))
        for bucket in buckets
        if bucket["caseCount"]
    )


def shadow_gate(records: list[dict[str, Any]], profile: CalibrationProfile) -> dict[str, Any]:
    """只统计pass/block，不改变任何在线检索或回答。"""
    passed = sum(float(record["confidence"]) >= profile.threshold for record in records)
    total = len(records)
    return {
        "profile": profile.model_dump(mode="json"),
        "total": total,
        "passCount": passed,
        "blockCount": total - passed,
        "passRate": passed / total if total else 0.0,
        "blockRate": (total - passed) / total if total else 0.0,
        "mode": "shadow_only",
    }


def recommend_threshold(records: list[dict[str, Any]]) -> dict[str, float]:
    """按balanced accuracy选阈值；只给离线建议，不写在线配置。"""
    if not records:
        raise ValueError("阈值建议至少需要一条记录")
    candidates = sorted({0.0, 1.0, *(float(row["confidence"]) for row in records)})
    positives = sum(int(row["target"]) for row in records)
    negatives = len(records) - positives
    best: tuple[float, float, float, float] | None = None
    for threshold in candidates:
        tp = sum(int(row["target"]) == 1 and float(row["confidence"]) >= threshold for row in records)
        tn = sum(int(row["target"]) == 0 and float(row["confidence"]) < threshold for row in records)
        tpr = tp / positives if positives else 0.0
        tnr = tn / negatives if negatives else 0.0
        balanced_accuracy = (tpr + tnr) / 2
        candidate = (balanced_accuracy, tnr, tpr, threshold)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    return {
        "threshold": best[3],
        "balancedAccuracy": best[0],
        "trueNegativeRate": best[1],
        "truePositiveRate": best[2],
    }
