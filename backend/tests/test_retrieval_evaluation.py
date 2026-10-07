"""TASK-003-C1检索评测、影子对比与可观测性测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.evaluation import (
    RetrievalEvaluator,
    calculate_case_metrics,
    load_evaluation_cases,
    validate_evaluation_dataset,
)
from app.rag.evaluation_protocol import RetrievalEvaluationCase
from app.rag.pipeline import TrustedRetrievalPipeline


def _case(**updates):
    data = {
        "case_id": "case_1",
        "query": "测试查询",
        "query_type": "概念解释",
        "expected_document_ids": ["doc_a"],
        "expected_chunk_ids": [],
        "expected_keywords": ["测试"],
        "expected_evidence": [],
        "knowledge_scope": None,
        "relevance": 3,
        "notes": "fixture",
        "annotator": "tester",
        "annotation_status": "pending_second_review",
        "evidence_basis": "unit fixture",
        "difficulty": "easy",
        "second_annotator": None,
    }
    data.update(updates)
    return RetrievalEvaluationCase.model_validate(data)


def _item(chunk, doc, score=0.8, complete=True):
    metadata = {"document_id": doc}
    if complete:
        metadata["document_title"] = doc
    return {"id": chunk, "content": chunk, "metadata": metadata, "score": score}


def test_metric_calculation_for_ranked_results():
    metrics = calculate_case_metrics(
        _case(),
        [_item("x#0", "wrong"), _item("doc_a#0", "doc_a")],
    )
    assert metrics["recall@1"] == 0.0
    assert metrics["recall@3"] == 1.0
    assert metrics["hit_rate@1"] == 0.0
    assert metrics["mrr"] == 0.5
    assert 0.0 < metrics["ndcg@3"] < 1.0


def test_empty_no_answer_metrics_are_not_fabricated():
    case = _case(
        expected_document_ids=[],
        expected_chunk_ids=[],
        expected_evidence=[],
        relevance=0,
        query_type="无答案问题",
    )
    metrics = calculate_case_metrics(case, [])
    assert metrics["recall@1"] is None
    assert metrics["mrr"] is None


def test_empty_result_does_not_fabricate_incomplete_source_fields():
    from app.rag.evaluation import source_completeness, source_coverage

    assert source_completeness([]) == 1.0
    assert source_coverage([]) == 1.0


def test_multiple_relevant_documents_reward_complete_ranking():
    case = _case(expected_document_ids=["doc_a", "doc_b"])
    metrics = calculate_case_metrics(
        case,
        [_item("doc_a#0", "doc_a"), _item("doc_b#0", "doc_b")],
    )
    assert metrics["recall@1"] == 0.5
    assert metrics["recall@3"] == 1.0
    assert metrics["ndcg@3"] == pytest.approx(1.0)


class _Retriever:
    def __init__(self, items):
        self.items = items

    def search(self, query, top_k=5):
        return self.items[:top_k]


def test_old_new_shadow_comparison_reports_differences():
    old = [_item("a#0", "doc_a", 0.7), _item("b#0", "doc_b", 0.6)]
    new = [_item("b#0", "doc_b", 0.9), _item("c#0", "doc_c", 0.5)]
    diff = RetrievalEvaluator.shadow_compare("c1", old, new, 2.0, 3.5, k=2)
    assert diff["oldOnlyDocuments"] == ["doc_a"]
    assert diff["newOnlyDocuments"] == ["doc_c"]
    assert diff["rankingDifferences"]["b#0"] == -1
    assert diff["scoreDifferences"]["b#0"] == pytest.approx(0.3)
    assert diff["latencyDifferenceMs"] == 1.5


def test_evaluator_invokes_old_and_new_and_records_empty_results():
    empty_old = _Retriever([])
    empty_new = _Retriever([])
    report = RetrievalEvaluator(
        empty_old,
        TrustedRetrievalPipeline(retriever=empty_new),
    ).evaluate([_case()])
    assert report.case_count == 1
    assert len(report.results) == 2
    assert all(result.empty_result for result in report.results)
    assert report.systems["hybrid_retriever"]["empty_result_rate"] == 1.0
    assert report.systems["trusted_pipeline"]["empty_result_rate"] == 1.0
    assert report.shadow_differences[0]["overlapAtK"] == 0.0


def test_pipeline_records_stage_observability(caplog):
    pipeline = TrustedRetrievalPipeline(retriever=_Retriever([_item("a#0", "doc_a")]))
    with caplog.at_level("INFO", logger="app.rag.pipeline"):
        response = pipeline.execute({"query": "测试", "user_id": "sensitive-user", "top_k": 5})
    observed = response.metadata["observability"]
    assert set(observed) == {
        "queryPlanningMs", "retrievalMs", "evidenceBuildMs", "totalMs",
        "candidateCount", "evidenceCount", "emptyResult",
    }
    assert observed["candidateCount"] == observed["evidenceCount"] == 1
    assert observed["emptyResult"] is False
    assert "sensitive-user" not in caplog.text
    assert "a#0" not in caplog.text


def test_evaluation_dataset_format_and_required_query_types():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "retrieval_cases.json"
    cases = load_evaluation_cases(path)
    assert len(cases) >= 40
    assert {case.query_type for case in cases} == {
        "概念解释", "方法比较", "操作步骤", "事实查询", "跨段落综合",
        "模糊表达", "无答案问题", "带knowledge_scope过滤",
    }
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.notes for case in cases)
    assert all(case.annotator and case.evidence_basis for case in cases)
    assert all(case.annotation_status == "pending_second_review" for case in cases)


def test_dataset_rejects_stale_document_and_chunk_ids():
    cases = [_case(expected_document_ids=["doc_missing"])]
    with pytest.raises(ValueError, match="失效document ID"):
        validate_evaluation_dataset(
            cases,
            available_document_ids={"doc_a"},
            available_chunk_ids={"doc_a#0"},
        )

    stale_chunk = [_case(expected_document_ids=[], expected_chunk_ids=["doc_a#99"])]
    with pytest.raises(ValueError, match="失效chunk ID"):
        validate_evaluation_dataset(
            stale_chunk,
            available_document_ids={"doc_a"},
            available_chunk_ids={"doc_a#0"},
        )


def test_dataset_rejects_duplicate_and_conflicting_queries():
    duplicate = [_case(case_id="a"), _case(case_id="b")]
    with pytest.raises(ValueError, match="重复query"):
        validate_evaluation_dataset(duplicate)
    conflict = [
        _case(case_id="a"),
        _case(case_id="b", expected_document_ids=["doc_b"]),
    ]
    with pytest.raises(ValueError, match="冲突标注"):
        validate_evaluation_dataset(conflict)
