"""Pure Canary review state machine for Trusted RAG.

This module is a control-plane design primitive only.  It evaluates a proposed
state transition and never changes routing, API, Agent, or Workflow state.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.trusted_rag_gate import CanaryDecision, CanaryDecisionValue


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanaryState(str, Enum):
    LEGACY_ONLY = "LEGACY_ONLY"
    SHADOW_ONLY = "SHADOW_ONLY"
    CANARY_1 = "CANARY_1"
    CANARY_5 = "CANARY_5"
    CANARY_20 = "CANARY_20"
    FULL_TRUSTED = "FULL_TRUSTED"
    ROLLBACK = "ROLLBACK"


STATE_WEIGHTS: dict[CanaryState, float] = {
    CanaryState.LEGACY_ONLY: 0.0,
    CanaryState.SHADOW_ONLY: 0.0,
    CanaryState.CANARY_1: 0.01,
    CanaryState.CANARY_5: 0.05,
    CanaryState.CANARY_20: 0.20,
    CanaryState.FULL_TRUSTED: 1.0,
    CanaryState.ROLLBACK: 0.0,
}


class QualityGateStatus(str, Enum):
    PASS_READY = "PASS_READY"
    BLOCK = "BLOCK"


class QualityGateProtocol(_StrictModel):
    status: QualityGateStatus
    gate_version: str
    block_reasons: list[str] = Field(default_factory=list)

    @classmethod
    def from_gate_decision(cls, decision: CanaryDecision) -> "QualityGateProtocol":
        ready = (
            decision.final_decision is CanaryDecisionValue.PASS
            and not decision.block_reasons
        )
        return cls(
            status=(QualityGateStatus.PASS_READY if ready else QualityGateStatus.BLOCK),
            gate_version=decision.snapshot.quality_gate_version,
            block_reasons=list(decision.block_reasons),
        )


class RollbackCondition(_StrictModel):
    max_error_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    max_timeout_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    max_latency_p95_ms: float = Field(default=1500.0, gt=0.0)
    max_quality_regression: float = Field(default=0.05, ge=0.0, le=1.0)
    min_observation_samples: int = Field(default=100, ge=1)


class CanaryPolicyProtocol(_StrictModel):
    """Versionable protocol consumed by a future, separately reviewed router."""

    current_state: CanaryState
    target_weight: float = Field(ge=0.0, le=1.0)
    quality_gate: QualityGateProtocol
    rollback_condition: RollbackCondition = Field(default_factory=RollbackCondition)

    @model_validator(mode="after")
    def validate_state_weight(self) -> "CanaryPolicyProtocol":
        expected = STATE_WEIGHTS[self.current_state]
        if abs(self.target_weight - expected) > 1e-9:
            raise ValueError(
                f"target_weight must be {expected} for state {self.current_state.value}"
            )
        return self


class CanaryQualitySnapshot(_StrictModel):
    relevance: float = Field(ge=0.0, le=1.0)
    support_rate: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    source_coverage: float = Field(ge=0.0, le=1.0)


class CanaryObservation(_StrictModel):
    sample_count: int = Field(ge=0)
    error_rate: float = Field(ge=0.0, le=1.0)
    timeout_rate: float = Field(ge=0.0, le=1.0)
    latency_p95_ms: float = Field(ge=0.0)
    quality: CanaryQualitySnapshot
    quality_baseline: CanaryQualitySnapshot


class ReviewAction(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    ROLLBACK = "ROLLBACK"


class CanaryReviewResult(_StrictModel):
    previous_state: CanaryState
    next_state: CanaryState
    target_weight: float
    action: ReviewAction
    reasons: list[str] = Field(default_factory=list)


_ALLOWED_TRANSITIONS: dict[CanaryState, set[CanaryState]] = {
    CanaryState.LEGACY_ONLY: {CanaryState.LEGACY_ONLY, CanaryState.SHADOW_ONLY},
    CanaryState.SHADOW_ONLY: {
        CanaryState.LEGACY_ONLY,
        CanaryState.SHADOW_ONLY,
        CanaryState.CANARY_1,
    },
    CanaryState.CANARY_1: {
        CanaryState.SHADOW_ONLY,
        CanaryState.CANARY_1,
        CanaryState.CANARY_5,
    },
    CanaryState.CANARY_5: {
        CanaryState.CANARY_1,
        CanaryState.CANARY_5,
        CanaryState.CANARY_20,
    },
    CanaryState.CANARY_20: {
        CanaryState.CANARY_5,
        CanaryState.CANARY_20,
        CanaryState.FULL_TRUSTED,
    },
    CanaryState.FULL_TRUSTED: {
        CanaryState.CANARY_20,
        CanaryState.FULL_TRUSTED,
    },
    CanaryState.ROLLBACK: {CanaryState.ROLLBACK, CanaryState.LEGACY_ONLY},
}

_FORWARD_ORDER: dict[CanaryState, int] = {
    CanaryState.LEGACY_ONLY: 0,
    CanaryState.SHADOW_ONLY: 1,
    CanaryState.CANARY_1: 2,
    CanaryState.CANARY_5: 3,
    CanaryState.CANARY_20: 4,
    CanaryState.FULL_TRUSTED: 5,
    CanaryState.ROLLBACK: -1,
}


class TrustedRAGCanaryStateMachine:
    """Fail-closed reviewer with no production mutation capability."""

    def review(
        self,
        policy: CanaryPolicyProtocol,
        requested_state: CanaryState,
        observation: CanaryObservation | None = None,
    ) -> CanaryReviewResult:
        rollback_reasons = self.rollback_reasons(policy, observation)
        exposed = policy.target_weight > 0.0
        if exposed and rollback_reasons:
            return self._result(
                policy.current_state,
                CanaryState.ROLLBACK,
                ReviewAction.ROLLBACK,
                rollback_reasons,
            )

        if requested_state not in _ALLOWED_TRANSITIONS[policy.current_state]:
            return self._hold(policy, "transition.not_allowed")

        requested_weight = STATE_WEIGHTS[requested_state]
        increases_exposure = requested_weight > policy.target_weight
        if increases_exposure and policy.quality_gate.status is not QualityGateStatus.PASS_READY:
            return self._hold(policy, "quality_gate.not_pass_ready")
        if increases_exposure and observation is None:
            return self._hold(policy, "observation.missing")
        if (
            increases_exposure
            and observation is not None
            and observation.sample_count
            < policy.rollback_condition.min_observation_samples
        ):
            return self._hold(policy, "observation.sample_count_below_minimum")
        if increases_exposure and rollback_reasons:
            return self._hold(policy, *rollback_reasons)

        action = (
            ReviewAction.PROMOTE
            if (
                policy.current_state is not CanaryState.ROLLBACK
                and _FORWARD_ORDER[requested_state]
                > _FORWARD_ORDER[policy.current_state]
            )
            else ReviewAction.HOLD
        )
        return self._result(policy.current_state, requested_state, action, [])

    @staticmethod
    def rollback_reasons(
        policy: CanaryPolicyProtocol,
        observation: CanaryObservation | None,
    ) -> list[str]:
        if observation is None:
            return []
        thresholds = policy.rollback_condition
        reasons: list[str] = []
        if observation.error_rate > thresholds.max_error_rate:
            reasons.append("rollback.error_rate_exceeded")
        if observation.timeout_rate > thresholds.max_timeout_rate:
            reasons.append("rollback.timeout_rate_exceeded")
        if observation.latency_p95_ms > thresholds.max_latency_p95_ms:
            reasons.append("rollback.latency_p95_exceeded")
        for metric in (
            "relevance",
            "support_rate",
            "completeness",
            "source_coverage",
        ):
            regression = getattr(observation.quality_baseline, metric) - getattr(
                observation.quality, metric
            )
            if regression > thresholds.max_quality_regression:
                reasons.append(f"rollback.quality_regression:{metric}")
        return reasons

    @staticmethod
    def _hold(policy: CanaryPolicyProtocol, *reasons: str) -> CanaryReviewResult:
        return CanaryReviewResult(
            previous_state=policy.current_state,
            next_state=policy.current_state,
            target_weight=policy.target_weight,
            action=ReviewAction.HOLD,
            reasons=list(reasons),
        )

    @staticmethod
    def _result(
        previous: CanaryState,
        next_state: CanaryState,
        action: ReviewAction,
        reasons: list[str],
    ) -> CanaryReviewResult:
        return CanaryReviewResult(
            previous_state=previous,
            next_state=next_state,
            target_weight=STATE_WEIGHTS[next_state],
            action=action,
            reasons=reasons,
        )
