from __future__ import annotations

from app.rag.evaluation_protocol import RetrievalEvaluationCase
from app.rag.protocol import RetrievalCandidate
from app.rag.rerank_evaluation import FixedCandidateCase, evaluate_fixed_candidates
from app.rag.reranker import MockReranker


def _case() -> RetrievalEvaluationCase:
    return RetrievalEvaluationCase(
        case_id="rerank_case",
        query="test query",
        query_type="fact",
        expected_document_ids=["doc_a"],
        relevance=3,
        annotator="test",
        annotation_status="single_reviewed",
        evidence_basis="fixture",
        difficulty="easy",
    )


def _candidate(candidate_id: str, document_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=candidate_id,
        content=f"content {candidate_id}",
        source={"documentId": document_id},
        metadata={"document_id": document_id},
        confidence_score=score,
    )


def test_mock_reranker_preserves_order_and_score():
    candidates = [_candidate("a", "wrong", 0.9), _candidate("b", "doc_a", 0.8)]
    results = MockReranker().rerank("query", candidates)
    assert [result.candidate_id for result in results] == ["a", "b"]
    assert [result.original_rank for result in results] == [1, 2]
    assert [result.rerank_rank for result in results] == [1, 2]
    assert [result.rerank_score for result in results] == [0.9, 0.8]


def test_offline_mock_experiment_has_zero_metric_delta():
    fixed = FixedCandidateCase(
        _case(),
        (_candidate("a", "wrong", 0.9), _candidate("b", "doc_a", 0.8)),
    )
    report = evaluate_fixed_candidates([fixed], MockReranker())
    assert report["baseline"] == report["rerank"]
    assert report["delta"] == {"mrr": 0.0, "ndcg@3": 0.0, "ndcg@5": 0.0, "top1": 0.0}
    assert report["rankingChanges"] == []
    assert report["rankChange"] == {"changedCaseCount": 0, "changedCandidateCount": 0}
    assert report["queryTypeMetrics"]["fact"]["delta"]["mrr"] == 0.0
