"""TASK-005-E 7/30 天 Shadow 观测、校准候选与准入门槛测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.knowledge_state import LearningEvent
from app.services.knowledge_state import ALGORITHM_VERSION
from app.services.knowledge_state_calibration import (
    DEFAULT_CAPPED_POLICY,
    ReplayPolicy,
    build_calibration_report,
    replay_with_policy,
)

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def _event(index: int, *, days: int, source: str, score: float, session: str = "s1") -> LearningEvent:
    event_type = "quiz" if source == "quiz_result" else source
    source_id = f"{source}:{session}:item-{index}"
    return LearningEvent(
        event_id=f"le_cal_{index}_{days}_{source}", user_id="u_10001", knowledge_id="ml",
        event_type=event_type, source_type=source, source_id=source_id,
        algorithm_version=ALGORITHM_VERSION, score=score,
        timestamp=NOW - timedelta(days=days, minutes=-index),
    )


def test_report_has_7_30_day_strata_drift_candidates_and_fail_closed_gate():
    events = [
        _event(1, days=20, source="quiz_result", score=0.76),
        _event(2, days=5, source="diagnostic", score=0.32),
        _event(3, days=4, source="feynman", score=0.82),
        _event(4, days=3, source="learning_step", score=1.0),
    ]
    report = build_calibration_report(events, as_of=NOW, anomaly_counts={"duplicate": 1})

    assert report["windows"]["7"]["eventCount"] == 3
    assert report["windows"]["30"]["eventCount"] == 4
    assert report["windows"]["30"]["sourceCoverage"] == [
        "diagnostic", "feynman", "learning_step", "quiz_result"
    ]
    assert report["windows"]["30"]["activityStrata"]["medium"] == 1
    assert report["drift7dVs30d"]["eventCountRatio"] == pytest.approx(0.75)
    assert {item["name"] for item in report["candidatePolicies"]} >= {
        "baseline", "session-cap-v1-candidate", "conflict-window-12h"
    }
    assert report["admission"]["decision"] == "BLOCK"
    assert report["writesLegacyMastery"] is False


def test_session_cap_suppresses_correlated_evidence_without_changing_baseline():
    events = [
        _event(index, days=1, source="feynman", score=0.95, session="same")
        for index in range(1, 5)
    ]
    baseline = replay_with_policy(events, ReplayPolicy())
    capped = replay_with_policy(events, DEFAULT_CAPPED_POLICY)

    assert baseline["suppressedWeight"] == pytest.approx(0.0)
    assert capped["suppressedWeight"] == pytest.approx(1.4)
    assert capped["mastery"] < baseline["mastery"]
    assert capped["confidence"] < baseline["confidence"]


def test_manual_review_list_contains_threshold_crossing_and_blocks_admission():
    events = [
        _event(1, days=2, source="quiz_result", score=0.72),
        _event(2, days=1, source="feynman", score=0.98),
    ]
    report = build_calibration_report(events, as_of=NOW)

    sample = report["manualReviewSamples"][0]
    assert sample["legacyBucket"] == "proficient"
    assert sample["shadowBucket"] == "mastered"
    assert sample["reviewStatus"] == "pending"
    assert report["admission"]["checks"]["manualCrossThresholdReviewComplete"] is False
