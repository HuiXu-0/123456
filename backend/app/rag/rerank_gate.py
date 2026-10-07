"""Offline/shadow-only rerank gate protocol and decision simulator."""
from __future__ import annotations

from enum import Enum
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.rag.protocol import RetrievalCandidate
from app.rag.reranker import BaseReranker, MockReranker, RerankResult


class RerankPolicy(str, Enum):
    ALWAYS = "always"
    NEVER = "never"
    CONDITIONAL = "conditional"


class RerankDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    reason: str = Field(min_length=1)
    query_type: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class OfflineRerankGate:
    """Exploratory gate. It is intentionally not wired to production code."""

    def __init__(self, policy: RerankPolicy | str, *,
                 beneficial_query_types: frozenset[str] = frozenset({"概念解释"}),
                 min_confidence: float = 0.9883) -> None:
        self.policy = RerankPolicy(policy)
        self.beneficial_query_types = beneficial_query_types
        self.min_confidence = min_confidence

    def decide(self, query_type: str, confidence: float) -> RerankDecision:
        if self.policy is RerankPolicy.ALWAYS:
            return RerankDecision(enabled=True, reason="policy_always", query_type=query_type,
                                  confidence=confidence)
        if self.policy is RerankPolicy.NEVER:
            return RerankDecision(enabled=False, reason="policy_never_hybrid_fallback",
                                  query_type=query_type, confidence=confidence)
        if query_type not in self.beneficial_query_types:
            return RerankDecision(enabled=False, reason="query_type_not_validated",
                                  query_type=query_type, confidence=confidence)
        if confidence < self.min_confidence:
            return RerankDecision(enabled=False, reason="confidence_below_shadow_threshold",
                                  query_type=query_type, confidence=confidence)
        return RerankDecision(enabled=True, reason="validated_type_and_confidence",
                              query_type=query_type, confidence=confidence)


class DecisionReranker(BaseReranker):
    """Applies decisions around an offline reranker and records every shadow choice."""

    def __init__(self, delegate: BaseReranker, gate: OfflineRerankGate,
                 query_context: Mapping[str, tuple[str, float]]) -> None:
        self.delegate = delegate
        self.gate = gate
        self.query_context = query_context
        self.decisions: list[RerankDecision] = []
        self._fallback = MockReranker()

    def rerank(self, query: str,
               candidates: Sequence[RetrievalCandidate]) -> list[RerankResult]:
        query_type, confidence = self.query_context[query]
        decision = self.gate.decide(query_type, confidence)
        self.decisions.append(decision)
        if not decision.enabled:
            return self._fallback.rerank(query, candidates)
        try:
            return self.delegate.rerank(query, candidates)
        except Exception:  # noqa: BLE001 - shadow simulation must record safe degradation
            self.decisions[-1] = RerankDecision(
                enabled=False, reason="reranker_error_hybrid_fallback",
                query_type=query_type, confidence=confidence)
            return self._fallback.rerank(query, candidates)
