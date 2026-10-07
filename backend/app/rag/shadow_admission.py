"""Content-free Shadow admission protocol and hard-deadline isolation."""
from __future__ import annotations

import math
import queue
import threading
import time
from collections import Counter
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShadowTaskStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class ShadowDeadlineResult(_StrictModel):
    status: ShadowTaskStatus
    elapsed_ms: float = Field(ge=0.0)
    result: Any | None = None
    error_type: str | None = None
    timeout_reason: str | None = None
    cancellation_requested: bool = False
    worker_isolated: bool = False


class ShadowDeadlineExecutor:
    """Run Shadow-only work behind a deadline without waiting for a stuck worker.

    Python cannot forcibly kill a thread safely. Workers are therefore daemonized,
    receive a cooperative cancellation event, and retain an isolation slot until
    they actually exit. A stuck worker can neither delay Legacy nor grow without
    bound.
    """

    def __init__(self, *, max_isolated_workers: int = 4) -> None:
        if max_isolated_workers < 1:
            raise ValueError("max_isolated_workers must be positive")
        self._slots = threading.BoundedSemaphore(max_isolated_workers)

    def run(
        self,
        task: Callable[[threading.Event], Any],
        *,
        deadline_ms: float,
    ) -> ShadowDeadlineResult:
        if deadline_ms <= 0:
            raise ValueError("deadline_ms must be positive")
        started = time.perf_counter()
        if not self._slots.acquire(blocking=False):
            return ShadowDeadlineResult(
                status=ShadowTaskStatus.CAPACITY_EXHAUSTED,
                elapsed_ms=0.0,
                timeout_reason="shadow.isolation_capacity_exhausted",
                worker_isolated=True,
            )

        cancellation = threading.Event()
        outcome: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                outcome.put((True, task(cancellation)))
            except BaseException as exc:  # isolate every Shadow worker failure
                outcome.put((False, exc))
            finally:
                self._slots.release()

        thread = threading.Thread(
            target=worker,
            name="trusted-rag-shadow-deadline",
            daemon=True,
        )
        thread.start()
        try:
            succeeded, value = outcome.get(timeout=deadline_ms / 1_000)
        except queue.Empty:
            cancellation.set()
            return ShadowDeadlineResult(
                status=ShadowTaskStatus.DEADLINE_EXCEEDED,
                elapsed_ms=round((time.perf_counter() - started) * 1_000, 6),
                timeout_reason="shadow.deadline_exceeded",
                cancellation_requested=True,
                worker_isolated=True,
            )

        elapsed_ms = round((time.perf_counter() - started) * 1_000, 6)
        if succeeded:
            return ShadowDeadlineResult(
                status=ShadowTaskStatus.COMPLETED,
                elapsed_ms=elapsed_ms,
                result=value,
            )
        return ShadowDeadlineResult(
            status=ShadowTaskStatus.FAILED,
            elapsed_ms=elapsed_ms,
            error_type=type(value).__name__,
            worker_isolated=True,
        )


class ShadowLatencyMetrics(_StrictModel):
    total_ms: float = Field(ge=0.0)
    rag_ms: float = Field(ge=0.0)
    tool_ms: float = Field(ge=0.0)


class ShadowQualityMetrics(_StrictModel):
    evidence_overlap: float = Field(ge=0.0, le=1.0)
    source_coverage: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class ShadowReliabilityMetrics(_StrictModel):
    timed_out: bool
    error_type: str | None
    timeout_reason: str | None
    cancellation_requested: bool
    worker_isolated: bool
    legacy_preserved: bool

    @model_validator(mode="after")
    def validate_timeout(self) -> "ShadowReliabilityMetrics":
        if self.timed_out and not self.timeout_reason:
            raise ValueError("timed_out samples require timeout_reason")
        if self.timeout_reason and not self.timed_out:
            raise ValueError("timeout_reason is only valid for timed_out samples")
        return self


class ShadowQueryType(str, Enum):
    CONCEPT_EXPLANATION = "concept_explanation"
    METHOD_COMPARISON = "method_comparison"
    OPERATION_STEPS = "operation_steps"
    PROGRAMMING_PRACTICE = "programming_practice"
    COMPREHENSIVE_QUESTION = "comprehensive_question"


class ShadowGateFeatures(_StrictModel):
    """Presence/provenance facts consumed by admission checks, never content."""

    quality_metrics_complete: bool
    latency_metrics_complete: bool
    reliability_metrics_complete: bool
    target_environment_sample: bool

    @model_validator(mode="after")
    def validate_completeness(self) -> "ShadowGateFeatures":
        if not (
            self.quality_metrics_complete
            and self.latency_metrics_complete
            and self.reliability_metrics_complete
        ):
            raise ValueError("frozen Shadow samples require complete gate metrics")
        return self


class ShadowEvaluationSample(_StrictModel):
    request_id: str = Field(min_length=1)
    query_type: ShadowQueryType
    latency_metrics: ShadowLatencyMetrics
    quality_metrics: ShadowQualityMetrics
    reliability_metrics: ShadowReliabilityMetrics
    gate_features: ShadowGateFeatures


class ShadowEvaluationAggregate(_StrictModel):
    sample_count: int
    query_type_counts: dict[str, int]
    evidence_overlap_mean: float | None
    source_coverage_mean: float | None
    confidence_mean: float | None
    p95_latency_ms: float | None
    timeout_rate: float
    error_rate: float
    all_timeouts_isolated: bool
    all_legacy_preserved: bool


class ShadowDatasetIntegrity(_StrictModel):
    valid: bool
    sample_count: int = Field(ge=0)
    query_type_counts: dict[str, int]
    errors: list[str]


class ShadowEvaluationDataset(_StrictModel):
    """Versioned metric-only dataset; content and user fields are rejected."""

    schema_version: Literal["trusted-rag-shadow-evaluation-v2"] = (
        "trusted-rag-shadow-evaluation-v2"
    )
    environment: str = Field(min_length=1)
    evaluation_window: str = Field(min_length=1)
    performance_verified: bool = False
    samples: list[ShadowEvaluationSample]

    @model_validator(mode="after")
    def validate_request_ids(self) -> "ShadowEvaluationDataset":
        request_ids = [item.request_id for item in self.samples]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("request_id must be unique")
        if self.performance_verified and not all(
            item.gate_features.target_environment_sample for item in self.samples
        ):
            raise ValueError(
                "performance_verified requires every sample from target environment"
            )
        return self

    def aggregate(self) -> ShadowEvaluationAggregate:
        count = len(self.samples)
        successful = [
            item for item in self.samples
            if item.reliability_metrics.error_type is None
            and not item.reliability_metrics.timed_out
        ]

        def mean(attribute: str) -> float | None:
            values = [
                getattr(item.quality_metrics, attribute)
                for item in successful
            ]
            return round(sum(values) / len(values), 6) if values else None

        latencies = sorted(item.latency_metrics.total_ms for item in self.samples)
        p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None
        timeouts = [item for item in self.samples if item.reliability_metrics.timed_out]
        errors = [
            item for item in self.samples
            if item.reliability_metrics.error_type is not None
            or item.reliability_metrics.timed_out
        ]
        return ShadowEvaluationAggregate(
            sample_count=count,
            query_type_counts=dict(sorted(Counter(item.query_type for item in self.samples).items())),
            evidence_overlap_mean=mean("evidence_overlap"),
            source_coverage_mean=mean("source_coverage"),
            confidence_mean=mean("confidence"),
            p95_latency_ms=round(p95, 6) if p95 is not None else None,
            timeout_rate=(len(timeouts) / count) if count else 0.0,
            error_rate=(len(errors) / count) if count else 0.0,
            all_timeouts_isolated=all(
                item.reliability_metrics.worker_isolated for item in timeouts
            ),
            all_legacy_preserved=all(
                item.reliability_metrics.legacy_preserved for item in self.samples
            ),
        )

    def check_integrity(self, *, minimum_per_query_type: int = 20) -> ShadowDatasetIntegrity:
        """Return a deterministic freeze check; any error blocks dataset use."""
        if minimum_per_query_type < 1:
            raise ValueError("minimum_per_query_type must be positive")
        counts = Counter(item.query_type.value for item in self.samples)
        errors: list[str] = []
        for query_type in ShadowQueryType:
            count = counts.get(query_type.value, 0)
            if count < minimum_per_query_type:
                errors.append(
                    f"query_type_underrepresented:{query_type.value}:{count}"
                )
        return ShadowDatasetIntegrity(
            valid=not errors,
            sample_count=len(self.samples),
            query_type_counts=dict(sorted(counts.items())),
            errors=errors,
        )

    def assert_integrity(self, *, minimum_per_query_type: int = 20) -> None:
        result = self.check_integrity(
            minimum_per_query_type=minimum_per_query_type
        )
        if not result.valid:
            raise ValueError("Shadow dataset integrity failed: " + "; ".join(result.errors))
