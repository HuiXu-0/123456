"""Independent, offline-only Rerank blind evaluation for TASK-004-C."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.protocol import RetrievalCandidate
from app.rag.reranker import BaseReranker, RerankResult


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlindCandidate(_StrictModel):
    anonymousId: str = Field(pattern=r"^RB\d{2}-[A-Z0-9]{4}$")
    content: str = Field(min_length=20)
    baselineRank: int = Field(ge=1)


class BlindRelevanceLabel(_StrictModel):
    anonymousId: str
    relevanceGrade: int = Field(ge=0, le=3)
    sourceDocumentRef: str = Field(min_length=1)
    reviewStatus: Literal["pending_independent_human_review", "human_verified"]


class HumanPreferenceLabel(_StrictModel):
    reviewStatus: Literal["pending_independent_human_review", "human_verified"]
    preferredSystem: Literal["baseline", "rerank", "tie"] | None = None
    reviewerId: str | None = None

    @model_validator(mode="after")
    def validate_review_state(self) -> "HumanPreferenceLabel":
        if self.reviewStatus == "human_verified":
            if self.preferredSystem is None or not self.reviewerId:
                raise ValueError("human_verified偏好必须包含preferredSystem和reviewerId")
        elif self.preferredSystem is not None or self.reviewerId is not None:
            raise ValueError("待人工复核的偏好不能预填模型选择或reviewerId")
        return self


class BlindEvaluationCase(_StrictModel):
    caseId: str = Field(min_length=1)
    query: str = Field(min_length=1)
    candidates: list[BlindCandidate] = Field(min_length=5, max_length=20)
    evaluationLabels: list[BlindRelevanceLabel] = Field(min_length=5, max_length=20)
    humanPreference: HumanPreferenceLabel

    @model_validator(mode="after")
    def validate_fixed_snapshot(self) -> "BlindEvaluationCase":
        candidate_ids = [item.anonymousId for item in self.candidates]
        label_ids = [item.anonymousId for item in self.evaluationLabels]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"{self.caseId}包含重复匿名候选ID")
        if set(candidate_ids) != set(label_ids) or len(label_ids) != len(set(label_ids)):
            raise ValueError(f"{self.caseId}候选与独立标签不一致")
        ranks = sorted(item.baselineRank for item in self.candidates)
        if ranks != list(range(1, len(self.candidates) + 1)):
            raise ValueError(f"{self.caseId} baselineRank必须连续")
        if not any(item.relevanceGrade > 0 for item in self.evaluationLabels):
            raise ValueError(f"{self.caseId}必须至少有一个相关候选")
        return self


class BlindEvaluationDataset(_StrictModel):
    datasetVersion: str
    datasetType: Literal["independent_rerank_blind_dataset"]
    independentFromTask003: Literal[True]
    task003CandidateSnapshotReused: Literal[False]
    candidateIdsAnonymized: Literal[True]
    candidateSnapshotFrozen: Literal[True]
    humanReviewStatus: Literal["pending_independent_human_review", "human_verified"]
    caseCount: int = Field(ge=1)
    cases: list[BlindEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_count(self) -> "BlindEvaluationDataset":
        if self.caseCount != len(self.cases):
            raise ValueError("caseCount与cases数量不一致")
        case_ids = [case.caseId for case in self.cases]
        queries = [" ".join(case.query.lower().split()) for case in self.cases]
        if len(case_ids) != len(set(case_ids)) or len(queries) != len(set(queries)):
            raise ValueError("盲测caseId和query必须唯一")
        return self


def load_blind_dataset(path: str | Path) -> BlindEvaluationDataset:
    return BlindEvaluationDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _metrics(ranking: list[str], grades: dict[str, int]) -> dict[str, float]:
    relevant = {candidate_id for candidate_id, grade in grades.items() if grade > 0}
    output: dict[str, float] = {}
    for k in (1, 3, 5):
        found = set(ranking[:k]) & relevant
        output[f"recall@{k}"] = len(found) / len(relevant)
    first = next((rank for rank, candidate_id in enumerate(ranking, 1) if candidate_id in relevant), None)
    output["mrr"] = 1.0 / first if first else 0.0
    for k in (3, 5):
        gains = [grades[candidate_id] for candidate_id in ranking[:k]]
        dcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(gains))
        ideal = sorted(grades.values(), reverse=True)[:k]
        idcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(ideal))
        output[f"ndcg@{k}"] = dcg / idcg if idcg else 0.0
    return {key: round(value, 6) for key, value in output.items()}


def _mean(rows: list[dict[str, float]]) -> dict[str, float]:
    return {
        key: round(sum(row[key] for row in rows) / len(rows), 6)
        for key in rows[0]
    }


def _validate_rerank_results(
    candidates: list[BlindCandidate], results: list[RerankResult]
) -> list[str]:
    expected = {item.anonymousId for item in candidates}
    actual = [item.candidate_id for item in results]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Reranker必须且只能返回全部匿名固定候选")
    ranks = sorted(item.rerank_rank for item in results)
    if ranks != list(range(1, len(results) + 1)):
        raise ValueError("rerank_rank必须连续且唯一")
    return [item.candidate_id for item in sorted(results, key=lambda item: item.rerank_rank)]


def evaluate_rerank_blind(
    dataset: BlindEvaluationDataset,
    reranker: BaseReranker,
) -> dict[str, Any]:
    baseline_rows: list[dict[str, float]] = []
    rerank_rows: list[dict[str, float]] = []
    case_results: list[dict[str, Any]] = []
    degraded_cases: list[dict[str, Any]] = []
    preference_counts: Counter[str] = Counter({"baseline": 0, "rerank": 0, "tie": 0})
    reviewed_preferences = 0
    verified_relevance_labels = 0
    total_relevance_labels = 0

    for case in dataset.cases:
        candidates = sorted(case.candidates, key=lambda item: item.baselineRank)
        # The model receives anonymous IDs and content only. Private source refs,
        # relevance grades and human preference state are never included.
        model_candidates = [
            RetrievalCandidate(
                id=item.anonymousId,
                content=item.content,
                source={},
                metadata={},
                confidence_score=round(1.0 - (item.baselineRank - 1) * 0.1, 6),
            )
            for item in candidates
        ]
        results = reranker.rerank(case.query, model_candidates)
        rerank_order = _validate_rerank_results(candidates, results)
        baseline_order = [item.anonymousId for item in candidates]
        grades = {item.anonymousId: item.relevanceGrade for item in case.evaluationLabels}
        total_relevance_labels += len(case.evaluationLabels)
        verified_relevance_labels += sum(
            item.reviewStatus == "human_verified" for item in case.evaluationLabels
        )
        baseline_metrics = _metrics(baseline_order, grades)
        rerank_metrics = _metrics(rerank_order, grades)
        delta = {
            key: round(rerank_metrics[key] - baseline_metrics[key], 6)
            for key in baseline_metrics
        }
        degraded_metrics = sorted(key for key, value in delta.items() if value < 0)
        result = {
            "caseId": case.caseId,
            "baselineRanking": baseline_order,
            "rerankRanking": rerank_order,
            "baseline": baseline_metrics,
            "rerank": rerank_metrics,
            "delta": delta,
            "degradedMetrics": degraded_metrics,
        }
        case_results.append(result)
        baseline_rows.append(baseline_metrics)
        rerank_rows.append(rerank_metrics)
        if degraded_metrics:
            degraded_cases.append(
                {"caseId": case.caseId, "degradedMetrics": degraded_metrics, "delta": delta}
            )
        preference = case.humanPreference
        if preference.reviewStatus == "human_verified":
            reviewed_preferences += 1
            assert preference.preferredSystem is not None
            preference_counts[preference.preferredSystem] += 1

    baseline = _mean(baseline_rows)
    rerank = _mean(rerank_rows)
    delta = {key: round(rerank[key] - baseline[key], 6) for key in baseline}
    all_preferences_reviewed = reviewed_preferences == len(dataset.cases)
    all_relevance_reviewed = verified_relevance_labels == total_relevance_labels
    block_reasons: list[str] = []
    if not all_relevance_reviewed:
        block_reasons.append("rerank.relevance_labels_pending_independent_human_review")
    if not all_preferences_reviewed:
        block_reasons.append("rerank.human_preference_pending_independent_human_review")
    if degraded_cases:
        block_reasons.append("rerank.degraded_cases_present")
    if min(delta["mrr"], delta["ndcg@3"], delta["ndcg@5"]) < 0:
        block_reasons.append("rerank.aggregate_metric_regression")
    independent_validation = not block_reasons
    return {
        "datasetVersion": dataset.datasetVersion,
        "caseCount": len(dataset.cases),
        "candidateStrategy": "fixed_anonymized_candidates",
        "modelEvaluationPolicy": "reranker_ranks_only; frozen labels score results",
        "metricsProvisional": not all_relevance_reviewed,
        "baseline": baseline,
        "rerank": rerank,
        "delta": delta,
        "humanPreference": {
            "status": "human_verified" if all_preferences_reviewed else "pending_independent_human_review",
            "reviewedCount": reviewed_preferences,
            "totalCount": len(dataset.cases),
            "counts": dict(preference_counts),
            "rerankPreferenceRate": (
                round(preference_counts["rerank"] / reviewed_preferences, 6)
                if reviewed_preferences
                else None
            ),
        },
        "relevanceReview": {
            "status": "human_verified" if all_relevance_reviewed else "pending_independent_human_review",
            "verifiedLabelCount": verified_relevance_labels,
            "totalLabelCount": total_relevance_labels,
        },
        "degradedCaseCount": len(degraded_cases),
        "degradedCases": degraded_cases,
        "caseResults": case_results,
        "gateEvidence": {
            "independentValidation": independent_validation,
            "mrrDelta": delta["mrr"],
            "ndcgAt3Delta": delta["ndcg@3"],
            "ndcgAt5Delta": delta["ndcg@5"],
            "degradedCaseCount": len(degraded_cases),
        },
        "finalDecision": "PASS" if independent_validation else "BLOCK",
        "blockReasons": block_reasons,
    }
