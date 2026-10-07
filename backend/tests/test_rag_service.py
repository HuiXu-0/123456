"""TASK-003-E1 Trusted RAG Service Adapter tests."""
from __future__ import annotations

from app.rag.protocol import EvidenceItem, RAGRequest, RAGResponse, TrustReport
from app.rag.rerank_gate import OfflineRerankGate
from app.rag.service import RAGService


class _PipelineStub:
    def __init__(self, response: RAGResponse) -> None:
        self.response = response
        self.calls: list[RAGRequest] = []

    def execute(self, request: RAGRequest) -> RAGResponse:
        self.calls.append(request)
        return self.response


def _response() -> RAGResponse:
    evidence = EvidenceItem(
        content="反向传播通过链式法则计算梯度。",
        source={"documentId": "doc-1"},
        score=0.99,
    )
    trust_report = TrustReport(
        retrieval_confidence=0.99,
        evidence_count=1,
        source_count=1,
        coverage=1.0,
    )
    return RAGResponse(
        evidence=[evidence],
        confidence=0.99,
        evidence_count=1,
        source_count=1,
        trust_report=trust_report,
    )


def test_service_calls_trusted_pipeline_and_preserves_trace_id():
    pipeline = _PipelineStub(_response())
    request = RAGRequest(query="反向传播", user_id="user-1", top_k=3)

    result = RAGService(pipeline=pipeline, trace_id="trace-e1").retrieve(request)

    assert pipeline.calls == [request]
    assert result.metadata["traceId"] == "trace-e1"
    timings = result.metadata["serviceObservability"]
    assert set(timings) == {"pipelineMs", "rerankShadowMs", "totalMs"}
    assert all(value >= 0 for value in timings.values())


def test_service_returns_pipeline_evidence_unchanged():
    expected = _response()
    result = RAGService(pipeline=_PipelineStub(expected)).retrieve(
        RAGRequest(query="链式法则", user_id="user-2")
    )

    assert result.evidence == expected.evidence
    assert result.evidence[0].source["documentId"] == "doc-1"


def test_service_returns_pipeline_trust_report():
    expected = _response()
    result = RAGService(pipeline=_PipelineStub(expected)).retrieve(
        RAGRequest(query="可信度", user_id="user-3")
    )

    assert result.trust_report is expected.trust_report
    assert result.trust_report.retrieval_confidence == 0.99
    assert result.trust_report.coverage == 1.0


def test_rerank_gate_shadow_is_observable_but_does_not_change_evidence():
    expected = _response()
    gate = OfflineRerankGate("conditional", min_confidence=0.9883)
    service = RAGService(
        pipeline=_PipelineStub(expected),
        rerank_shadow_gate=gate,
        query_type_resolver=lambda _request: "概念解释",
    )

    result = service.retrieve(RAGRequest(query="什么是反向传播", user_id="user-4"))

    assert result.evidence == expected.evidence
    assert result.metadata["rerankShadow"] == {
        "mode": "shadow",
        "applied": False,
        "enabled": True,
        "reason": "validated_type_and_confidence",
        "queryType": "概念解释",
        "confidence": 0.99,
    }


def test_service_logs_no_query_or_evidence_content(caplog):
    with caplog.at_level("INFO", logger="app.rag.service"):
        RAGService(pipeline=_PipelineStub(_response()), trace_id="trace-safe").retrieve(
            RAGRequest(query="敏感查询正文", user_id="sensitive-user")
        )

    assert "敏感查询正文" not in caplog.text
    assert "反向传播通过链式法则计算梯度" not in caplog.text
    assert "sensitive-user" not in caplog.text
    assert "trace-safe" in caplog.text
