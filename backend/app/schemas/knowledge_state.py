"""TASK-005-B 知识状态领域模型与服务输入输出协议。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LearningEventType(str, Enum):
    """当前状态引擎可接收的证据类型；枚举值与 TASK-005-A 权重表一致。"""

    QUIZ = "quiz"
    PRACTICE = "practice"
    FEYNMAN = "feynman"
    DIAGNOSTIC = "diagnostic"
    RETRIEVAL = "retrieval"
    LEARNING_STEP = "learning_step"
    SELF_REPORT = "self_report"


class LearningEventSourceType(str, Enum):
    """原始学习证据来源；与状态引擎的事件类型分开演进。"""

    QUIZ_RESULT = "quiz_result"
    DIAGNOSTIC = "diagnostic"
    FEYNMAN = "feynman"
    LEARNING_STEP = "learning_step"
    PRACTICE = "practice"
    RETRIEVAL = "retrieval"
    SELF_REPORT = "self_report"


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class KnowledgeNode(_DomainModel):
    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    difficulty: float = Field(ge=0.0, le=1.0)
    prerequisites: list[str] = Field(default_factory=list)

    @field_validator("prerequisites")
    @classmethod
    def unique_prerequisites(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("prerequisites 不能包含空知识节点 id")
        if len(value) != len(set(value)):
            raise ValueError("prerequisites 不能重复")
        return value

    @model_validator(mode="after")
    def cannot_require_itself(self) -> "KnowledgeNode":
        if self.id in self.prerequisites:
            raise ValueError("知识节点不能依赖自身")
        return self


class UserKnowledgeState(_DomainModel):
    user_id: str = Field(min_length=1, max_length=64)
    knowledge_id: str = Field(min_length=1, max_length=32)
    mastery_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    last_updated: datetime

    @field_validator("last_updated")
    @classmethod
    def last_updated_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("last_updated 必须包含时区")
        return value.astimezone(timezone.utc)


class LearningEvent(_DomainModel):
    event_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    knowledge_id: str = Field(min_length=1, max_length=32)
    event_type: LearningEventType
    source_type: LearningEventSourceType
    source_id: str = Field(min_length=1, max_length=128)
    algorithm_version: str = Field(min_length=1, max_length=32)
    score: float = Field(ge=0.0, le=1.0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp 必须包含时区")
        return value.astimezone(timezone.utc)
