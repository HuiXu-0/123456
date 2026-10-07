from app.rag.rerank_gate import OfflineRerankGate, RerankPolicy
from app.rag.rerank_gate import DecisionReranker
from app.rag.reranker import BaseReranker


def test_always_and_never_policies_are_explicit():
    assert OfflineRerankGate(RerankPolicy.ALWAYS).decide("操作步骤", 0.1).enabled is True
    decision = OfflineRerankGate(RerankPolicy.NEVER).decide("概念解释", 1.0)
    assert decision.enabled is False
    assert decision.reason == "policy_never_hybrid_fallback"


def test_conditional_policy_requires_validated_type_and_confidence():
    gate = OfflineRerankGate("conditional", min_confidence=0.9883)
    assert gate.decide("概念解释", 0.99).enabled is True
    assert gate.decide("概念解释", 0.98).reason == "confidence_below_shadow_threshold"
    assert gate.decide("方法比较", 1.0).reason == "query_type_not_validated"


class _FailingReranker(BaseReranker):
    def rerank(self, query, candidates):
        raise RuntimeError("offline fixture failure")


def test_delegate_failure_degrades_to_hybrid_order():
    controlled = DecisionReranker(
        _FailingReranker(), OfflineRerankGate("always"), {"q": ("概念解释", 1.0)})
    assert controlled.rerank("q", []) == []
    assert controlled.decisions[0].enabled is False
    assert controlled.decisions[0].reason == "reranker_error_hybrid_fallback"
