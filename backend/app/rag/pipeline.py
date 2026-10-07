"""Trusted Retrieval Pipeline统一入口（TASK-003-B2）。

本阶段只建立协议、流程和旧Retriever适配，不改变现有检索算法。
"""
from __future__ import annotations

import re
import logging
import time
from typing import Any, Protocol

from app.rag.protocol import EvidenceItem, QueryPlan, RAGRequest, RAGResponse, TrustReport
from app.rag.retriever import get_retriever
from app.rag.text_quality import validate_text_quality

_KEYWORD_RE = re.compile(r"[A-Za-z0-9_+#.-]+|[一-鿿]+")
logger = logging.getLogger("app.rag.pipeline")


class Retriever(Protocol):
    """兼容现有HybridRetriever的最小检索接口。"""

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


class QueryPlanner(Protocol):
    def plan(self, request: RAGRequest) -> QueryPlan: ...


class BasicQueryPlanner:
    """零LLM的基础规划器；后续可替换为Query理解组件。"""

    def plan(self, request: RAGRequest) -> QueryPlan:
        filters: dict[str, Any] = {}
        if request.knowledge_scope is not None:
            filters["knowledge_scope"] = request.knowledge_scope
        keywords = list(dict.fromkeys(_KEYWORD_RE.findall(request.query.lower())))
        return QueryPlan(intent="retrieve", keywords=keywords, filters=filters)


class TrustedRetrievalPipeline:
    """Request → QueryPlan → Retriever → Evidence → Response。"""

    schema_version = "rag-response-v1"

    def __init__(
        self,
        retriever: Retriever | None = None,
        planner: QueryPlanner | None = None,
    ) -> None:
        self.retriever = retriever or get_retriever()
        self.planner = planner or BasicQueryPlanner()

    @staticmethod
    def _score(candidate: dict[str, Any]) -> float:
        raw = candidate.get("score")
        if raw is None:
            raw = candidate.get("confidence_score")
        if raw is None:
            raw = candidate.get("confidenceScore")
        if raw is None:
            raw = candidate.get("fusion_score")
        if raw is None:
            raw = candidate.get("rrfScore")
        if raw is None:
            raw = candidate.get("vectorScore", 0.0)
        return round(max(0.0, min(1.0, float(raw))), 4)

    @classmethod
    def _evidence(cls, candidate: dict[str, Any]) -> EvidenceItem:
        content = str(candidate.get("content") or "")
        validate_text_quality(content, context="Evidence构建 ")
        metadata = dict(candidate.get("metadata") or {})
        source = {
            "chunkId": candidate.get("id"),
            "documentId": metadata.get("document_id") or metadata.get("docId"),
            "title": metadata.get("document_title") or metadata.get("title"),
            "location": metadata.get("source_location"),
        }
        source = {key: value for key, value in source.items() if value is not None}
        retrieval_metadata = {
            **metadata,
            "vectorScore": candidate.get("vectorScore"),
            "bm25Score": candidate.get("bm25Score"),
            "rrfScore": candidate.get("rrfScore"),
            "denseScore": candidate.get("dense_score"),
            "keywordScore": candidate.get("keyword_score"),
            "fusionScore": candidate.get("fusion_score"),
            "normalizedDenseScore": candidate.get("normalized_dense_score"),
            "normalizedKeywordScore": candidate.get("normalized_keyword_score"),
            "confidenceScore": candidate.get("confidence_score"),
        }
        retrieval_metadata = {
            key: value for key, value in retrieval_metadata.items() if value is not None
        }
        return EvidenceItem(
            content=content,
            source=source,
            score=cls._score(candidate),
            metadata=retrieval_metadata,
            score_breakdown={
                "denseScore": float(candidate.get("dense_score") or candidate.get("vectorScore") or 0.0),
                "keywordScore": float(candidate.get("keyword_score") or candidate.get("bm25Score") or 0.0),
                "normalizedDenseScore": float(candidate.get("normalized_dense_score") or 0.0),
                "normalizedKeywordScore": float(candidate.get("normalized_keyword_score") or 0.0),
                "fusionScore": float(candidate.get("fusion_score") or candidate.get("rrfScore") or 0.0),
                "confidenceScore": cls._score(candidate),
            },
        )

    @staticmethod
    def _reason_codes(
        *, evidence: list[EvidenceItem], filters: dict[str, Any]
    ) -> list[str]:
        reasons: list[str] = []
        if not evidence:
            reasons.extend(["NO_EVIDENCE", "LOW_SCORE"])
            if filters:
                reasons.append("FILTERED_RESULT")
            if filters.get("knowledge_scope") is not None:
                reasons.append("OUT_OF_SCOPE")
        source_ids = {
            item.source.get("documentId") for item in evidence if item.source.get("documentId")
        }
        if evidence and len(source_ids) <= 1:
            reasons.append("SINGLE_SOURCE")
        return reasons

    def execute(self, request: RAGRequest | dict[str, Any]) -> RAGResponse:
        total_started = time.perf_counter()
        validated_request = RAGRequest.model_validate(request)
        planning_started = time.perf_counter()
        plan = self.planner.plan(validated_request)
        planning_ms = (time.perf_counter() - planning_started) * 1000
        retrieval_started = time.perf_counter()
        try:
            candidates = self.retriever.search(
                validated_request.query,
                top_k=validated_request.top_k,
                filters=plan.filters,
            )
        except TypeError:
            # 兼容测试桩和旧适配器；默认HybridRetriever支持filters。
            candidates = self.retriever.search(
                validated_request.query,
                top_k=validated_request.top_k,
            )
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        evidence_started = time.perf_counter()
        evidence = [self._evidence(candidate) for candidate in candidates]
        source_ids = {
            item.source.get("documentId") for item in evidence if item.source.get("documentId")
        }
        reason_codes = self._reason_codes(evidence=evidence, filters=plan.filters)
        for item in evidence:
            item.reason_codes = list(reason_codes)
        confidence = max((item.score for item in evidence), default=0.0)
        evidence_ms = (time.perf_counter() - evidence_started) * 1000
        total_ms = (time.perf_counter() - total_started) * 1000
        observability = {
            "queryPlanningMs": round(planning_ms, 4),
            "retrievalMs": round(retrieval_ms, 4),
            "evidenceBuildMs": round(evidence_ms, 4),
            "totalMs": round(total_ms, 4),
            "candidateCount": len(candidates),
            "evidenceCount": len(evidence),
            "emptyResult": not evidence,
        }
        logger.info(
            "trusted_retrieval_completed planningMs=%.4f retrievalMs=%.4f "
            "evidenceMs=%.4f totalMs=%.4f candidates=%d evidence=%d empty=%s",
            planning_ms,
            retrieval_ms,
            evidence_ms,
            total_ms,
            len(candidates),
            len(evidence),
            not evidence,
        )
        return RAGResponse(
            evidence=evidence,
            confidence=confidence,
            evidence_count=len(evidence),
            source_count=len(source_ids),
            reason_codes=reason_codes,
            trust_report=TrustReport(
                retrieval_confidence=confidence,
                evidence_count=len(evidence),
                source_count=len(source_ids),
                coverage=(len(source_ids) / len(evidence)) if evidence else 0.0,
                reason_codes=reason_codes,
            ),
            metadata={
                "schemaVersion": self.schema_version,
                "queryPlan": plan.model_dump(mode="json"),
                "resultCount": len(evidence),
                "retriever": type(self.retriever).__name__,
                "confidenceSemantics": "heuristic_max_retrieval_score_not_probability",
                "retrievalDecision": "evidence_found" if evidence else "low_confidence",
                "lowConfidence": not evidence,
                "observability": observability,
            },
        )


_pipeline: TrustedRetrievalPipeline | None = None


def get_trusted_retrieval_pipeline() -> TrustedRetrievalPipeline:
    """未来RAG调用的统一默认入口。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = TrustedRetrievalPipeline()
    return _pipeline
