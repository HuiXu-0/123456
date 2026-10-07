"""TASK-005-C Evidence Adapter、幂等、回放与 Shadow 测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal
from app.models.entities import (
    LearningEventRecord,
    LearningStepProgress,
    Mastery,
    QuizAttempt,
    UserKnowledgeStateRecord,
)
from app.services.knowledge_state import (
    ALGORITHM_VERSION,
    DuplicateEventConflict,
    KnowledgeStateService,
    replay_learning_events,
)
from app.services.knowledge_state_shadow import evaluate_shadow
from app.services.learning_evidence import (
    EvidenceAdapterError,
    from_diagnostic,
    from_feynman,
    from_learning_step,
    from_quiz_result,
)

BASE = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_shadow_tables():
    with SessionLocal() as db:
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()


def _history():
    return [
        from_diagnostic(
            user_id="u_10001",
            result={"kpId": "ml", "correct": False},
            source_id="diagnostic:session-1:q1",
            occurred_at=BASE,
        ),
        from_feynman(
            user_id="u_10001",
            knowledge_id="ml",
            result={"score": 82},
            source_id="feynman:session-1:turn-1",
            occurred_at=BASE + timedelta(hours=1),
        ),
        from_quiz_result(
            QuizAttempt(
                id=42,
                user_id="u_10001",
                kp_id="ml",
                score=76,
                correct_count=7,
                total=10,
                passed=True,
                created_at=BASE + timedelta(hours=2),
            )
        ),
    ]


def test_adapter_converts_all_supported_evidence_sources():
    quiz, diagnostic, feynman = _history()[2], _history()[0], _history()[1]
    step = from_learning_step(
        LearningStepProgress(
            id=7,
            user_id="u_10001",
            kp_id="ml",
            step="lecture",
            done=True,
            updated_at=BASE + timedelta(hours=3),
        )
    )

    assert (quiz.event_type.value, quiz.source_type.value, quiz.score) == (
        "quiz", "quiz_result", 0.76
    )
    assert (diagnostic.event_type.value, diagnostic.score) == ("diagnostic", 0.32)
    assert (feynman.event_type.value, feynman.score) == ("feynman", 0.82)
    assert (step.event_type.value, step.score) == ("learning_step", 1.0)
    assert len({quiz.event_id, diagnostic.event_id, feynman.event_id, step.event_id}) == 4
    assert all(item.algorithm_version == ALGORITHM_VERSION for item in [quiz, diagnostic, feynman, step])
    with pytest.raises(EvidenceAdapterError):
        from_learning_step(
            LearningStepProgress(
                id=8, user_id="u_10001", kp_id="ml", step="video", done=False,
                updated_at=BASE,
            )
        )


def test_event_processing_is_idempotent_and_rejects_conflicting_payload():
    event = _history()[0]
    with SessionLocal() as db:
        service = KnowledgeStateService(db)
        first = service.update_state(event)
        second = service.update_state(event)
        assert first == second
        assert db.query(LearningEventRecord).count() == 1

        conflicting = event.model_copy(update={"score": 0.99})
        with pytest.raises(DuplicateEventConflict):
            service.update_state(conflicting)


def test_replay_is_consistent_for_out_of_order_and_duplicate_delivery():
    history = _history()
    chronological = replay_learning_events(history)
    replayed = replay_learning_events([history[2], history[0], history[1], history[0]])

    assert replayed.mastery_score == pytest.approx(chronological.mastery_score)
    assert replayed.confidence == pytest.approx(chronological.confidence)
    assert replayed.last_updated == chronological.last_updated


def test_shadow_reports_mastery_confidence_and_distribution_without_mastery_write():
    history = _history()
    with SessionLocal() as db:
        before = db.query(Mastery).filter_by(user_id="u_10001", kp_id="ml").one_or_none()
        before_values = (
            (before.score, before.confidence, before.score_source, before.status)
            if before else None
        )

    report = evaluate_shadow(history)
    node = report["nodes"][0]
    assert report["mode"] == "shadow"
    assert report["eventCount"] == 3
    assert report["comparableNodeCount"] == 1
    assert node["legacyMastery"] == pytest.approx(0.76)
    assert node["legacyConfidence"] == pytest.approx(0.85)
    assert node["masteryDifference"] != pytest.approx(0.0)
    assert node["confidenceDifference"] != pytest.approx(0.0)
    assert sum(v["legacy"] for v in report["distributionChange"].values()) == 1
    assert sum(v["knowledgeState"] for v in report["distributionChange"].values()) == 1

    with SessionLocal() as db:
        after = db.query(Mastery).filter_by(user_id="u_10001", kp_id="ml").one_or_none()
        after_values = (
            (after.score, after.confidence, after.score_source, after.status)
            if after else None
        )
    assert after_values == before_values
