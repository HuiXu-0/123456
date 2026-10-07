"""Agent-side port and adapter for Trusted RAG (TASK-003-E2)."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Protocol, runtime_checkable

from app.core.envelope import current_trace_id
from app.rag.protocol import RAGRequest, RAGResponse
from app.rag.service import RAGService

logger = logging.getLogger("app.agents.tools.rag")


class RAGServicePort(Protocol):
    """The only downstream capability required by the concrete adapter."""

    trace_id: str

    def retrieve(self, request: RAGRequest) -> RAGResponse: ...


@runtime_checkable
class AgentRAGPort(Protocol):
    """Minimal RAG capability exposed to Agents."""

    def search(
        self,
        query: str,
        knowledge_scope: str | list[str] | dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> RAGResponse: ...


class RAGToolError(RuntimeError):
    """Sanitized Agent-facing retrieval error with a correlation trace."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(f"Trusted RAG search failed (traceId={trace_id})")


class RAGTool:
    """Translate the Agent search contract into a trusted RAG request."""

    def __init__(
        self,
        user_id: str,
        *,
        service: RAGServicePort | None = None,
        trace_id: str | None = None,
    ) -> None:
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id must not be blank")
        if service is not None and trace_id is not None and service.trace_id != trace_id:
            raise ValueError("service trace_id does not match tool trace_id")

        self.user_id = normalized_user_id
        self.trace_id = (
            service.trace_id
            if service is not None
            else trace_id or current_trace_id() or f"ragtool_{uuid.uuid4().hex[:12]}"
        )
        self._service = service or RAGService(trace_id=self.trace_id)

    def search(
        self,
        query: str,
        knowledge_scope: str | list[str] | dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> RAGResponse:
        """Search trusted evidence without exposing lower-level RAG components."""
        started = time.perf_counter()
        try:
            request = RAGRequest(
                query=query,
                user_id=self.user_id,
                knowledge_scope=knowledge_scope,
                top_k=top_k,
            )
            response = self._service.retrieve(request)
            response = RAGResponse.model_validate(response)
            response_trace_id = response.metadata.get("traceId")
            if response_trace_id != self.trace_id:
                raise ValueError("RAG response traceId does not match tool traceId")
            if response.trust_report is None:
                raise ValueError("RAG response is missing trust_report")
            for key in ("observability", "serviceObservability"):
                if not isinstance(response.metadata.get(key), dict):
                    raise ValueError(f"RAG response is missing {key}")
        except Exception as exc:
            # Do not attach traceback: provider exception messages may contain query text.
            logger.error(
                "agent_rag_tool_failed traceId=%s errorType=%s",
                self.trace_id,
                type(exc).__name__,
            )
            raise RAGToolError(self.trace_id) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "agent_rag_tool_completed traceId=%s totalMs=%.4f evidence=%d",
            self.trace_id,
            elapsed_ms,
            response.evidence_count,
        )
        return response
