"""TASK-004-E1 Trusted RAG canary Gate tests."""
from __future__ import annotations

from app.rag.trusted_rag_gate import (
    CanaryDecisionValue,
    FaultInjectionResults,
    FaultScenarioResult,
    RerankMetrics,
    ShadowMetrics,
    TrustedRAGGate,
)
from app.rag.shadow_admission import ShadowEvaluationDataset
from app.rag.evidence_quality_evaluation import EvidenceEvaluationRecord, QualityEvaluationResult


def _quality(**overrides) -> QualityEvaluationResult:
    values = {
        "evaluation_window": "fixture", "request_count": 100,
        "evidence_record_count": 100, "evidence_overlap": 0.75,
        "source_coverage": 0.85, "human_relevance": 0.9,
        "support_rate": 0.9, "completeness": 0.8, "source_quality": 0.9,
        "review_complete": True,
        "evaluated_request_ids": [f"req-{index}" for index in range(100)],
        "records": [EvidenceEvaluationRecord(
            request_id=f"req-{index}", evidence_rank=1, relevance_score=3,
            completeness_score=3, supports_answer=True, source_quality=3,
        ) for index in range(100)],
    }
    values.update(overrides)
    return QualityEvaluationResult(**values)


def _quality_for_dataset(dataset: ShadowEvaluationDataset) -> QualityEvaluationResult:
    request_ids = [sample.request_id for sample in dataset.samples]
    aggregate = dataset.aggregate()
    return QualityEvaluationResult(
        evaluation_window="fixture", request_count=len(request_ids),
        evidence_record_count=len(request_ids),
        evidence_overlap=aggregate.evidence_overlap_mean or 0.0,
        source_coverage=aggregate.source_coverage_mean or 0.0,
        human_relevance=0.9, support_rate=0.9, completeness=0.8,
        source_quality=0.9, review_complete=True,
        evaluated_request_ids=request_ids,
        records=[EvidenceEvaluationRecord(
            request_id=request_id, evidence_rank=1, relevance_score=3,
            completeness_score=3, supports_answer=True, source_quality=3,
        ) for request_id in request_ids],
    )


def _rerank(**overrides) -> RerankMetrics:
    values = {
        "independent_validation": True,
        "metrics_provisional": False,
        "human_review_complete": True,
        "mrr_delta": 0.03,
        "ndcg_at_3_delta": 0.02,
        "ndcg_at_5_delta": 0.01,
        "degraded_case_count": 0,
    }
    values.update(overrides)
    return RerankMetrics(**values)


def _shadow(**overrides) -> ShadowMetrics:
    values = {
        "sample_count": 100,
        "evidence_overlap_mean": 0.75,
        "source_coverage_mean": 0.85,
        "confidence_mean": 0.90,
        "p95_latency_ms": 1200.0,
        "timeout_rate": 0.01,
        "error_rate": 0.02,
        "performance_verified": True,
        "rerank": _rerank(),
    }
    values.update(overrides)
    return ShadowMetrics(**values)


def _faults(*, blocked: bool = False) -> FaultInjectionResults:
    scenario = FaultScenarioResult(
        scenario_id="hard_hang" if blocked else "timeout_exception",
        status="BLOCK" if blocked else "PASS",
        legacy_preserved=True,
        trusted_isolated=not blocked,
        rollback_path="legacy",
        content_safe=True,
        structured_reason=(
            "timeout.hard_hang_no_hard_deadline"
            if blocked
            else "retriever.timeout_exception"
        ),
    )
    return FaultInjectionResults(
        scenario_count=1,
        pass_count=0 if blocked else 1,
        block_count=1 if blocked else 0,
        scenarios=[scenario],
    )


def test_all_gate_checks_pass():
    result = TrustedRAGGate().evaluate(_shadow(), _faults(), quality_evaluation=_quality())

    assert result.quality_pass
    assert result.latency_pass
    assert result.reliability_pass
    assert result.rerank_pass
    assert result.final_decision is CanaryDecisionValue.PASS
    assert result.block_reasons == []
    assert result.remediation == []
    assert result.rollback_recommended is False


def test_single_metric_failure_blocks_p95():
    result = TrustedRAGGate().evaluate(_shadow(p95_latency_ms=1500.01), _faults(), quality_evaluation=_quality())

    assert result.quality_pass and result.reliability_pass and result.rerank_pass
    assert result.latency_pass is False
    assert result.block_reasons == ["latency.p95_exceeded"]


def test_multiple_metric_failures_are_all_reported():
    result = TrustedRAGGate().evaluate(
        _shadow(
            evidence_overlap_mean=0.60,
            p95_latency_ms=1600.0,
            timeout_rate=0.02,
            error_rate=0.03,
            rerank=_rerank(degraded_case_count=1),
        ),
        _faults(blocked=True),
        quality_evaluation=_quality(completeness=0.29),
    )

    assert not result.quality_pass
    assert not result.latency_pass
    assert not result.reliability_pass
    assert not result.rerank_pass
    assert "quality.completeness_below_threshold" in result.block_reasons
    assert "reliability.fault_failure_rate_exceeded" in result.block_reasons
    assert "reliability.fault_blocked:timeout.hard_hang_no_hard_deadline" in result.block_reasons
    assert "rerank.degraded_cases_exceeded" in result.block_reasons


def test_missing_inputs_fail_closed():
    result = TrustedRAGGate().evaluate(None, None)

    assert result.final_decision is CanaryDecisionValue.BLOCK
    assert result.block_reasons == [
        "quality.shadow_metrics_missing",
        "latency.shadow_metrics_missing",
        "reliability.shadow_metrics_missing",
        "reliability.fault_results_missing",
        "rerank.metrics_missing",
    ]


def test_block_decision_recommends_automatic_rollback():
    result = TrustedRAGGate().evaluate(_shadow(), _faults(blocked=True), quality_evaluation=_quality())

    assert result.final_decision is CanaryDecisionValue.BLOCK
    assert result.rollback_recommended is True
    assert result.recommended_action == "keep_legacy_and_set_canary_weight_to_zero"


def test_task004_reports_are_supported_without_trusting_provisional_rerank():
    rerank = RerankMetrics.from_blind_evaluation(
        {
            "evaluationType": "offline_independent_blind_candidate_ranking",
            "metricsProvisional": True,
            "delta": {"mrr": 0.4, "ndcg@3": 0.5, "ndcg@5": 0.3},
            "degradedCaseCount": 2,
            "humanPreference": {"status": "pending_independent_human_review"},
            "relevanceReview": {"status": "pending_independent_human_review"},
        }
    )

    result = TrustedRAGGate().evaluate(_shadow(rerank=rerank), _faults(), quality_evaluation=_quality())

    assert result.rerank_pass is False
    assert set(result.block_reasons) >= {
        "rerank.metrics_still_provisional",
        "rerank.human_review_incomplete",
        "rerank.degraded_cases_exceeded",
    }


def test_gate_accepts_complete_shadow_evaluation_dataset():
    query_types = (
        "concept_explanation", "method_comparison", "operation_steps",
        "programming_practice", "comprehensive_question",
    )
    samples = []
    for query_type in query_types:
        for index in range(20):
            samples.append({
                "request_id": f"{query_type}-{index}",
                "query_type": query_type,
                "latency_metrics": {
                    "total_ms": 100 + index,
                    "rag_ms": 90 + index,
                    "tool_ms": 0,
                },
                "quality_metrics": {
                    "evidence_overlap": 0.75,
                    "source_coverage": 0.85,
                    "confidence": 0.9,
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
            })
    dataset = ShadowEvaluationDataset(
        environment="target-canary-review",
        evaluation_window="frozen-window-v1",
        performance_verified=True,
        samples=samples,
    )

    result = TrustedRAGGate().evaluate(
        dataset, _faults(), rerank_metrics=_rerank(),
        quality_evaluation=_quality_for_dataset(dataset),
    )

    assert result.final_decision is CanaryDecisionValue.PASS
    assert result.snapshot.shadow_input_type == "dataset"
    assert result.snapshot.query_type_counts == {name: 20 for name in sorted(query_types)}


def test_dataset_underrepresented_query_type_blocks_with_remediation():
    dataset = ShadowEvaluationDataset(
        environment="fixture",
        evaluation_window="fixture",
        samples=[],
    )
    result = TrustedRAGGate().evaluate(dataset, _faults(), rerank_metrics=_rerank(), quality_evaluation=_quality())

    assert result.final_decision is CanaryDecisionValue.BLOCK
    assert "complete_stratified_shadow_dataset" in result.remediation


def test_human_quality_result_is_required_and_gate_recalculates_it():
    missing = TrustedRAGGate().evaluate(_shadow(), _faults())
    assert "quality.human_evaluation_missing" in missing.block_reasons

    low = TrustedRAGGate().evaluate(
        _shadow(), _faults(),
        quality_evaluation=_quality(human_relevance=0.66, support_rate=0.79),
    )
    assert set(low.block_reasons) >= {
        "quality.relevance_below_threshold",
        "quality.support_rate_below_threshold",
    }
    assert low.snapshot.quality_evaluation_request_count == 100


def test_quality_gate_v2_uses_four_metrics_and_overlap_is_diagnostic():
    result = TrustedRAGGate().evaluate(
        _shadow(evidence_overlap_mean=0.0, confidence_mean=0.0),
        _faults(),
        quality_evaluation=_quality(
            human_relevance=0.67,
            support_rate=0.80,
            completeness=0.30,
            source_coverage=0.80,
        ),
    )

    assert result.quality_pass is True
    assert result.snapshot.quality_gate_version == "quality-gate-v2"
    assert result.snapshot.evidence_overlap_diagnostic == 0.0
    assert result.snapshot.confidence_diagnostic == 0.0


def test_explicitly_disabled_rerank_policy_passes_only_with_hybrid_fallback():
    disabled = _rerank(
        policy_enabled=False,
        fallback="hybrid",
        reason="independent_validation_regression_policy_disabled",
        metrics_provisional=False,
        human_review_complete=False,
        mrr_delta=0.0,
        ndcg_at_3_delta=0.0,
        ndcg_at_5_delta=0.0,
        degraded_case_count=0,
    )
    result = TrustedRAGGate().evaluate(
        _shadow(rerank=disabled), _faults(), quality_evaluation=_quality()
    )
    assert result.rerank_pass is True

    unsafe = disabled.model_copy(update={"fallback": "empty"})
    blocked = TrustedRAGGate().evaluate(
        _shadow(rerank=unsafe), _faults(), quality_evaluation=_quality()
    )
    assert "rerank.disabled_fallback_must_be_hybrid" in blocked.block_reasons
