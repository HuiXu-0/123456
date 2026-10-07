"""Content-free Agent-RAG Shadow metric collection (TASK-003-E4-B)."""
from __future__ import annotations

import math
from collections import Counter, deque
from threading import Lock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShadowMetricObservation(_StrictModel):
    """One metric-only sample; query, user and knowledge content are forbidden."""

    traceId: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    total_latency: float = Field(ge=0.0)
    rag_latency: float | None = Field(default=None, ge=0.0)
    tool_latency: float = Field(ge=0.0)
    timed_out: bool = False
    error_type: str | None = None
    evidence_overlap: float | None = Field(default=None, ge=0.0, le=1.0)
    source_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class Distribution(_StrictModel):
    count: int = Field(ge=0)
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None


class PerformanceMetrics(_StrictModel):
    total_latency: Distribution
    rag_latency: Distribution
    tool_latency: Distribution
    timeout_rate: float = Field(ge=0.0, le=1.0)
    error_rate: float = Field(ge=0.0, le=1.0)


class QualityMetrics(_StrictModel):
    evidence_overlap: Distribution
    source_coverage: Distribution
    confidence_distribution: dict[str, int]
    reason_codes: dict[str, int]


class ShadowMetricsReport(_StrictModel):
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    performance: PerformanceMetrics
    quality: QualityMetrics


class ShadowMetricsSink(Protocol):
    def record(self, observation: ShadowMetricObservation) -> None: ...


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _distribution(values: list[float]) -> Distribution:
    if not values:
        return Distribution(count=0)
    return Distribution(
        count=len(values),
        mean=round(sum(values) / len(values), 6),
        p50=round(_percentile(values, 0.50), 6),
        p95=round(_percentile(values, 0.95), 6),
    )


class ShadowMetricsCollector:
    """Thread-safe bounded in-memory collector for offline Shadow analysis."""

    def __init__(self, *, max_samples: int = 10_000) -> None:
        if max_samples < 1:
            raise ValueError("max_samples must be positive")
        self._observations: deque[ShadowMetricObservation] = deque(maxlen=max_samples)
        self._lock = Lock()

    def record(self, observation: ShadowMetricObservation) -> None:
        validated = ShadowMetricObservation.model_validate(observation)
        with self._lock:
            self._observations.append(validated)

    def observations(self) -> tuple[ShadowMetricObservation, ...]:
        with self._lock:
            return tuple(self._observations)

    def report(self) -> ShadowMetricsReport:
        samples = self.observations()
        successful = [item for item in samples if item.error_type is None]
        confidence_bins = Counter({"low_0_0.5": 0, "medium_0.5_0.8": 0, "high_0.8_1.0": 0})
        reason_codes: Counter[str] = Counter()
        for item in successful:
            if item.confidence is not None:
                if item.confidence < 0.5:
                    confidence_bins["low_0_0.5"] += 1
                elif item.confidence < 0.8:
                    confidence_bins["medium_0.5_0.8"] += 1
                else:
                    confidence_bins["high_0.8_1.0"] += 1
            reason_codes.update(item.reason_codes)

        count = len(samples)
        return ShadowMetricsReport(
            sample_count=count,
            success_count=len(successful),
            performance=PerformanceMetrics(
                total_latency=_distribution([item.total_latency for item in samples]),
                rag_latency=_distribution(
                    [item.rag_latency for item in samples if item.rag_latency is not None]
                ),
                tool_latency=_distribution([item.tool_latency for item in samples]),
                timeout_rate=(sum(item.timed_out for item in samples) / count) if count else 0.0,
                error_rate=(sum(item.error_type is not None for item in samples) / count)
                if count
                else 0.0,
            ),
            quality=QualityMetrics(
                evidence_overlap=_distribution(
                    [
                        item.evidence_overlap
                        for item in successful
                        if item.evidence_overlap is not None
                    ]
                ),
                source_coverage=_distribution(
                    [
                        item.source_coverage
                        for item in successful
                        if item.source_coverage is not None
                    ]
                ),
                confidence_distribution=dict(confidence_bins),
                reason_codes=dict(sorted(reason_codes.items())),
            ),
        )
