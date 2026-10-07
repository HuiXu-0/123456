from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.rag.evidence_quality_evaluation import (
    EvidenceEvaluationRecord,
    QualityEvaluationResult,
)


def _record(request_id: str = "req-1", rank: int = 1) -> EvidenceEvaluationRecord:
    return EvidenceEvaluationRecord(
        request_id=request_id,
        evidence_rank=rank,
        relevance_score=3,
        completeness_score=2,
        supports_answer=True,
        source_quality=3,
    )


def test_evaluation_schema_and_aggregate_are_complete():
    result = QualityEvaluationResult.from_records(
        evaluation_window="frozen-v1",
        evidence_overlap=0.7,
        source_coverage=0.8,
        records=[_record()],
        expected_request_ids={"req-1"},
    )
    assert result.request_count == result.evidence_record_count == 1
    assert result.human_relevance == result.support_rate == result.source_quality == 1.0
    assert result.completeness == 0.666667


def test_schema_rejects_privacy_and_free_text_fields():
    payload = _record().model_dump()
    for field in ("query", "knowledge_content", "user_identity", "content"):
        with pytest.raises(ValidationError):
            EvidenceEvaluationRecord.model_validate({**payload, field: "forbidden"})
    assert set(payload) == {
        "request_id", "evidence_rank", "relevance_score", "completeness_score",
        "supports_answer", "source_quality",
    }


def test_data_integrity_rejects_missing_duplicate_and_noncontiguous_records():
    result = QualityEvaluationResult.from_records(
        evaluation_window="v1", evidence_overlap=0.1, source_coverage=0.2,
        records=[_record()], expected_request_ids={"req-1", "req-2"},
    )
    assert result.request_count == 2
    assert result.support_rate == 0.5
    base = {
        "evaluation_window": "v1", "request_count": 1, "evidence_record_count": 2,
        "evidence_overlap": 0.1, "source_coverage": 0.2, "human_relevance": 1,
        "support_rate": 1, "completeness": 1, "source_quality": 1,
        "review_complete": True,
        "evaluated_request_ids": ["req-1"],
    }
    with pytest.raises(ValidationError, match="unique"):
        QualityEvaluationResult.model_validate({**base, "records": [_record(), _record()]})
    with pytest.raises(ValidationError, match="contiguous"):
        QualityEvaluationResult.model_validate({**base, "records": [_record(rank=1), _record(rank=3)]})


def test_serialized_protocol_contains_no_forbidden_privacy_keys():
    serialized = json.dumps(_record().model_dump())
    for forbidden in ("query", "knowledge_content", "user_identity", "content"):
        assert f'"{forbidden}"' not in serialized
