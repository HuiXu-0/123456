from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.canary_fault_injection import (
    CanaryFaultInjectionEvaluator,
    RollbackPath,
    ScenarioStatus,
)


RESULTS = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "trusted_rag_fault_results.json"
)


@pytest.fixture(scope="module")
def report():
    return CanaryFaultInjectionEvaluator(
        hard_timeout_budget_ms=10,
        hang_probe_ms=100,
    ).evaluate()


def _by_id(report):
    return {item.scenarioId: item for item in report.scenarios}


def test_all_faults_keep_legacy_authoritative_and_use_structured_legacy_rollback(report):
    assert report.scenarioCount == 11
    assert report.passCount == 11
    assert report.blockCount == 0
    assert all(item.legacyPreserved for item in report.scenarios)
    assert all(item.rollbackPath is RollbackPath.LEGACY for item in report.scenarios)
    assert all(item.structuredReason and " " not in item.structuredReason for item in report.scenarios)
    assert all(item.contentSafe for item in report.scenarios)


def test_exception_timeout_and_hard_hang_are_distinct_and_deadline_isolates(report):
    scenarios = _by_id(report)
    exception_timeout = scenarios["retriever_timeout_exception"]
    hard_hang = scenarios["retriever_hard_hang"]

    assert exception_timeout.timeoutKind == "exception"
    assert exception_timeout.recordedErrorType == "TimeoutError"
    assert exception_timeout.trustedIsolated is True
    assert exception_timeout.status is ScenarioStatus.PASS
    assert hard_hang.timeoutKind == "hard_hang"
    assert hard_hang.recordedErrorType == "ShadowDeadlineExceeded"
    assert hard_hang.elapsedMs < 100
    assert hard_hang.trustedIsolated is True
    assert hard_hang.deadlineEnforced is True
    assert hard_hang.cancellationRequested is True
    assert hard_hang.timeoutReason == "shadow.deadline_exceeded"
    assert hard_hang.status is ScenarioStatus.PASS
    assert report.finalDecision is ScenarioStatus.PASS
    assert report.blockReasons == []


def test_retriever_vector_model_llm_and_metrics_scenarios_are_covered(report):
    scenarios = _by_id(report)
    expected = {
        "retriever_runtime_error": "RetrieverFailure",
        "vector_collection_unavailable": "CollectionUnavailableError",
        "vector_query_error": "VectorQueryError",
        "vector_data_error": "VectorDataError",
        "reranker_load_failure": "RerankerLoadError",
        "reranker_inference_failure": "RerankerInferenceError",
        "llm_controlled_degradation": "LLMDegradationError",
    }
    for scenario_id, error_type in expected.items():
        item = scenarios[scenario_id]
        assert item.recordedErrorType == error_type
        assert item.status is ScenarioStatus.PASS
        assert item.trustedIsolated is True
    assert scenarios["retriever_empty_evidence"].recordedErrorType is None
    assert scenarios["retriever_empty_evidence"].status is ScenarioStatus.PASS
    assert scenarios["reranker_inference_failure"].componentFallback == "hybrid_then_legacy"
    assert scenarios["reranker_inference_failure"].limitation == (
        "DecisionReranker fallback verification=true"
    )
    assert scenarios["metrics_sink_failure"].metricsIsolated is True
    assert scenarios["metrics_sink_failure"].status is ScenarioStatus.PASS


def test_report_never_persists_query_evidence_or_exception_messages(report):
    serialized = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in (
        "FAULT-INJECTION-SENSITIVE-QUERY",
        "FAULT-INJECTION-SENSITIVE-EVIDENCE",
        "SENSITIVE-DESCRIPTION",
        "SENSITIVE-LEGACY-EVIDENCE",
        "SENSITIVE-TIMEOUT-MESSAGE",
        "SENSITIVE-RETRIEVER-MESSAGE",
        "SENSITIVE-VECTOR",
        "SENSITIVE-RERANKER",
        "SENSITIVE-LLM-MESSAGE",
        "SENSITIVE-METRICS-SINK-MESSAGE",
    ):
        assert forbidden not in serialized


def test_invalid_hang_probe_configuration_fails_closed():
    with pytest.raises(ValueError, match="hang_probe_ms"):
        CanaryFaultInjectionEvaluator(hard_timeout_budget_ms=10, hang_probe_ms=10)
    with pytest.raises(ValueError, match="hang_probe_ms"):
        CanaryFaultInjectionEvaluator(hard_timeout_budget_ms=0, hang_probe_ms=10)


def test_committed_fault_result_preserves_block_and_environment_contract():
    result = json.loads(RESULTS.read_text(encoding="utf-8"))

    assert result["schemaVersion"] == "trusted-rag-fault-injection-v2"
    assert result["evaluationType"] == "offline_fault_injection"
    assert result["productionPerformance"] is False
    assert result["productionMutation"] is False
    assert result["legacyAuthority"] is True
    assert result["scenarioCount"] == 11
    assert result["passCount"] == 11
    assert result["blockCount"] == 0
    assert result["finalDecision"] == "PASS"
    assert result["blockReasons"] == []
    assert result["environment"]["runtime"]["python"]
    assert result["environment"]["runtime"]["platform"]
    assert all(item["rollbackPath"] == "legacy" for item in result["scenarios"])
