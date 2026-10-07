"""可信检索离线评测协议（TASK-003-C1）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedEvidence(_StrictModel):
    document_id: str | None = None
    chunk_id: str | None = None
    relevance: int = Field(default=1, ge=1, le=3)
    expected_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_identifier(self):
        if not self.document_id and not self.chunk_id:
            raise ValueError("ExpectedEvidence至少需要document_id或chunk_id")
        return self


class RetrievalEvaluationCase(_StrictModel):
    case_id: str
    query: str
    query_type: str
    expected_document_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_keywords: list[str] = Field(default_factory=list)
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)
    knowledge_scope: str | list[str] | dict[str, Any] | None = None
    relevance: int = Field(default=1, ge=0, le=3)
    notes: str = ""
    annotator: str
    annotation_status: Literal[
        "single_reviewed", "pending_second_review", "double_reviewed"
    ]
    evidence_basis: str
    difficulty: Literal["easy", "medium", "hard"]
    second_annotator: str | None = None

    @model_validator(mode="after")
    def _validate_expectation(self):
        if not self.case_id.strip() or not self.query.strip() or not self.query_type.strip():
            raise ValueError("case_id、query和query_type不能为空")
        has_expected = bool(
            self.expected_document_ids
            or self.expected_chunk_ids
            or self.expected_evidence
        )
        if self.relevance == 0 and has_expected:
            raise ValueError("无答案用例不能声明相关Evidence")
        if self.relevance > 0 and not has_expected:
            raise ValueError("有答案用例必须声明相关Evidence")
        if not self.annotator.strip() or not self.evidence_basis.strip():
            raise ValueError("annotator和evidence_basis不能为空")
        if self.annotation_status == "double_reviewed" and not self.second_annotator:
            raise ValueError("double_reviewed必须声明second_annotator")
        return self


class RetrievalEvaluationResult(_StrictModel):
    case_id: str
    system: str
    retrieved_document_ids: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    latency_ms: float = Field(ge=0.0)
    empty_result: bool
    source_completeness: float = Field(ge=0.0, le=1.0)
    source_coverage: float = Field(ge=0.0, le=1.0)


class RetrievalEvaluationReport(_StrictModel):
    dataset_name: str
    case_count: int = Field(ge=0)
    systems: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    results: list[RetrievalEvaluationResult] = Field(default_factory=list)
    shadow_differences: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
