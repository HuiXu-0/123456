"""TASK-004-E4-C Trusted RAG Canary review state machine tests."""
from __future__ import annotations

import pytest

from app.rag.trusted_rag_canary import (
    CanaryObservation,
    CanaryPolicyProtocol,
    CanaryQualitySnapshot,
    CanaryState,
    QualityGateProtocol,
    QualityGateStatus,
    ReviewAction,
    RollbackCondition,
    STATE_WEIGHTS,
    TrustedRAGCanaryStateMachine,
)


def _quality(**overrides) -> CanaryQualitySnapshot:
    values = {
        "relevance": 0.70,
        "support_rate": 0.86,
        "completeness": 0.32,
        "source_coverage": 1.0,
    }
    values.update(overrides)
    return CanaryQualitySnapshot(**values)


def _observation(**overrides) -> CanaryObservation:
    values = {
        "sample_count": 100,
        "error_rate": 0.0,
        "timeout_rate": 0.0,
        "latency_p95_ms": 30.0,
        "quality": _quality(),
        "quality_baseline": _quality(),
    }
    values.update(overrides)
    return CanaryObservation(**values)


def _policy(
    state: CanaryState,
    *,
    gate: QualityGateStatus = QualityGateStatus.PASS_READY,
) -> CanaryPolicyProtocol:
    return CanaryPolicyProtocol(
        current_state=state,
        target_weight=STATE_WEIGHTS[state],
        quality_gate=QualityGateProtocol(
            status=gate,
            gate_version="quality-gate-v2",
            block_reasons=[] if gate is QualityGateStatus.PASS_READY else ["quality.blocked"],
        ),
        rollback_condition=RollbackCondition(),
    )


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (CanaryState.LEGACY_ONLY, CanaryState.SHADOW_ONLY),
        (CanaryState.SHADOW_ONLY, CanaryState.CANARY_1),
        (CanaryState.CANARY_1, CanaryState.CANARY_5),
        (CanaryState.CANARY_5, CanaryState.CANARY_20),
        (CanaryState.CANARY_20, CanaryState.FULL_TRUSTED),
    ],
)
def test_state_promotions_follow_the_review_path(current, requested):
    result = TrustedRAGCanaryStateMachine().review(
        _policy(current), requested, _observation()
    )

    assert result.next_state is requested
    assert result.target_weight == STATE_WEIGHTS[requested]
    assert result.action is ReviewAction.PROMOTE
    assert result.reasons == []


def test_skipping_a_canary_stage_is_blocked():
    result = TrustedRAGCanaryStateMachine().review(
        _policy(CanaryState.SHADOW_ONLY),
        CanaryState.CANARY_20,
        _observation(),
    )

    assert result.next_state is CanaryState.SHADOW_ONLY
    assert result.action is ReviewAction.HOLD
    assert result.reasons == ["transition.not_allowed"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"error_rate": 0.021}, "rollback.error_rate_exceeded"),
        ({"timeout_rate": 0.011}, "rollback.timeout_rate_exceeded"),
        ({"latency_p95_ms": 1500.01}, "rollback.latency_p95_exceeded"),
        (
            {"quality": _quality(relevance=0.64)},
            "rollback.quality_regression:relevance",
        ),
    ],
)
def test_active_canary_automatically_rolls_back_on_threshold_breach(overrides, reason):
    result = TrustedRAGCanaryStateMachine().review(
        _policy(CanaryState.CANARY_5),
        CanaryState.CANARY_20,
        _observation(**overrides),
    )

    assert result.next_state is CanaryState.ROLLBACK
    assert result.target_weight == 0.0
    assert result.action is ReviewAction.ROLLBACK
    assert reason in result.reasons


def test_gate_blocks_exposure_and_missing_observation_fails_closed():
    machine = TrustedRAGCanaryStateMachine()
    blocked = machine.review(
        _policy(CanaryState.SHADOW_ONLY, gate=QualityGateStatus.BLOCK),
        CanaryState.CANARY_1,
        _observation(),
    )
    missing = machine.review(
        _policy(CanaryState.SHADOW_ONLY), CanaryState.CANARY_1
    )

    assert blocked.next_state is CanaryState.SHADOW_ONLY
    assert blocked.reasons == ["quality_gate.not_pass_ready"]
    assert missing.next_state is CanaryState.SHADOW_ONLY
    assert missing.reasons == ["observation.missing"]


def test_policy_rejects_a_weight_that_does_not_match_its_state():
    with pytest.raises(ValueError, match="target_weight must be 0.05"):
        CanaryPolicyProtocol(
            current_state=CanaryState.CANARY_5,
            target_weight=0.20,
            quality_gate=QualityGateProtocol(
                status=QualityGateStatus.PASS_READY,
                gate_version="quality-gate-v2",
            ),
        )


def test_rollback_must_recover_through_legacy_only():
    result = TrustedRAGCanaryStateMachine().review(
        _policy(CanaryState.ROLLBACK), CanaryState.LEGACY_ONLY
    )

    assert result.next_state is CanaryState.LEGACY_ONLY
    assert result.target_weight == 0.0
    assert result.action is ReviewAction.HOLD
