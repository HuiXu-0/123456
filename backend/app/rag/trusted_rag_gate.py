"""Fail-closed offline admission gate for Trusted RAG canary evaluation."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.rag.shadow_admission import ShadowEvaluationDataset
from app.rag.evidence_quality_evaluation import QualityEvaluationResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanaryDecisionValue(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class TrustedRAGGateConfig(_StrictModel):
    quality_gate_version: str = "quality-gate-v2"
    min_sample_count: int = Field(default=100, ge=1)
    # At least two of every three reviewed Evidence records must be relevant.
    min_relevance: float = Field(default=0.67, ge=0.0, le=1.0)
    min_support_rate: float = Field(default=0.80, ge=0.0, le=1.0)
    min_completeness: float = Field(default=0.30, ge=0.0, le=1.0)
    min_source_coverage: float = Field(default=0.80, ge=0.0, le=1.0)
    max_p95_latency_ms: float = Field(default=1500.0, gt=0.0)
    max_timeout_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    max_error_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    max_fault_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_rerank_degraded_cases: int = Field(default=0, ge=0)
    min_query_type_samples: int = Field(default=20, ge=1)
    required_query_types: tuple[str, ...] = (
        "concept_explanation",
        "method_comparison",
        "operation_steps",
        "programming_practice",
        "comprehensive_question",
    )


class RerankMetrics(_StrictModel):
    independent_validation: bool
    metrics_provisional: bool
    human_review_complete: bool
    mrr_delta: float | None = None
    ndcg_at_3_delta: float | None = None
    ndcg_at_5_delta: float | None = None
    degraded_case_count: int | None = Field(default=None, ge=0)
    policy_enabled: bool = True
    fallback: str = "hybrid"
    reason: str = "validated_policy"

    @classmethod
    def from_blind_evaluation(cls, payload: dict[str, Any]) -> "RerankMetrics":
        delta = payload.get("delta") or {}
        review = payload.get("humanPreference") or {}
        relevance_review = payload.get("relevanceReview") or {}
        human_complete = (
            review.get("status") == "completed"
            and relevance_review.get("status") == "completed"
        )
        return cls(
            independent_validation=(
                payload.get("evaluationType")
                == "offline_independent_blind_candidate_ranking"
            ),
            metrics_provisional=bool(payload.get("metricsProvisional", True)),
            human_review_complete=human_complete,
            mrr_delta=delta.get("mrr"),
            ndcg_at_3_delta=delta.get("ndcg@3"),
            ndcg_at_5_delta=delta.get("ndcg@5"),
            degraded_case_count=payload.get("degradedCaseCount"),
        )


class ShadowMetrics(_StrictModel):
    """Aggregated, content-free Shadow evidence supplied to the offline gate."""

    sample_count: int | None = Field(default=None, ge=0)
    evidence_overlap_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    source_coverage_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    p95_latency_ms: float | None = Field(default=None, ge=0.0)
    timeout_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    performance_verified: bool = False
    rerank: RerankMetrics | None = None


class FaultScenarioResult(_StrictModel):
    scenario_id: str
    status: str
    legacy_preserved: bool
    trusted_isolated: bool
    rollback_path: str
    content_safe: bool
    structured_reason: str


class FaultInjectionResults(_StrictModel):
    scenario_count: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    scenarios: list[FaultScenarioResult]

    @classmethod
    def from_report(cls, payload: dict[str, Any]) -> "FaultInjectionResults":
        return cls(
            scenario_count=payload.get("scenarioCount", 0),
            pass_count=payload.get("passCount", 0),
            block_count=payload.get("blockCount", 0),
            scenarios=[
                FaultScenarioResult(
                    scenario_id=item.get("scenarioId", ""),
                    status=item.get("status", ""),
                    legacy_preserved=bool(item.get("legacyPreserved", False)),
                    trusted_isolated=bool(item.get("trustedIsolated", False)),
                    rollback_path=item.get("rollbackPath", ""),
                    content_safe=bool(item.get("contentSafe", False)),
                    structured_reason=item.get("structuredReason", "fault.unknown"),
                )
                for item in payload.get("scenarios", [])
            ],
        )


class GateSnapshot(_StrictModel):
    quality_gate_version: str
    sample_count: int | None
    p95_latency_ms: float | None
    timeout_rate: float | None
    error_rate: float | None
    fault_scenario_count: int | None
    fault_failure_rate: float | None
    query_type_counts: dict[str, int] | None = None
    shadow_input_type: str
    quality_evaluation_request_count: int | None = None
    human_relevance: float | None = None
    support_rate: float | None = None
    completeness: float | None = None
    source_coverage: float | None = None
    evidence_overlap_diagnostic: float | None = None
    confidence_diagnostic: float | None = None


class CanaryDecision(_StrictModel):
    quality_pass: bool
    latency_pass: bool
    reliability_pass: bool
    rerank_pass: bool
    final_decision: CanaryDecisionValue
    block_reasons: list[str]
    remediation: list[str]
    rollback_recommended: bool
    recommended_action: str
    snapshot: GateSnapshot


class TrustedRAGGate:
    """Pure evaluator. It has no route, API, Agent, Workflow, or RAG side effects."""

    def __init__(self, config: TrustedRAGGateConfig | None = None) -> None:
        self.config = config or TrustedRAGGateConfig()

    def evaluate(
        self,
        shadow_metrics: ShadowMetrics | ShadowEvaluationDataset | None,
        fault_results: FaultInjectionResults | None,
        rerank_metrics: RerankMetrics | None = None,
        quality_evaluation: QualityEvaluationResult | None = None,
    ) -> CanaryDecision:
        reasons: dict[str, list[str]] = {
            "quality": [],
            "latency": [],
            "reliability": [],
            "rerank": [],
        }
        dataset = (
            shadow_metrics
            if isinstance(shadow_metrics, ShadowEvaluationDataset)
            else None
        )
        query_type_counts: dict[str, int] | None = None
        if dataset is not None:
            aggregate = dataset.aggregate()
            query_type_counts = aggregate.query_type_counts
            shadow = ShadowMetrics(
                sample_count=aggregate.sample_count,
                evidence_overlap_mean=aggregate.evidence_overlap_mean,
                source_coverage_mean=aggregate.source_coverage_mean,
                confidence_mean=aggregate.confidence_mean,
                p95_latency_ms=aggregate.p95_latency_ms,
                timeout_rate=aggregate.timeout_rate,
                error_rate=aggregate.error_rate,
                performance_verified=dataset.performance_verified,
                rerank=rerank_metrics,
            )
        else:
            shadow = shadow_metrics

        if shadow is None:
            reasons["quality"].append("quality.shadow_metrics_missing")
            reasons["latency"].append("latency.shadow_metrics_missing")
            reasons["reliability"].append("reliability.shadow_metrics_missing")
            reasons["rerank"].append("rerank.metrics_missing")
        else:
            if quality_evaluation is None:
                reasons["quality"].append("quality.human_evaluation_missing")
            else:
                if not quality_evaluation.review_complete:
                    reasons["quality"].append("quality.human_review_incomplete")
                if quality_evaluation.request_count < self.config.min_sample_count:
                    reasons["quality"].append("quality.human_sample_count_below_minimum")
                if quality_evaluation.human_relevance < self.config.min_relevance:
                    reasons["quality"].append("quality.relevance_below_threshold")
                if quality_evaluation.support_rate < self.config.min_support_rate:
                    reasons["quality"].append("quality.support_rate_below_threshold")
                if quality_evaluation.completeness < self.config.min_completeness:
                    reasons["quality"].append("quality.completeness_below_threshold")
                if quality_evaluation.source_coverage < self.config.min_source_coverage:
                    reasons["quality"].append("quality.source_coverage_below_threshold")
                if dataset is not None:
                    dataset_request_ids = {item.request_id for item in dataset.samples}
                    if set(quality_evaluation.evaluated_request_ids) != dataset_request_ids:
                        reasons["quality"].append("quality.request_ids_mismatch")
                    for name, reviewed, measured in (
                        ("evidence_overlap", quality_evaluation.evidence_overlap, shadow.evidence_overlap_mean),
                        ("source_coverage", quality_evaluation.source_coverage, shadow.source_coverage_mean),
                    ):
                        if measured is None or abs(reviewed - measured) > 0.000001:
                            reasons["quality"].append(f"quality.{name}_input_mismatch")

            if shadow.p95_latency_ms is None:
                reasons["latency"].append("latency.p95_missing")
            elif shadow.p95_latency_ms > self.config.max_p95_latency_ms:
                reasons["latency"].append("latency.p95_exceeded")
            if not shadow.performance_verified:
                reasons["latency"].append("latency.performance_not_verified")

            if shadow.sample_count is None:
                reasons["reliability"].append("reliability.sample_count_missing")
            elif shadow.sample_count < self.config.min_sample_count:
                reasons["reliability"].append("reliability.sample_count_below_minimum")
            for name, value, threshold in (
                ("timeout_rate", shadow.timeout_rate, self.config.max_timeout_rate),
                ("error_rate", shadow.error_rate, self.config.max_error_rate),
            ):
                if value is None:
                    reasons["reliability"].append(f"reliability.{name}_missing")
                elif value > threshold:
                    reasons["reliability"].append(f"reliability.{name}_exceeded")

            if dataset is not None:
                for query_type in self.config.required_query_types:
                    if query_type_counts.get(query_type, 0) < self.config.min_query_type_samples:
                        reasons["reliability"].append(
                            f"reliability.query_type_underrepresented:{query_type}"
                        )
                if not aggregate.all_timeouts_isolated:
                    reasons["reliability"].append(
                        "reliability.deadline_timeout_not_isolated"
                    )
                if not aggregate.all_legacy_preserved:
                    reasons["reliability"].append(
                        "reliability.legacy_not_preserved_in_shadow_dataset"
                    )

            self._evaluate_rerank(shadow.rerank, reasons["rerank"])

        fault_failure_rate: float | None = None
        if fault_results is None:
            reasons["reliability"].append("reliability.fault_results_missing")
        elif fault_results.scenario_count == 0:
            reasons["reliability"].append("reliability.fault_scenarios_missing")
        else:
            if len(fault_results.scenarios) != fault_results.scenario_count:
                reasons["reliability"].append("reliability.fault_result_count_mismatch")
            fault_failure_rate = round(
                fault_results.block_count / fault_results.scenario_count, 6
            )
            if fault_failure_rate > self.config.max_fault_failure_rate:
                reasons["reliability"].append("reliability.fault_failure_rate_exceeded")
            for scenario in fault_results.scenarios:
                if scenario.status != "PASS":
                    reasons["reliability"].append(
                        f"reliability.fault_blocked:{scenario.structured_reason}"
                    )
                if not scenario.legacy_preserved:
                    reasons["reliability"].append(
                        f"reliability.legacy_not_preserved:{scenario.scenario_id}"
                    )
                if not scenario.trusted_isolated:
                    reasons["reliability"].append(
                        f"reliability.trusted_not_isolated:{scenario.scenario_id}"
                    )
                if scenario.rollback_path != "legacy":
                    reasons["reliability"].append(
                        f"reliability.rollback_path_invalid:{scenario.scenario_id}"
                    )
                if not scenario.content_safe:
                    reasons["reliability"].append(
                        f"reliability.content_safety_failed:{scenario.scenario_id}"
                    )

        passes = {name: not values for name, values in reasons.items()}
        block_reasons = list(dict.fromkeys(reason for values in reasons.values() for reason in values))
        remediation = self._remediation(block_reasons)
        final = (
            CanaryDecisionValue.PASS
            if all(passes.values())
            else CanaryDecisionValue.BLOCK
        )
        return CanaryDecision(
            quality_pass=passes["quality"],
            latency_pass=passes["latency"],
            reliability_pass=passes["reliability"],
            rerank_pass=passes["rerank"],
            final_decision=final,
            block_reasons=block_reasons,
            remediation=remediation,
            rollback_recommended=final is CanaryDecisionValue.BLOCK,
            recommended_action=(
                "eligible_for_manual_canary_review"
                if final is CanaryDecisionValue.PASS
                else "keep_legacy_and_set_canary_weight_to_zero"
            ),
            snapshot=GateSnapshot(
                quality_gate_version=self.config.quality_gate_version,
                sample_count=shadow.sample_count if shadow else None,
                p95_latency_ms=shadow.p95_latency_ms if shadow else None,
                timeout_rate=shadow.timeout_rate if shadow else None,
                error_rate=shadow.error_rate if shadow else None,
                fault_scenario_count=(fault_results.scenario_count if fault_results else None),
                fault_failure_rate=fault_failure_rate,
                query_type_counts=query_type_counts,
                shadow_input_type=(
                    "dataset" if dataset is not None
                    else "aggregate" if shadow is not None
                    else "missing"
                ),
                quality_evaluation_request_count=(
                    quality_evaluation.request_count if quality_evaluation else None
                ),
                human_relevance=(
                    quality_evaluation.human_relevance if quality_evaluation else None
                ),
                support_rate=(
                    quality_evaluation.support_rate if quality_evaluation else None
                ),
                completeness=(
                    quality_evaluation.completeness if quality_evaluation else None
                ),
                source_coverage=(
                    quality_evaluation.source_coverage if quality_evaluation else None
                ),
                evidence_overlap_diagnostic=(
                    shadow.evidence_overlap_mean if shadow else None
                ),
                confidence_diagnostic=(shadow.confidence_mean if shadow else None),
            ),
        )

    @staticmethod
    def _remediation(block_reasons: list[str]) -> list[str]:
        actions: list[str] = []
        for reason in block_reasons:
            if reason.startswith("quality."):
                actions.append("collect_and_review_shadow_quality_metrics")
            elif reason.startswith("latency."):
                actions.append("verify_target_environment_shadow_latency")
            elif "query_type_underrepresented" in reason:
                actions.append("complete_stratified_shadow_dataset")
            elif "deadline" in reason or "trusted_not_isolated" in reason:
                actions.append("enforce_shadow_deadline_and_worker_isolation")
            elif reason.startswith("reliability.fault"):
                actions.append("rerun_fault_injection_until_all_scenarios_pass")
            elif reason.startswith("reliability."):
                actions.append("reduce_shadow_timeout_error_rate_and_preserve_legacy")
            elif reason.startswith("rerank."):
                actions.append("apply_query_type_gate_or_hybrid_fallback_and_revalidate_rerank")
        return list(dict.fromkeys(actions))

    def _evaluate_rerank(
        self, rerank: RerankMetrics | None, reasons: list[str]
    ) -> None:
        if rerank is None:
            reasons.append("rerank.metrics_missing")
            return
        if not rerank.policy_enabled:
            if rerank.fallback != "hybrid":
                reasons.append("rerank.disabled_fallback_must_be_hybrid")
            if rerank.degraded_case_count not in (0, None):
                reasons.append("rerank.disabled_policy_degraded_cases_present")
            return
        if not rerank.independent_validation:
            reasons.append("rerank.independent_validation_required")
        if rerank.metrics_provisional:
            reasons.append("rerank.metrics_still_provisional")
        if not rerank.human_review_complete:
            reasons.append("rerank.human_review_incomplete")
        for name, value in (
            ("mrr", rerank.mrr_delta),
            ("ndcg_at_3", rerank.ndcg_at_3_delta),
            ("ndcg_at_5", rerank.ndcg_at_5_delta),
        ):
            if value is None:
                reasons.append(f"rerank.{name}_delta_missing")
            elif value < 0:
                reasons.append(f"rerank.{name}_regressed")
        if rerank.degraded_case_count is None:
            reasons.append("rerank.degraded_case_count_missing")
        elif rerank.degraded_case_count > self.config.max_rerank_degraded_cases:
            reasons.append("rerank.degraded_cases_exceeded")
