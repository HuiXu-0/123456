"""TASK-005-B 知识状态模型与服务测试。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.entities import LearningEventRecord, UserKnowledgeStateRecord
from app.schemas.knowledge_state import KnowledgeNode, LearningEvent
from app.services.knowledge_state import (
    EVENT_WEIGHTS,
    KnowledgeStateService,
    UnknownKnowledgeNode,
    UnknownUser,
)


def _event(
    *,
    score: float,
    at: datetime,
    event_type: str = "quiz",
    knowledge_id: str = "ml",
) -> LearningEvent:
    source_type = "quiz_result" if event_type == "quiz" else event_type
    source_id = f"test:{knowledge_id}:{event_type}:{at.isoformat()}"
    return LearningEvent(
        event_id=f"le_{abs(hash(source_id))}",
        user_id="u_10001",
        knowledge_id=knowledge_id,
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        algorithm_version="ks-logodds-v1",
        score=score,
        timestamp=at,
    )


@pytest.fixture(autouse=True)
def _clean_knowledge_state_tables():
    """本模块逐例隔离新增的两张内部表，不影响既有业务数据。"""
    with SessionLocal() as db:
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(LearningEventRecord).delete()
        db.query(UserKnowledgeStateRecord).delete()
        db.commit()


def test_state_is_created_by_first_event():
    with SessionLocal() as db:
        service = KnowledgeStateService(db)
        at = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)

        state = service.update_state(_event(score=0.9, at=at))

        assert state.user_id == "u_10001"
        assert state.knowledge_id == "ml"
        assert state.mastery_score > 0.5
        assert state.confidence == pytest.approx(1 - math.exp(-1.0))
        assert state.last_updated == at
        assert service.get_state("u_10001", "ml") == state


def test_update_uses_event_weight_and_time_ordered_replay():
    with SessionLocal() as db:
        service = KnowledgeStateService(db)
        base = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
        service.update_state(_event(score=0.2, at=base + timedelta(days=1)))
        replayed = service.update_state(
            _event(score=1.0, at=base, event_type="diagnostic")
        )
        chronological_event = _event(
            score=1.0, at=base, event_type="diagnostic", knowledge_id="nn"
        )
        service.update_state(chronological_event)
        chronological = service.update_state(
            _event(
                score=0.2,
                at=base + timedelta(days=1),
                event_type="quiz",
                knowledge_id="nn",
            )
        )

        assert EVENT_WEIGHTS["quiz"] > EVENT_WEIGHTS["diagnostic"]
        assert replayed.last_updated == base + timedelta(days=1)
        assert replayed.mastery_score == pytest.approx(chronological.mastery_score)
        assert replayed.confidence == pytest.approx(chronological.confidence)
        assert 0.0 <= replayed.mastery_score <= 1.0
        assert 0.0 <= replayed.confidence <= 1.0


def test_history_is_append_only_ordered_and_can_span_nodes():
    with SessionLocal() as db:
        service = KnowledgeStateService(db)
        base = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
        service.update_state(_event(score=0.8, at=base + timedelta(hours=1)))
        service.update_state(_event(score=0.6, at=base, event_type="practice"))
        service.update_state(
            _event(
                score=1.0,
                at=base + timedelta(hours=2),
                event_type="learning_step",
                knowledge_id="nn",
            )
        )

        node_history = service.get_history("u_10001", "ml")
        all_history = service.get_history("u_10001")
        assert [item.timestamp for item in node_history] == [base, base + timedelta(hours=1)]
        assert len(all_history) == 3
        assert {item.knowledge_id for item in all_history} == {"ml", "nn"}


def test_domain_and_database_integrity_constraints():
    KnowledgeNode(
        id="ml",
        name="机器学习基础",
        description="基础概念",
        difficulty=0.3,
        prerequisites=[],
    )
    with pytest.raises(ValidationError):
        KnowledgeNode(
            id="cnn",
            name="CNN",
            description="",
            difficulty=1.1,
            prerequisites=["nn", "nn"],
        )
    with pytest.raises(ValidationError):
        KnowledgeNode(
            id="cnn",
            name="CNN",
            description="",
            difficulty=0.7,
            prerequisites=["cnn"],
        )
    with pytest.raises(ValidationError):
        _event(score=1.1, at=datetime.now(timezone.utc))
    with pytest.raises(ValidationError):
        _event(score=0.5, at=datetime(2026, 7, 23, 8, 0))

    with SessionLocal() as db:
        service = KnowledgeStateService(db)
        with pytest.raises(UnknownUser):
            service.update_state(
                _event(score=0.5, at=datetime.now(timezone.utc)).model_copy(
                    update={"user_id": "missing"}
                )
            )
        with pytest.raises(UnknownKnowledgeNode):
            service.update_state(
                _event(score=0.5, at=datetime.now(timezone.utc)).model_copy(
                    update={"knowledge_id": "missing"}
                )
            )

        db.add(
            UserKnowledgeStateRecord(
                user_id="u_10001",
                knowledge_id="ml",
                mastery_score=2.0,
                confidence=0.5,
                last_updated=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
