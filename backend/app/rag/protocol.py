"""统一可信检索协议（TASK-003-B2）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RAGRequest(_StrictModel):
    query: str
    user_id: str
    knowledge_scope: str | list[str] | dict[str, Any] | None = None
    top_k: int = Field(default=5, ge=1, le=100)

    @field_validator("query", "user_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class QueryPlan(_StrictModel):
    intent: str
    keywords: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("intent")
    @classmethod
    def _intent_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("intent must not be blank")
        return normalized


class RetrievalCandidate(_StrictModel):
    """Dense、Keyword和Fusion阶段共享的统一候选协议。"""

    id: str
    content: str
    source: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    dense_score: float = 0.0
    keyword_score: float = 0.0
    normalized_dense_score: float = Field(default=0.0, ge=0.0, le=1.0)
    normalized_keyword_score: float = Field(default=0.0, ge=0.0, le=1.0)
    fusion_score: float = 0.0
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("id", "content")
    @classmethod
    def _candidate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate id/content must not be blank")
        return value


class EvidenceItem(_StrictModel):
    content: str
    source: dict[str, Any] = Field(default_factory=dict)
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class CalibrationProfile(_StrictModel):
    """按Embedding Profile隔离的离线Confidence校准契约。"""

    profile_id: str
    threshold: float = Field(ge=0.0, le=1.0)
    calibration_version: str

    @field_validator("profile_id", "calibration_version")
    @classmethod
    def _calibration_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calibration profile fields must not be blank")
        return normalized


class TrustReport(_StrictModel):
    """检索阶段Trust基础快照；不代表最终答案可信度。"""

    retrieval_confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class RAGResponse(_StrictModel):
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    trust_report: TrustReport | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
