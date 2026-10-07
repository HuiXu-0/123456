from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rag.calibration import (
    confidence_bucket_analysis,
    expected_calibration_error,
    recommend_threshold,
    shadow_gate,
)
from app.rag.protocol import CalibrationProfile, TrustReport


def _record(confidence: float, target: float, metric: float | None = None):
    return {
        "confidence": confidence,
        "target": target,
        "hit_rate@5": metric,
        "recall@5": metric,
        "mrr": metric,
    }


def test_confidence_buckets_include_boundaries_and_ignore_null_ranking_metrics():
    buckets = confidence_bucket_analysis(
        [_record(0.0, 0.0, None), _record(0.2, 1.0, 1.0), _record(1.0, 1.0, 0.5)]
    )
    assert [bucket["caseCount"] for bucket in buckets] == [1, 1, 0, 0, 1]
    assert buckets[0]["hitRateAt5"] is None
    assert buckets[1]["hitRateAt5"] == 1.0
    assert buckets[4]["mrr"] == 0.5


def test_expected_calibration_error_uses_fixed_five_buckets():
    records = [_record(0.1, 0.0), _record(0.9, 1.0)]
    assert expected_calibration_error(records) == pytest.approx(0.1)
    assert expected_calibration_error([]) == 0.0


def test_calibration_profile_is_embedding_profile_scoped_and_strict():
    profile = CalibrationProfile(
        profile_id="hash:deterministic-hash-v1:512",
        threshold=0.65,
        calibration_version="c2-40-v1",
    )
    assert profile.threshold == 0.65
    with pytest.raises(ValidationError):
        CalibrationProfile(profile_id="", threshold=0.5, calibration_version="v1")
    with pytest.raises(ValidationError):
        CalibrationProfile(profile_id="hash", threshold=1.1, calibration_version="v1")


def test_trust_report_schema_validates_bounds_and_counts():
    report = TrustReport(
        retrieval_confidence=0.7,
        evidence_count=3,
        source_count=2,
        coverage=2 / 3,
        reason_codes=["SINGLE_SOURCE"],
    )
    assert report.coverage == pytest.approx(2 / 3)
    with pytest.raises(ValidationError):
        TrustReport(
            retrieval_confidence=1.2,
            evidence_count=0,
            source_count=0,
            coverage=0,
        )


def test_shadow_gate_only_counts_and_does_not_mutate_records():
    records = [_record(0.4, 0.0), _record(0.8, 1.0)]
    snapshot = [dict(record) for record in records]
    profile = CalibrationProfile(profile_id="hash", threshold=0.6, calibration_version="v1")
    result = shadow_gate(records, profile)
    assert result["passCount"] == result["blockCount"] == 1
    assert result["mode"] == "shadow_only"
    assert records == snapshot


def test_threshold_recommendation_reports_balanced_accuracy():
    result = recommend_threshold(
        [_record(0.2, 0.0), _record(0.4, 0.0), _record(0.7, 1.0), _record(0.9, 1.0)]
    )
    assert result["threshold"] == 0.7
    assert result["balancedAccuracy"] == 1.0
