"""TASK-003-E4-C Trusted RAG offline migration gate tests."""
from __future__ import annotations

from app.agents.shadow_metrics import ShadowMetricObservation
from app.agents.trusted_rag_gate import (
    GateDecision,
    HistoricalShadowResults,
    OfflineTrustedRAGGateEvaluator,
    RecommendedPath,
    RerankAssessment,
    TrustedRAGGateConfig,
)


def _observation(index: int, **overrides) -> ShadowMetricObservation:
    values = {
        "traceId": f"trace-{index}",
        "agent": "resource",
        "tool": "trusted_rag",
        "total_latency": 110.0,
        "tool_latency": 110.0,
        "evidence_overlap": 0.75,
        "source_coverage": 0.85,
        "confidence": 0.90,
    }
    values.update(overrides)
    return ShadowMetricObservation(**values)


def _rerank(**overrides) -> RerankAssessment:
    values = {
        "independent_validation": True,
        "mrr_delta": 0.03,
        "ndcg_at_3_delta": 0.02,
        "ndcg_at_5_delta": 0.01,
        "degraded_case_count": 0,
    }
    values.update(overrides)
    return RerankAssessment(**values)


def test_all_gate_rules_pass_at_configured_boundaries():
    history = HistoricalShadowResults(
        observations=[_observation(index) for index in range(100)],
        legacy_p95_latency_ms=100.0,
        rerank=_rerank(),
    )

    result = OfflineTrustedRAGGateEvaluator().evaluate(history)

    assert result.quality_pass is True
    assert result.latency_pass is True
    assert result.reliability_pass is True
    assert result.rerank_pass is True
    assert result.final_decision is GateDecision.PASS
    assert result.pass_count == 4
    assert result.block_count == 0
    assert result.block_reasons == []
    assert result.metrics.p95_latency_ratio == 1.1
    assert result.recommended_path is RecommendedPath.TRUSTED_RAG_CANARY


def test_configurable_quality_performance_and_reliability_thresholds_block():
    observations = [_observation(index) for index in range(10)]
    observations[0] = _observation(
        0, tool_latency=160.0, timed_out=True, error_type="TimeoutError",
        evidence_overlap=None, source_coverage=None, confidence=None,
    )
    config = TrustedRAGGateConfig(
        evidence_overlap_threshold=0.80,
        source_coverage_threshold=0.90,
        confidence_threshold=0.95,
        p95_latency_ratio=1.20,
        timeout_rate=0.05,
        error_rate=0.05,
        min_sample_count=10,
    )

    result = OfflineTrustedRAGGateEvaluator(config).evaluate(
        HistoricalShadowResults(
            observations=observations,
            legacy_p95_latency_ms=100.0,
            rerank=_rerank(mrr_delta=-0.01, degraded_case_count=1),
        )
    )

    assert result.final_decision is GateDecision.BLOCK
    assert result.pass_count == 0
    assert result.block_count == 4
    assert set(result.block_reasons) == {
        "quality.evidence_overlap_below_threshold",
        "quality.source_coverage_below_threshold",
        "quality.confidence_below_threshold",
        "latency.p95_latency_ratio_exceeded",
        "reliability.timeout_rate_exceeded",
        "reliability.error_rate_exceeded",
        "rerank.aggregate_metric_regression",
        "rerank.degraded_cases_present",
    }


def test_missing_metrics_fail_closed_with_explicit_reasons():
    result = OfflineTrustedRAGGateEvaluator().evaluate(HistoricalShadowResults())

    assert result.final_decision is GateDecision.BLOCK
    assert result.pass_count == 0
    assert result.block_count == 4
    assert set(result.block_reasons) == {
        "quality.evidence_overlap_missing",
        "quality.source_coverage_missing",
        "quality.confidence_missing",
        "latency.p95_latency_ratio_missing",
        "reliability.sample_count_below_minimum",
        "reliability.timeout_rate_missing",
        "reliability.error_rate_missing",
        "rerank.assessment_missing",
    }


def test_any_trusted_rag_gate_failure_keeps_legacy_path():
    result = OfflineTrustedRAGGateEvaluator().evaluate(
        HistoricalShadowResults(
            observations=[_observation(index) for index in range(100)],
            legacy_p95_latency_ms=100.0,
            rerank=_rerank(independent_validation=False),
        )
    )

    assert result.quality_pass and result.latency_pass and result.reliability_pass
    assert result.rerank_pass is False
    assert result.final_decision is GateDecision.BLOCK
    assert result.recommended_path is RecommendedPath.LEGACY
    assert result.block_reasons == ["rerank.independent_validation_required"]
