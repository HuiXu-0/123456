"""TASK-003-D3-A 离线 Rerank 实验框架；不接入生产 Pipeline。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.rag.evaluation import calculate_case_metrics
from app.rag.evaluation_protocol import RetrievalEvaluationCase
from app.rag.protocol import RetrievalCandidate
from app.rag.reranker import BaseReranker, RerankResult


@dataclass(frozen=True)
class FixedCandidateCase:
    case: RetrievalEvaluationCase
    candidates: tuple[RetrievalCandidate, ...]

    def __post_init__(self) -> None:
        if len(self.candidates) > 20:
            raise ValueError("固定候选集最多允许 Hybrid Retrieval Top20")
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.case.case_id} 固定候选集存在重复 candidate_id")


def candidate_from_dict(item: dict[str, Any]) -> RetrievalCandidate:
    """将 HybridRetriever 兼容字典冻结为统一候选协议。"""
    return RetrievalCandidate(
        id=str(item["id"]),
        content=str(item["content"]),
        source=dict(item.get("source") or {}),
        metadata=dict(item.get("metadata") or {}),
        dense_score=float(item.get("dense_score", item.get("vectorScore", 0.0))),
        keyword_score=float(item.get("keyword_score", item.get("bm25Score", 0.0))),
        normalized_dense_score=float(item.get("normalized_dense_score", 0.0)),
        normalized_keyword_score=float(item.get("normalized_keyword_score", 0.0)),
        fusion_score=float(item.get("fusion_score", item.get("rrfScore", 0.0))),
        confidence_score=float(item.get("confidence_score", item.get("confidenceScore", 0.0))),
    )


def _metric_item(candidate: RetrievalCandidate, score: float) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "content": candidate.content,
        "source": candidate.source,
        "metadata": candidate.metadata,
        "score": score,
    }


def _mean(rows: Iterable[dict[str, float | None]], key: str) -> float:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


def evaluate_fixed_candidates(
    fixed_cases: Sequence[FixedCandidateCase],
    reranker: BaseReranker,
) -> dict[str, Any]:
    """比较固定候选集的 Baseline 与 Rerank 排序。"""
    baseline_rows: list[dict[str, float | None]] = []
    rerank_rows: list[dict[str, float | None]] = []
    changes: list[dict[str, Any]] = []
    per_type: dict[str, dict[str, list[dict[str, float | None]]]] = {}

    for fixed in fixed_cases:
        candidates = list(fixed.candidates)
        results = reranker.rerank(fixed.case.query, candidates)
        _validate_results(candidates, results)
        by_id = {candidate.id: candidate for candidate in candidates}
        ranked = sorted(results, key=lambda result: result.rerank_rank)
        baseline_items = [
            _metric_item(candidate, candidate.confidence_score) for candidate in candidates
        ]
        reranked_items = [
            _metric_item(by_id[result.candidate_id], result.rerank_score)
            for result in ranked
        ]
        baseline_row = calculate_case_metrics(fixed.case, baseline_items)
        rerank_row = calculate_case_metrics(fixed.case, reranked_items)
        baseline_rows.append(baseline_row)
        rerank_rows.append(rerank_row)
        bucket = per_type.setdefault(fixed.case.query_type, {"baseline": [], "rerank": []})
        bucket["baseline"].append(baseline_row)
        bucket["rerank"].append(rerank_row)
        moved = [
            result.model_dump(mode="json")
            for result in ranked
            if result.original_rank != result.rerank_rank
        ]
        if moved:
            sources = {"mrr": "mrr", "ndcg@3": "ndcg@3", "ndcg@5": "ndcg@5",
                       "top1": "hit_rate@1"}
            case_delta = {key: round((rerank_row.get(source) or 0.0) -
                                     (baseline_row.get(source) or 0.0), 6)
                          for key, source in sources.items()}
            changes.append({"caseId": fixed.case.case_id, "query": fixed.case.query,
                            "queryType": fixed.case.query_type,
                            "metricDelta": case_delta, "moved": moved})

    baseline = _summary(baseline_rows)
    reranked = _summary(rerank_rows)
    type_metrics = {}
    for query_type, rows in per_type.items():
        type_baseline, type_rerank = _summary(rows["baseline"]), _summary(rows["rerank"])
        type_metrics[query_type] = {
            "caseCount": len(rows["baseline"]), "baseline": type_baseline,
            "rerank": type_rerank,
            "delta": {key: round(type_rerank[key] - type_baseline[key], 6)
                      for key in type_baseline},
        }
    return {
        "caseCount": len(fixed_cases),
        "candidateStrategy": "fixed_hybrid_top20",
        "baseline": baseline,
        "rerank": reranked,
        "delta": {key: round(reranked[key] - baseline[key], 6) for key in baseline},
        "rankChange": {"changedCaseCount": len(changes),
                       "changedCandidateCount": sum(len(change["moved"]) for change in changes)},
        "queryTypeMetrics": type_metrics,
        "rankingChanges": changes,
    }


def _summary(rows: list[dict[str, float | None]]) -> dict[str, float]:
    return {
        "mrr": round(_mean(rows, "mrr"), 6),
        "ndcg@3": round(_mean(rows, "ndcg@3"), 6),
        "ndcg@5": round(_mean(rows, "ndcg@5"), 6),
        "top1": round(_mean(rows, "hit_rate@1"), 6),
    }


def _validate_results(
    candidates: Sequence[RetrievalCandidate], results: Sequence[RerankResult]
) -> None:
    expected = {candidate.id for candidate in candidates}
    actual = [result.candidate_id for result in results]
    if len(actual) != len(candidates) or set(actual) != expected:
        raise ValueError("Reranker 必须且只能返回固定候选集中的全部候选")
    ranks = sorted(result.rerank_rank for result in results)
    if ranks != list(range(1, len(results) + 1)):
        raise ValueError("rerank_rank 必须从1开始连续且不重复")
