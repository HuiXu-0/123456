"""TASK-003-B2统一可信检索Pipeline测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.protocol import EvidenceItem, QueryPlan, RAGRequest, RAGResponse


def test_rag_protocol_schemas_are_strict():
    request = RAGRequest(
        query="反向传播",
        user_id="u_1",
        knowledge_scope=["deep-learning"],
        top_k=3,
    )
    assert request.top_k == 3
    assert QueryPlan(intent="retrieve", keywords=["反向传播"], filters={})
    assert EvidenceItem(content="证据", source={}, score=0.8, metadata={})
    assert RAGResponse(evidence=[], confidence=0.0, metadata={})
    with pytest.raises(ValidationError):
        RAGRequest(query="", user_id="u_1", top_k=5)
    with pytest.raises(ValidationError):
        RAGRequest(query="有效", user_id="u_1", top_k=5, unexpected=True)


class _LegacyRetriever:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.results[:top_k]


def test_pipeline_runs_plan_retrieval_evidence_response_flow():
    retriever = _LegacyRetriever(
        [
            {
                "id": "doc_1#0",
                "content": "反向传播通过链式法则计算梯度。",
                "metadata": {"document_id": "doc_1", "document_title": "深度学习", "source_location": "第3章"},
                "vectorScore": 0.91,
                "bm25Score": 2.4,
                "rrfScore": 0.72,
            }
        ]
    )
    response = TrustedRetrievalPipeline(retriever=retriever).execute(
        {"query": "反向传播 梯度", "user_id": "u_1", "knowledge_scope": "course-ai", "top_k": 2}
    )

    assert retriever.calls == [("反向传播 梯度", 2)]
    assert response.confidence == 0.72
    assert response.metadata["queryPlan"] == {
        "intent": "retrieve",
        "keywords": ["反向传播", "梯度"],
        "filters": {"knowledge_scope": "course-ai"},
    }
    assert response.metadata["resultCount"] == 1


def test_pipeline_builds_evidence_from_legacy_candidate():
    candidate = {
        "id": "doc_7#2",
        "content": "Transformer使用自注意力机制。",
        "metadata": {
            "document_id": "doc_7",
            "document_title": "Transformer教程",
            "source_location": "第2页",
            "category": "课程",
        },
        "score": 0.88,
        "rrfScore": 0.02,
    }
    response = TrustedRetrievalPipeline(
        retriever=_LegacyRetriever([candidate])
    ).execute(RAGRequest(query="Transformer", user_id="u_2", top_k=1))

    evidence = response.evidence[0]
    assert evidence.content == candidate["content"]
    assert evidence.score == 0.88
    assert evidence.source == {
        "chunkId": "doc_7#2",
        "documentId": "doc_7",
        "title": "Transformer教程",
        "location": "第2页",
    }
    assert evidence.metadata["category"] == "课程"
    assert evidence.metadata["rrfScore"] == 0.02


def test_pipeline_returns_valid_empty_response():
    response = TrustedRetrievalPipeline(
        retriever=_LegacyRetriever([])
    ).execute({"query": "不存在的知识", "user_id": "u_3", "top_k": 5})

    assert response.evidence == []
    assert response.confidence == 0.0
    assert response.metadata["resultCount"] == 0
    assert response.metadata["retrievalDecision"] == "low_confidence"
    assert response.metadata["lowConfidence"] is True
    assert response.evidence_count == response.source_count == 0
    assert response.reason_codes == ["NO_EVIDENCE", "LOW_SCORE"]
    assert response.trust_report is not None
    assert response.trust_report.retrieval_confidence == 0.0
    assert response.trust_report.coverage == 0.0


def test_pipeline_exposes_score_breakdown_counts_and_single_source_reason():
    candidate = {
        "id": "doc_1#0",
        "content": "反向传播使用链式法则。",
        "metadata": {"document_id": "doc_1", "document_title": "神经网络"},
        "dense_score": 0.8,
        "keyword_score": 4.0,
        "normalized_dense_score": 1.0,
        "normalized_keyword_score": 0.7,
        "fusion_score": 0.02,
        "confidence_score": 0.81,
    }
    response = TrustedRetrievalPipeline(
        retriever=_LegacyRetriever([candidate])
    ).execute({"query": "反向传播", "user_id": "u_1"})
    assert response.confidence == 0.81
    assert response.evidence_count == response.source_count == 1
    assert response.reason_codes == ["SINGLE_SOURCE"]
    assert response.trust_report is not None
    assert response.trust_report.coverage == 1.0
    evidence = response.evidence[0]
    assert evidence.reason_codes == ["SINGLE_SOURCE"]
    assert evidence.score_breakdown["confidenceScore"] == 0.81
    assert evidence.score_breakdown["normalizedDenseScore"] == 1.0


def test_empty_scoped_result_exposes_filter_and_scope_reason_codes():
    response = TrustedRetrievalPipeline(
        retriever=_LegacyRetriever([])
    ).execute(
        {
            "query": "范围外问题",
            "user_id": "u_1",
            "knowledge_scope": {"document_ids": ["doc_missing"]},
        }
    )
    assert response.reason_codes == [
        "NO_EVIDENCE", "LOW_SCORE", "FILTERED_RESULT", "OUT_OF_SCOPE"
    ]
