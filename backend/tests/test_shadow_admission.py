from __future__ import annotations

import json
import threading
import time

import pytest
from pydantic import ValidationError

from app.rag.shadow_admission import (
    ShadowDeadlineExecutor,
    ShadowEvaluationDataset,
    ShadowTaskStatus,
)


def _dataset_payload() -> dict:
    return {
        "environment": "canary-review-fixture",
        "evaluation_window": "fixture-v1",
        "performance_verified": True,
        "samples": [
            {
                "request_id": "req-1",
                "query_type": "concept_explanation",
                "latency_metrics": {"total_ms": 10, "rag_ms": 8, "tool_ms": 9},
                "quality_metrics": {
                    "evidence_overlap": 0.8,
                    "source_coverage": 0.9,
                    "confidence": 0.85,
                },
                "reliability_metrics": {
                    "timed_out": False,
                    "error_type": None,
                    "timeout_reason": None,
                    "cancellation_requested": False,
                    "worker_isolated": False,
                    "legacy_preserved": True,
                },
                "gate_features": {
                    "quality_metrics_complete": True,
                    "latency_metrics_complete": True,
                    "reliability_metrics_complete": True,
                    "target_environment_sample": True,
                },
            }
        ],
    }


def test_shadow_task_completes_before_deadline():
    result = ShadowDeadlineExecutor().run(lambda cancelled: not cancelled.is_set(), deadline_ms=50)

    assert result.status is ShadowTaskStatus.COMPLETED
    assert result.result is True
    assert result.timeout_reason is None


def test_shadow_task_deadline_requests_cancellation_and_returns_immediately():
    observed = threading.Event()

    def cooperative(cancelled: threading.Event):
        cancelled.wait()
        observed.set()

    result = ShadowDeadlineExecutor().run(cooperative, deadline_ms=10)

    assert result.status is ShadowTaskStatus.DEADLINE_EXCEEDED
    assert result.timeout_reason == "shadow.deadline_exceeded"
    assert result.cancellation_requested and result.worker_isolated
    assert observed.wait(0.1)


def test_stuck_worker_is_daemon_isolated_and_does_not_delay_legacy():
    release = threading.Event()
    executor = ShadowDeadlineExecutor(max_isolated_workers=1)
    legacy = "legacy-authoritative"
    started = time.perf_counter()
    result = executor.run(lambda _cancelled: release.wait(), deadline_ms=10)
    elapsed_ms = (time.perf_counter() - started) * 1_000

    assert legacy == "legacy-authoritative"
    assert result.status is ShadowTaskStatus.DEADLINE_EXCEEDED
    assert result.worker_isolated
    assert elapsed_ms < 100
    saturated = executor.run(lambda _cancelled: None, deadline_ms=10)
    assert saturated.status is ShadowTaskStatus.CAPACITY_EXHAUSTED
    assert saturated.timeout_reason == "shadow.isolation_capacity_exhausted"
    release.set()


def test_dataset_aggregates_content_free_quality_latency_and_reliability():
    dataset = ShadowEvaluationDataset.model_validate(_dataset_payload())
    report = dataset.aggregate()

    assert report.sample_count == 1
    assert report.query_type_counts == {"concept_explanation": 1}
    assert report.p95_latency_ms == 10
    assert report.evidence_overlap_mean == 0.8
    assert report.timeout_rate == report.error_rate == 0.0


def test_dataset_rejects_query_user_and_knowledge_content():
    for forbidden_field in ("query", "user_identity", "knowledge_content"):
        payload = _dataset_payload()
        payload["samples"][0][forbidden_field] = "sensitive"
        with pytest.raises(ValidationError):
            ShadowEvaluationDataset.model_validate(payload)
    serialized = json.dumps(_dataset_payload(), ensure_ascii=False)
    assert "query正文" not in serialized


def test_dataset_stratified_coverage_accepts_twenty_per_required_type():
    query_types = (
        "concept_explanation",
        "method_comparison",
        "operation_steps",
        "programming_practice",
        "comprehensive_question",
    )
    template = _dataset_payload()["samples"][0]
    payload = _dataset_payload()
    payload["samples"] = []
    for query_type in query_types:
        for index in range(20):
            sample = json.loads(json.dumps(template))
            sample["request_id"] = f"{query_type}-{index}"
            sample["query_type"] = query_type
            payload["samples"].append(sample)

    dataset = ShadowEvaluationDataset.model_validate(payload)
    integrity = dataset.check_integrity()

    assert integrity.valid
    assert integrity.sample_count == 100
    assert integrity.query_type_counts == {name: 20 for name in sorted(query_types)}


def test_missing_gate_data_blocks_dataset_validation():
    for field in (
        "latency_metrics",
        "quality_metrics",
        "reliability_metrics",
        "gate_features",
    ):
        payload = _dataset_payload()
        del payload["samples"][0][field]
        with pytest.raises(ValidationError):
            ShadowEvaluationDataset.model_validate(payload)

    nested = _dataset_payload()
    del nested["samples"][0]["reliability_metrics"]["error_type"]
    with pytest.raises(ValidationError):
        ShadowEvaluationDataset.model_validate(nested)


def test_duplicate_request_and_out_of_range_metric_are_rejected():
    duplicate = _dataset_payload()
    duplicate["samples"].append(json.loads(json.dumps(duplicate["samples"][0])))
    with pytest.raises(ValidationError, match="request_id must be unique"):
        ShadowEvaluationDataset.model_validate(duplicate)

    invalid_range = _dataset_payload()
    invalid_range["samples"][0]["quality_metrics"]["confidence"] = 1.01
    with pytest.raises(ValidationError):
        ShadowEvaluationDataset.model_validate(invalid_range)
