"""TASK-005-B 知识状态查询、确定性更新与历史服务。

该服务是独立领域层，不接入 API、Agent、Workflow、RAG，也不改写对外 Mastery。
迟到事件会按时间顺序重放该用户/节点的完整事件序列，结果可确定性复现。
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import (
    KnowledgePoint,
    LearningEventRecord,
    User,
    UserKnowledgeStateRecord,
)
from app.schemas.knowledge_state import LearningEvent, UserKnowledgeState

ALGORITHM_VERSION = "ks-logodds-v1.1-shadow"
NEUTRAL_MASTERY = 0.5
LEARNING_RATE = 2.0
CONFIDENCE_RATE = 1.0
MASTERY_DECAY_DAYS = 30.0
CONFIDENCE_DECAY_DAYS = 60.0
EPSILON = 1e-6
CONFLICT_WINDOW_HOURS = 24.0
CONFLICT_SCORE_GAP = 0.40
CONFLICT_CONFIDENCE_FACTOR = 0.75

EVENT_WEIGHTS = {
    "quiz": 1.00,
    "practice": 0.90,
    "feynman": 0.70,
    "diagnostic": 0.55,
    "retrieval": 0.50,
    "learning_step": 0.15,
    "self_report": 0.10,
}


class KnowledgeStateError(ValueError):
    """知识状态服务领域错误基类。"""


class UnknownUser(KnowledgeStateError):
    pass


class UnknownKnowledgeNode(KnowledgeStateError):
    pass


class DuplicateEventConflict(KnowledgeStateError):
    """同一 event_id 携带了不同事件内容。"""


class KnowledgeStateServiceProtocol(Protocol):
    """供 TASK-005-C 及后续调用方依赖的最小协议；本阶段不接入调用方。"""

    def get_state(self, user_id: str, knowledge_id: str) -> UserKnowledgeState | None: ...

    def update_state(self, event: LearningEvent) -> UserKnowledgeState: ...

    def get_history(
        self, user_id: str, knowledge_id: str | None = None
    ) -> list[LearningEvent]: ...


def _utc_naive(value: datetime) -> datetime:
    """SQLite 统一存 UTC naive；领域协议输入仍强制携带时区。"""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _advance_values(
    mastery: float,
    confidence: float,
    previous_at: datetime | None,
    event_type: str,
    score: float,
    timestamp: datetime,
    *,
    event_weight: float | None = None,
) -> tuple[float, float]:
    if previous_at is not None:
        elapsed_days = max(0.0, (timestamp - previous_at).total_seconds() / 86400.0)
        mastery = NEUTRAL_MASTERY + (mastery - NEUTRAL_MASTERY) * math.exp(
            -elapsed_days / MASTERY_DECAY_DAYS
        )
        confidence *= math.exp(-elapsed_days / CONFIDENCE_DECAY_DAYS)

    weight = EVENT_WEIGHTS[event_type] if event_weight is None else event_weight
    if weight < 0:
        raise KnowledgeStateError("event_weight 不能为负数")
    bounded = min(1.0 - EPSILON, max(EPSILON, mastery))
    log_odds = math.log(bounded / (1.0 - bounded))
    mastery = _sigmoid(log_odds + LEARNING_RATE * weight * (2.0 * score - 1.0))
    confidence = 1.0 - (1.0 - confidence) * math.exp(-CONFIDENCE_RATE * weight)
    return mastery, confidence


def is_conflicting_evidence(
    previous_score: float,
    previous_at: datetime,
    score: float,
    timestamp: datetime,
    *,
    window_hours: float = CONFLICT_WINDOW_HOURS,
    score_gap: float = CONFLICT_SCORE_GAP,
) -> bool:
    """识别短窗口内方向相反且差异显著的证据，仅用于 Shadow 降置信。"""
    elapsed_hours = abs((timestamp - previous_at).total_seconds()) / 3600.0
    opposite = (previous_score < 0.5 <= score) or (score < 0.5 <= previous_score)
    return (
        elapsed_hours <= window_hours
        and opposite
        and abs(score - previous_score) >= score_gap
    )


def replay_learning_events(events: Iterable[LearningEvent]) -> UserKnowledgeState:
    """按 (timestamp, event_id) 重放单用户单节点历史，供离线工具与 Shadow 共用。"""
    unique: dict[str, LearningEvent] = {}
    for item in events:
        previous = unique.get(item.event_id)
        if previous is not None and previous != item:
            raise DuplicateEventConflict(item.event_id)
        unique[item.event_id] = item
    ordered = sorted(unique.values(), key=lambda item: (item.timestamp, item.event_id))
    if not ordered:
        raise KnowledgeStateError("LearningEvent 历史不能为空")
    owner = (ordered[0].user_id, ordered[0].knowledge_id)
    if any((item.user_id, item.knowledge_id) != owner for item in ordered):
        raise KnowledgeStateError("一次重放只能包含同一用户、同一知识节点")
    mastery = NEUTRAL_MASTERY
    confidence = 0.0
    previous_at: datetime | None = None
    prior_evidence: list[tuple[float, datetime]] = []
    for item in ordered:
        current_at = _utc_naive(item.timestamp)
        mastery, confidence = _advance_values(
            mastery,
            confidence,
            previous_at,
            item.event_type.value,
            item.score,
            current_at,
        )
        if any(
            is_conflicting_evidence(score, occurred_at, item.score, current_at)
            for score, occurred_at in prior_evidence
        ):
            confidence *= CONFLICT_CONFIDENCE_FACTOR
        previous_at = current_at
        prior_evidence.append((item.score, current_at))
    assert previous_at is not None
    return UserKnowledgeState(
        user_id=owner[0],
        knowledge_id=owner[1],
        mastery_score=mastery,
        confidence=confidence,
        last_updated=_utc_aware(previous_at),
    )


class KnowledgeStateService:
    """基于 SQLAlchemy Session 的知识状态服务协议实现。"""

    def __init__(self, db: Session):
        self.db = db

    def get_state(self, user_id: str, knowledge_id: str) -> UserKnowledgeState | None:
        row = self.db.scalar(
            select(UserKnowledgeStateRecord).where(
                UserKnowledgeStateRecord.user_id == user_id,
                UserKnowledgeStateRecord.knowledge_id == knowledge_id,
            )
        )
        return self._state_model(row) if row is not None else None

    def update_state(self, event: LearningEvent) -> UserKnowledgeState:
        self._validate_references(event.user_id, event.knowledge_id)
        existing = self.db.scalar(
            select(LearningEventRecord).where(
                LearningEventRecord.event_id == event.event_id
            )
        )
        if existing is not None:
            self._assert_same_event(existing, event)
            state = self.get_state(existing.user_id, existing.knowledge_id)
            if state is None:
                state = self._rebuild_state(existing.user_id, existing.knowledge_id)
                self.db.commit()
            return state
        event_row = LearningEventRecord(
            event_id=event.event_id,
            user_id=event.user_id,
            knowledge_id=event.knowledge_id,
            event_type=event.event_type.value,
            source_type=event.source_type.value,
            source_id=event.source_id,
            algorithm_version=event.algorithm_version,
            score=event.score,
            timestamp=_utc_naive(event.timestamp),
        )
        self.db.add(event_row)
        try:
            self.db.flush()
        except IntegrityError:
            # 并发重复投递可能在“先查后写”窗口命中唯一索引；回滚本次插入后按
            # 已存在事件处理，保证幂等而不是把正常重试暴露为数据库错误。
            self.db.rollback()
            existing = self.db.scalar(
                select(LearningEventRecord).where(
                    LearningEventRecord.event_id == event.event_id
                )
            )
            if existing is None:
                raise
            self._assert_same_event(existing, event)
            state = self.get_state(existing.user_id, existing.knowledge_id)
            if state is None:
                state = self._rebuild_state(existing.user_id, existing.knowledge_id)
                self.db.commit()
            return state

        self._rebuild_state(event.user_id, event.knowledge_id)
        self.db.commit()
        row = self.db.scalar(
            select(UserKnowledgeStateRecord).where(
                UserKnowledgeStateRecord.user_id == event.user_id,
                UserKnowledgeStateRecord.knowledge_id == event.knowledge_id,
            )
        )
        assert row is not None
        self.db.refresh(row)
        return self._state_model(row)

    def _rebuild_state(self, user_id: str, knowledge_id: str) -> UserKnowledgeState:
        history = self._history_rows(user_id, knowledge_id)
        mastery = NEUTRAL_MASTERY
        confidence = 0.0
        previous_at: datetime | None = None
        prior_evidence: list[tuple[float, datetime]] = []
        for item in history:
            mastery, confidence = _advance_values(
                mastery, confidence, previous_at, item.event_type, item.score, item.timestamp
            )
            if any(
                is_conflicting_evidence(score, occurred_at, item.score, item.timestamp)
                for score, occurred_at in prior_evidence
            ):
                confidence *= CONFLICT_CONFIDENCE_FACTOR
            previous_at = item.timestamp
            prior_evidence.append((item.score, item.timestamp))

        state_row = self.db.scalar(
            select(UserKnowledgeStateRecord).where(
                UserKnowledgeStateRecord.user_id == user_id,
                UserKnowledgeStateRecord.knowledge_id == knowledge_id,
            )
        )
        assert previous_at is not None
        if state_row is None:
            state_row = UserKnowledgeStateRecord(
                user_id=user_id,
                knowledge_id=knowledge_id,
                mastery_score=mastery,
                confidence=confidence,
                last_updated=previous_at,
            )
            self.db.add(state_row)
        else:
            state_row.mastery_score = mastery
            state_row.confidence = confidence
            state_row.last_updated = previous_at
        self.db.flush()
        return self._state_model(state_row)

    def get_history(
        self, user_id: str, knowledge_id: str | None = None
    ) -> list[LearningEvent]:
        query = select(LearningEventRecord).where(LearningEventRecord.user_id == user_id)
        if knowledge_id is not None:
            query = query.where(LearningEventRecord.knowledge_id == knowledge_id)
        rows = self.db.scalars(
            query.order_by(LearningEventRecord.timestamp, LearningEventRecord.event_id)
        ).all()
        return [
            LearningEvent(
                event_id=row.event_id,
                user_id=row.user_id,
                knowledge_id=row.knowledge_id,
                event_type=row.event_type,
                source_type=row.source_type,
                source_id=row.source_id,
                algorithm_version=row.algorithm_version,
                score=row.score,
                timestamp=_utc_aware(row.timestamp),
            )
            for row in rows
        ]

    def _history_rows(self, user_id: str, knowledge_id: str) -> list[LearningEventRecord]:
        return list(
            self.db.scalars(
                select(LearningEventRecord)
                .where(
                    LearningEventRecord.user_id == user_id,
                    LearningEventRecord.knowledge_id == knowledge_id,
                )
                .order_by(LearningEventRecord.timestamp, LearningEventRecord.event_id)
            ).all()
        )

    @staticmethod
    def _assert_same_event(row: LearningEventRecord, event: LearningEvent) -> None:
        stored = (
            row.user_id,
            row.knowledge_id,
            row.event_type,
            row.source_type,
            row.source_id,
            row.algorithm_version,
            row.score,
            _utc_aware(row.timestamp),
        )
        incoming = (
            event.user_id,
            event.knowledge_id,
            event.event_type.value,
            event.source_type.value,
            event.source_id,
            event.algorithm_version,
            event.score,
            event.timestamp,
        )
        if stored != incoming:
            raise DuplicateEventConflict(event.event_id)

    def _validate_references(self, user_id: str, knowledge_id: str) -> None:
        if self.db.get(User, user_id) is None:
            raise UnknownUser(user_id)
        if self.db.get(KnowledgePoint, knowledge_id) is None:
            raise UnknownKnowledgeNode(knowledge_id)

    @staticmethod
    def _state_model(row: UserKnowledgeStateRecord) -> UserKnowledgeState:
        return UserKnowledgeState(
            user_id=row.user_id,
            knowledge_id=row.knowledge_id,
            mastery_score=row.mastery_score,
            confidence=row.confidence,
            last_updated=_utc_aware(row.last_updated),
        )
