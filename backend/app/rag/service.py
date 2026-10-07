"""Agent-facing adapter for the trusted retrieval pipeline (TASK-003-E1).

The adapter deliberately keeps rerank-gate evaluation in shadow mode: a gate
decision is observable, but it never changes the pipeline response ordering.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Protocol

from app.core.envelope import current_trace_id
from app.rag.pipeline import get_trusted_retrieval_pipeline
from app.rag.protocol import RAGRequest, RAGResponse
from app.rag.rerank_gate import OfflineRerankGate

logger = logging.getLogger("app.rag.service")


class RetrievalPipeline(Protocol):
    def execute(self, request: RAGRequest) -> RAGResponse: ...


QueryTypeResolver = Callable[[RAGRequest], str]


def _unclassified_query_type(_request: RAGRequest) -> str:
    """Safe default until a dedicated query classifier is introduced."""
    return "unclassified"


class RAGService:
    """Stable service boundary for future Agent access to trusted retrieval."""

    def __init__(
        self,
        pipeline: RetrievalPipeline | None = None,
        *,
        trace_id: str | None = None,
        rerank_shadow_gate: OfflineRerankGate | None = None,
        query_type_resolver: QueryTypeResolver | None = None,
    ) -> None:
        self.pipeline = pipeline or get_trusted_retrieval_pipeline()
        self.trace_id = trace_id or current_trace_id() or f"rag_{uuid.uuid4().hex[:12]}"
        self.rerank_shadow_gate = rerank_shadow_gate
        self.query_type_resolver = query_type_resolver or _unclassified_query_type

    def retrieve(self, request: RAGRequest) -> RAGResponse:
        """Return trusted evidence without logging query or evidence content."""
        total_started = time.perf_counter()
        validated_request = RAGRequest.model_validate(request)

        pipeline_started = time.perf_counter()
        response = self.pipeline.execute(validated_request)
        pipeline_ms = (time.perf_counter() - pipeline_started) * 1000

        shadow_started = time.perf_counter()
        shadow_metadata: dict[str, object] = {"mode": "disabled", "applied": False}
        if self.rerank_shadow_gate is not None:
            query_type = self.query_type_resolver(validated_request)
            decision = self.rerank_shadow_gate.decide(query_type, response.confidence)
            shadow_metadata = {
                "mode": "shadow",
                "applied": False,
                "enabled": decision.enabled,
                "reason": decision.reason,
                "queryType": decision.query_type,
                "confidence": decision.confidence,
            }
        shadow_ms = (time.perf_counter() - shadow_started) * 1000
        total_ms = (time.perf_counter() - total_started) * 1000

        response.metadata["traceId"] = self.trace_id
        response.metadata["rerankShadow"] = shadow_metadata
        response.metadata["serviceObservability"] = {
            "pipelineMs": round(pipeline_ms, 4),
            "rerankShadowMs": round(shadow_ms, 4),
            "totalMs": round(total_ms, 4),
        }
        logger.info(
            "rag_service_retrieve_completed traceId=%s pipelineMs=%.4f "
            "shadowMs=%.4f totalMs=%.4f evidence=%d shadowMode=%s",
            self.trace_id,
            pipeline_ms,
            shadow_ms,
            total_ms,
            response.evidence_count,
            shadow_metadata["mode"],
        )
        return response


def get_rag_service() -> RAGService:
    """Create an adapter bound to the current request trace context."""
    return RAGService()
