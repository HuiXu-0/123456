"""Privacy-safe Evidence quality evaluation protocol for Trusted RAG."""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceEvaluationRecord(_StrictModel):
    """A content-free human Evidence judgement.

    Scores use a four-point rubric: 0=none/unknown, 1=weak, 2=partial,
    3=complete/high.  The protocol deliberately has no free-text field.
    """

    request_id: str = Field(min_length=1, max_length=128)
    evidence_rank: int = Field(ge=1, le=100)
    relevance_score: int = Field(ge=0, le=3)
    completeness_score: int = Field(ge=0, le=3)
    supports_answer: bool
    source_quality: int = Field(ge=0, le=3)


class QualityEvaluationResult(_StrictModel):
    """Gate-ready aggregate plus the privacy-safe Evidence judgements."""

    schema_version: str = "trusted-rag-quality-evaluation-v1"
    evaluation_window: str = Field(min_length=1)
    request_count: int = Field(ge=1)
    evidence_record_count: int = Field(ge=0)
    evidence_overlap: float = Field(ge=0.0, le=1.0)
    source_coverage: float = Field(ge=0.0, le=1.0)
    human_relevance: float = Field(ge=0.0, le=1.0)
    support_rate: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    source_quality: float = Field(ge=0.0, le=1.0)
    review_complete: bool
    evaluated_request_ids: list[str]
    records: list[EvidenceEvaluationRecord]

    @model_validator(mode="after")
    def validate_integrity(self) -> "QualityEvaluationResult":
        if len(self.records) != self.evidence_record_count:
            raise ValueError("evidence_record_count does not match records")
        if len(self.evaluated_request_ids) != self.request_count:
            raise ValueError("request_count does not match evaluated_request_ids")
        if len(set(self.evaluated_request_ids)) != len(self.evaluated_request_ids):
            raise ValueError("evaluated_request_ids must be unique")
        grouped: dict[str, list[int]] = defaultdict(list)
        seen: set[tuple[str, int]] = set()
        for record in self.records:
            key = (record.request_id, record.evidence_rank)
            if key in seen:
                raise ValueError("request_id/evidence_rank must be unique")
            seen.add(key)
            grouped[record.request_id].append(record.evidence_rank)
        if not set(grouped).issubset(self.evaluated_request_ids):
            raise ValueError("record request_id was not evaluated")
        for request_id, ranks in grouped.items():
            if sorted(ranks) != list(range(1, len(ranks) + 1)):
                raise ValueError(f"evidence ranks must be contiguous: {request_id}")
        return self

    @classmethod
    def from_records(
        cls,
        *,
        evaluation_window: str,
        evidence_overlap: float,
        source_coverage: float,
        records: list[EvidenceEvaluationRecord],
        expected_request_ids: set[str],
    ) -> "QualityEvaluationResult":
        actual_request_ids = {record.request_id for record in records}
        extra = sorted(actual_request_ids - expected_request_ids)
        if extra:
            raise ValueError(f"quality request ids mismatch; extra={extra}")
        record_count = len(records)
        support_by_request = {
            request_id: any(
                record.supports_answer
                for record in records
                if record.request_id == request_id
            )
            for request_id in expected_request_ids
        }
        mean = lambda name: round(
            sum(getattr(record, name) for record in records) / (record_count * 3), 6
        ) if record_count else 0.0
        return cls(
            evaluation_window=evaluation_window,
            request_count=len(expected_request_ids),
            evidence_record_count=record_count,
            evidence_overlap=round(evidence_overlap, 6),
            source_coverage=round(source_coverage, 6),
            human_relevance=mean("relevance_score"),
            support_rate=round(
                sum(support_by_request.values()) / len(support_by_request), 6
            ),
            completeness=mean("completeness_score"),
            source_quality=mean("source_quality"),
            review_complete=True,
            evaluated_request_ids=sorted(expected_request_ids),
            records=records,
        )
