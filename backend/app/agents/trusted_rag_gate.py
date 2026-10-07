"""Offline migration gate for Trusted RAG canary admission (TASK-003-E4-C)."""
from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.agents.shadow_metrics import ShadowMetricObservation


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateDecision(str, Enum):
    PASS = "pass"
    BLOCK = "block"


class RecommendedPath(str, Enum):
    LEGACY = "legacy"
    TRUSTED_RAG_CANARY = "trusted_rag_canary"


class TrustedRAGGateConfig(_StrictModel):
    """Versionable thresholds; confidence remains a retrieval heuristic."""

    evidence_overlap_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    source_coverage_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    p95_latency_ratio: float = Field(default=1.50, gt=0.0)
    timeout_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    error_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    min_sample_count: int = Field(default=100, ge=1)


class RerankAssessment(_StrictModel):
    """Frozen offline evidence required before conditional Rerank may pass."""

    independent_validation: bool
    mrr_delta: float
    ndcg_at_3_delta: float
    ndcg_at_5_delta: float
    degraded_case_count: int = Field(ge=0)


class HistoricalShadowResults(_StrictModel):
    observations: list[ShadowMetricObservation] = Field(default_factory=list)
    legacy_p95_latency_ms: float | None = Field(default=None, gt=0.0)
    rerank: RerankAssessment | None = None


class GateMetricSnapshot(_StrictModel):
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    evidence_overlap: float | None = Field(default=None, ge=0.0, le=1.0)
    source_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    trusted_p95_latency_ms: float | None = Field(default=None, ge=0.0)
    legacy_p95_latency_ms: float | None = Field(default=None, gt=0.0)
    p95_latency_ratio: float | None = Field(default=None, ge=0.0)
    timeout_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class TrustedRAGGate(_StrictModel):
    """Auditable migration decision; it never changes a runtime route."""

    quality_pass: bool
    latency_pass: bool
    reliability_pass: bool
    rerank_pass: bool
    final_decision: GateDecision
    pass_count: int = Field(ge=0, le=4)
    block_count: int = Field(ge=0, le=4)
    block_reasons: list[str]
    recommended_path: RecommendedPath
    metrics: GateMetricSnapshot


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 6)


class OfflineTrustedRAGGateEvaluator:
    """Evaluates historical Shadow observations without production side effects."""

    def __init__(self, config: TrustedRAGGateConfig | None = None) -> None:
        self.config = config or TrustedRAGGateConfig()

    def evaluate(self, history: HistoricalShadowResults) -> TrustedRAGGate:
        observations = history.observations
        successful = [item for item in observations if item.error_type is None]
        evidence_values = [
            item.evidence_overlap for item in successful if item.evidence_overlap is not None
        ]
        coverage_values = [
            item.source_coverage for item in successful if item.source_coverage is not None
        ]
        confidence_values = [
            item.confidence for item in successful if item.confidence is not None
        ]
        evidence_overlap = _mean(evidence_values)
        source_coverage = _mean(coverage_values)
        confidence = _mean(confidence_values)
        trusted_p95 = _p95([item.tool_latency for item in observations])
        latency_ratio = (
            round(trusted_p95 / history.legacy_p95_latency_ms, 6)
            if trusted_p95 is not None and history.legacy_p95_latency_ms is not None
            else None
        )
        sample_count = len(observations)
        timeout_rate = (
            round(sum(item.timed_out for item in observations) / sample_count, 6)
            if sample_count
            else None
        )
        error_rate = (
            round(sum(item.error_type is not None for item in observations) / sample_count, 6)
            if sample_count
            else None
        )

        reasons: dict[str, list[str]] = {
            "quality": [], "latency": [], "reliability": [], "rerank": []
        }
        quality_metrics = (
            ("evidence_overlap", evidence_overlap, self.config.evidence_overlap_threshold),
            ("source_coverage", source_coverage, self.config.source_coverage_threshold),
            ("confidence", confidence, self.config.confidence_threshold),
        )
        for name, value, threshold in quality_metrics:
            if value is None:
                reasons["quality"].append(f"quality.{name}_missing")
            elif value < threshold:
                reasons["quality"].append(f"quality.{name}_below_threshold")

        metric_counts = {
            "evidence_overlap": len(evidence_values),
            "source_coverage": len(coverage_values),
            "confidence": len(confidence_values),
        }
        for name, count in metric_counts.items():
            if successful and count != len(successful):
                missing_reason = f"quality.{name}_missing"
                if missing_reason not in reasons["quality"]:
                    reasons["quality"].append(missing_reason)

        if latency_ratio is None:
            reasons["latency"].append("latency.p95_latency_ratio_missing")
        elif latency_ratio > self.config.p95_latency_ratio:
            reasons["latency"].append("latency.p95_latency_ratio_exceeded")

        if sample_count < self.config.min_sample_count:
            reasons["reliability"].append("reliability.sample_count_below_minimum")
        if timeout_rate is None:
            reasons["reliability"].append("reliability.timeout_rate_missing")
        elif timeout_rate > self.config.timeout_rate:
            reasons["reliability"].append("reliability.timeout_rate_exceeded")
        if error_rate is None:
            reasons["reliability"].append("reliability.error_rate_missing")
        elif error_rate > self.config.error_rate:
            reasons["reliability"].append("reliability.error_rate_exceeded")

        rerank = history.rerank
        if rerank is None:
            reasons["rerank"].append("rerank.assessment_missing")
        else:
            if not rerank.independent_validation:
                reasons["rerank"].append("rerank.independent_validation_required")
            if min(rerank.mrr_delta, rerank.ndcg_at_3_delta, rerank.ndcg_at_5_delta) < 0:
                reasons["rerank"].append("rerank.aggregate_metric_regression")
            if rerank.degraded_case_count:
                reasons["rerank"].append("rerank.degraded_cases_present")

        passes = {name: not failures for name, failures in reasons.items()}
        pass_count = sum(passes.values())
        final_decision = GateDecision.PASS if pass_count == 4 else GateDecision.BLOCK
        return TrustedRAGGate(
            quality_pass=passes["quality"],
            latency_pass=passes["latency"],
            reliability_pass=passes["reliability"],
            rerank_pass=passes["rerank"],
            final_decision=final_decision,
            pass_count=pass_count,
            block_count=4 - pass_count,
            block_reasons=[reason for group in reasons.values() for reason in group],
            recommended_path=(
                RecommendedPath.TRUSTED_RAG_CANARY
                if final_decision is GateDecision.PASS
                else RecommendedPath.LEGACY
            ),
            metrics=GateMetricSnapshot(
                sample_count=sample_count,
                success_count=len(successful),
                evidence_overlap=evidence_overlap,
                source_coverage=source_coverage,
                confidence=confidence,
                trusted_p95_latency_ms=trusted_p95,
                legacy_p95_latency_ms=history.legacy_p95_latency_ms,
                p95_latency_ratio=latency_ratio,
                timeout_rate=timeout_rate,
                error_rate=error_rate,
            ),
        )
