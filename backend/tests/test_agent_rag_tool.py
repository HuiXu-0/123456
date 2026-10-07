"""TASK-003-E2 Agent-side Trusted RAG Tool tests."""
from __future__ import annotations

import pytest

from app.agents.tools.rag_tool import AgentRAGPort, RAGTool, RAGToolError
from app.rag.protocol import EvidenceItem, RAGRequest, RAGResponse, TrustReport


class _ServiceStub:
    def __init__(self, response: RAGResponse, *, trace_id: str = "trace-e2") -> None:
        self.response = response
        self.trace_id = trace_id
        self.calls: list[RAGRequest] = []

    def retrieve(self, request: RAGRequest) -> RAGResponse:
        self.calls.append(request)
        return self.response


class _FailingService:
    trace_id = "trace-failed"

    def retrieve(self, request: RAGRequest) -> RAGResponse:
        raise RuntimeError("backend failure containing sensitive query: " + request.query)


def _response(trace_id: str = "trace-e2") -> RAGResponse:
    evidence = EvidenceItem(
        content="卷积核可以提取局部特征。",
        source={"documentId": "doc-cnn"},
        score=0.91,
    )
    report = TrustReport(
        retrieval_confidence=0.91,
        evidence_count=1,
        source_count=1,
        coverage=1.0,
        reason_codes=["SINGLE_SOURCE"],
    )
    return RAGResponse(
        evidence=[evidence],
        confidence=0.91,
        evidence_count=1,
        source_count=1,
        reason_codes=["SINGLE_SOURCE"],
        trust_report=report,
        metadata={
            "traceId": trace_id,
            "observability": {"retrievalMs": 1.2},
            "serviceObservability": {"totalMs": 1.5},
        },
    )


def test_tool_implements_agent_port_and_calls_service():
    service = _ServiceStub(_response())
    tool = RAGTool(" user-1 ", service=service)

    result = tool.search("卷积神经网络", {"course": "ai"}, 3)

    assert isinstance(tool, AgentRAGPort)
    assert result is service.response
    assert service.calls == [
        RAGRequest(
            query="卷积神经网络",
            user_id="user-1",
            knowledge_scope={"course": "ai"},
            top_k=3,
        )
    ]


def test_tool_passes_response_and_observability_through():
    expected = _response()
    result = RAGTool("user-2", service=_ServiceStub(expected)).search("CNN")

    assert result is expected
    assert result.evidence[0].content == "卷积核可以提取局部特征。"
    assert result.metadata["observability"] == {"retrievalMs": 1.2}
    assert result.metadata["serviceObservability"] == {"totalMs": 1.5}


def test_tool_preserves_trust_report():
    expected = _response()
    result = RAGTool("user-3", service=_ServiceStub(expected)).search("局部特征")

    assert result.trust_report is expected.trust_report
    assert result.trust_report.retrieval_confidence == 0.91
    assert result.trust_report.reason_codes == ["SINGLE_SOURCE"]


def test_tool_and_response_trace_ids_are_consistent():
    tool = RAGTool("user-4", service=_ServiceStub(_response("trace-shared"), trace_id="trace-shared"))

    result = tool.search("trace")

    assert tool.trace_id == result.metadata["traceId"] == "trace-shared"


def test_tool_wraps_errors_without_exposing_query(caplog):
    tool = RAGTool("private-user", service=_FailingService())

    with caplog.at_level("ERROR", logger="app.agents.tools.rag"):
        with pytest.raises(RAGToolError) as caught:
            tool.search("敏感知识正文")

    assert caught.value.trace_id == "trace-failed"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "敏感知识正文" not in str(caught.value)
    assert "敏感知识正文" not in caplog.text
    assert "private-user" not in caplog.text


def test_tool_rejects_mismatched_response_trace_id():
    tool = RAGTool("user-5", service=_ServiceStub(_response("trace-other")))

    with pytest.raises(RAGToolError) as caught:
        tool.search("trace mismatch")

    assert isinstance(caught.value.__cause__, ValueError)
