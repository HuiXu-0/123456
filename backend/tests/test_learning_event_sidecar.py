"""TASK-005-D 真实旁路累计、幂等、隔离与冲突治理测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal
from app.models.entities import (
    LearningEventAnomalyRecord,
    LearningEventRecord,
    LearningStepProgress,
    Mastery,
    QuizAttempt,
    UserKnowledgeStateRecord,
)
from app.services.knowledge_state import (
    CONFLICT_CONFIDENCE_FACTOR,
    _advance_values,
)
from app.services.knowledge_state_shadow import accumulated_shadow_statistics
from app.services.learning_evidence import (
    capture_shadow_event,
    from_diagnostic,
    from_feynman,
    from_learning_step,
    from_quiz_result,
)

BASE = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_shadow_records():
    with SessionLocal() as db:
        db.query(LearningEventAnomalyRecord).delete()
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(LearningEventAnomalyRecord).delete()
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()


def _mastery_snapshot() -> list[tuple]:
    with SessionLocal() as db:
        return [
            (row.user_id, row.kp_id, row.status, row.score, row.confidence, row.score_source)
            for row in db.query(Mastery).order_by(Mastery.user_id, Mastery.kp_id).all()
        ]


def test_four_sources_accumulate_real_shadow_statistics_without_mastery_write():
    before = _mastery_snapshot()
    events = [
        from_quiz_result(
            QuizAttempt(
                id=501,
                user_id="u_10001",
                kp_id="ml",
                score=76,
                correct_count=7,
                total=10,
                passed=True,
                created_at=BASE,
            )
        ),
        from_diagnostic(
            user_id="u_10001",
            result={"kpId": "ml", "correct": True},
            source_id="diagnostic:d-real:q-1",
            occurred_at=BASE + timedelta(minutes=1),
        ),
        from_feynman(
            user_id="u_10001",
            knowledge_id="ml",
            result={"score": 82},
            source_id="feynman:f-real:turn-1",
            occurred_at=BASE + timedelta(minutes=2),
        ),
        from_learning_step(
            LearningStepProgress(
                id=502,
                user_id="u_10001",
                kp_id="ml",
                step="lecture",
                done=True,
                updated_at=BASE + timedelta(minutes=3),
            )
        ),
    ]

    assert [capture_shadow_event(event) for event in events] == ["captured"] * 4
    with SessionLocal() as db:
        report = accumulated_shadow_statistics(db)

    assert report["userCount"] == 1
    assert report["nodeCount"] == 1
    assert report["eventCount"] == 4
    assert report["sourceCounts"] == {
        "diagnostic": 1,
        "feynman": 1,
        "learning_step": 1,
        "quiz_result": 1,
    }
    assert report["meanMasteryDifference"] is not None
    assert report["meanConfidenceDifference"] is not None
    assert _mastery_snapshot() == before


def test_stable_source_id_makes_event_id_and_capture_idempotent():
    first = from_diagnostic(
        user_id="u_10001",
        result={"kpId": "ml", "correct": True},
        source_id="diagnostic:d-retry:q-1",
        occurred_at=BASE,
    )
    retried = from_diagnostic(
        user_id="u_10001",
        result={"kpId": "ml", "correct": True},
        source_id="diagnostic:d-retry:q-1",
        occurred_at=BASE + timedelta(seconds=5),
    )

    assert retried.event_id == first.event_id
    assert capture_shadow_event(first) == "captured"
    assert capture_shadow_event(retried) == "duplicate"
    with SessionLocal() as db:
        assert db.query(LearningEventRecord).count() == 1
        anomaly = db.query(LearningEventAnomalyRecord).one()
        assert anomaly.anomaly_type == "duplicate"


def test_conflicting_evidence_is_recorded_and_only_reduces_shadow_confidence():
    wrong = from_diagnostic(
        user_id="u_10001",
        result={"kpId": "ml", "correct": False},
        source_id="diagnostic:d-conflict:q-1",
        occurred_at=BASE,
    )
    high_quiz = from_quiz_result(
        QuizAttempt(
            id=503,
            user_id="u_10001",
            kp_id="ml",
            score=90,
            correct_count=9,
            total=10,
            passed=True,
            created_at=BASE + timedelta(hours=1),
        )
    )

    assert capture_shadow_event(wrong) == "captured"
    assert capture_shadow_event(high_quiz) == "captured_with_conflict"

    mastery, confidence = _advance_values(0.5, 0.0, None, "diagnostic", 0.32, BASE)
    mastery, confidence = _advance_values(
        mastery,
        confidence,
        BASE,
        "quiz",
        0.90,
        BASE + timedelta(hours=1),
    )
    with SessionLocal() as db:
        state = db.query(UserKnowledgeStateRecord).one()
        anomaly = db.query(LearningEventAnomalyRecord).one()
        assert state.mastery_score == pytest.approx(mastery)
        assert state.confidence == pytest.approx(
            confidence * CONFLICT_CONFIDENCE_FACTOR
        )
        assert anomaly.anomaly_type == "evidence_conflict"
