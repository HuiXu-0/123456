"""Retrieval-time quality protection for Trusted RAG Evidence.

The gate is deliberately local to retrieval.  It neither changes the API nor
routes traffic; it only decides whether the fused candidates need the
deterministic keyword fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceQualityAssessment:
    passed: bool
    relevance: float
    source_count: int
    support: bool
    reasons: tuple[str, ...]


class EvidenceQualityGate:
    """Protect the first three Evidence items before they leave retrieval."""

    def __init__(self, *, min_relevance: float = 0.90) -> None:
        if not 0.0 <= min_relevance <= 1.0:
            raise ValueError("min_relevance must be between 0 and 1")
        self.min_relevance = min_relevance

    @staticmethod
    def _relative_keyword_score(candidate: dict[str, Any]) -> float:
        metadata = candidate.get("metadata") or {}
        value = metadata.get("keywordRelativeScore", 0.0)
        return max(0.0, min(1.0, float(value or 0.0)))

    def evaluate(self, candidates: list[dict[str, Any]]) -> EvidenceQualityAssessment:
        protected = candidates[:3]
        if not protected:
            return EvidenceQualityAssessment(False, 0.0, 0, False, ("relevance", "source_count", "support"))

        relative_scores = [self._relative_keyword_score(item) for item in protected]
        relevance = round(sum(relative_scores) / len(relative_scores), 6)
        source_ids = {
            str((item.get("source") or {}).get("documentId") or
                (item.get("metadata") or {}).get("document_id") or "")
            for item in protected
        }
        source_ids.discard("")
        source_count = len(source_ids)
        support = all(
            float(item.get("keyword_score") or item.get("bm25Score") or 0.0) > 0.0
            and score >= self.min_relevance
            for item, score in zip(protected, relative_scores)
        )
        reasons: list[str] = []
        if relevance < self.min_relevance:
            reasons.append("relevance")
        if source_count < len(protected):
            reasons.append("source_count")
        if not support:
            reasons.append("support")
        return EvidenceQualityAssessment(
            passed=not reasons,
            relevance=relevance,
            source_count=source_count,
            support=support,
            reasons=tuple(reasons),
        )
