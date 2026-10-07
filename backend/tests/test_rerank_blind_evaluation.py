from __future__ import annotations

import json
from pathlib import Path

from app.rag.protocol import RetrievalCandidate
from app.rag.rerank_blind_evaluation import (
    BlindEvaluationDataset,
    evaluate_rerank_blind,
    load_blind_dataset,
)
from app.rag.reranker import BaseReranker, MockReranker, RerankResult


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "rerank_blind_dataset.json"
RESULTS = ROOT / "evaluation" / "rerank_blind_results.json"
TASK003_DATASET = ROOT / "evaluation" / "retrieval_cases.json"


class _OrderedReranker(BaseReranker):
    def __init__(self, ordered_ids: list[str]) -> None:
        self.ordered_ids = ordered_ids
        self.seen_candidates: list[RetrievalCandidate] = []

    def rerank(
        self, query: str, candidates: list[RetrievalCandidate]
    ) -> list[RerankResult]:
        del query
        self.seen_candidates = list(candidates)
        original = {item.id: rank for rank, item in enumerate(candidates, 1)}
        scores = {item.id: item.confidence_score for item in candidates}
        return [
            RerankResult(
                candidate_id=candidate_id,
                original_rank=original[candidate_id],
                rerank_rank=rank,
                original_score=scores[candidate_id],
                rerank_score=float(len(self.ordered_ids) - rank),
            )
            for rank, candidate_id in enumerate(self.ordered_ids, 1)
        ]


def _verified_dataset() -> BlindEvaluationDataset:
    return BlindEvaluationDataset.model_validate(
        {
            "datasetVersion": "fixture-independent-v1",
            "datasetType": "independent_rerank_blind_dataset",
            "independentFromTask003": True,
            "task003CandidateSnapshotReused": False,
            "candidateIdsAnonymized": True,
            "candidateSnapshotFrozen": True,
            "humanReviewStatus": "human_verified",
            "caseCount": 1,
            "cases": [
                {
                    "caseId": "fixture",
                    "query": "哪个候选相关",
                    "candidates": [
                        {
                            "anonymousId": f"RB01-{suffix}",
                            "content": f"固定候选正文 {rank}，长度满足盲测协议要求。",
                            "baselineRank": rank,
                        }
                        for rank, suffix in enumerate(
                            ["A7K2", "M4Q9", "T8X3", "C5V1", "P2N6"], 1
                        )
                    ],
                    "evaluationLabels": [
                        {
                            "anonymousId": f"RB01-{suffix}",
                            "relevanceGrade": 3 if rank == 5 else 0,
                            "sourceDocumentRef": f"private-doc-{rank}",
                            "reviewStatus": "human_verified",
                        }
                        for rank, suffix in enumerate(
                            ["A7K2", "M4Q9", "T8X3", "C5V1", "P2N6"], 1
                        )
                    ],
                    "humanPreference": {
                        "reviewStatus": "human_verified",
                        "preferredSystem": "rerank",
                        "reviewerId": "reviewer-001",
                    },
                }
            ],
        }
    )


def test_dataset_is_independent_fixed_and_anonymized_from_task003():
    dataset = load_blind_dataset(DATASET)
    task003_queries = {
        " ".join(item["query"].lower().split())
        for item in json.loads(TASK003_DATASET.read_text(encoding="utf-8"))
    }

    assert dataset.datasetVersion == "rerank-blind-independent-v1"
    assert dataset.caseCount == 20
    assert dataset.independentFromTask003 is True
    assert dataset.task003CandidateSnapshotReused is False
    assert dataset.candidateIdsAnonymized is True
    assert dataset.candidateSnapshotFrozen is True
    assert all(" ".join(case.query.lower().split()) not in task003_queries for case in dataset.cases)
    anonymous_ids = [item.anonymousId for case in dataset.cases for item in case.candidates]
    assert len(anonymous_ids) == len(set(anonymous_ids)) == 100
    assert all("doc_" not in candidate_id.lower() for candidate_id in anonymous_ids)
    assert all(len(case.candidates) == 5 for case in dataset.cases)


def test_pending_human_labels_are_never_replaced_by_automatic_preference():
    dataset = load_blind_dataset(DATASET)
    report = evaluate_rerank_blind(dataset, MockReranker())

    assert report["humanPreference"] == {
        "status": "pending_independent_human_review",
        "reviewedCount": 0,
        "totalCount": 20,
        "counts": {"baseline": 0, "rerank": 0, "tie": 0},
        "rerankPreferenceRate": None,
    }
    assert report["relevanceReview"]["status"] == "pending_independent_human_review"
    assert report["metricsProvisional"] is True
    assert report["gateEvidence"]["independentValidation"] is False
    assert report["finalDecision"] == "BLOCK"
    assert "rerank.human_preference_pending_independent_human_review" in report["blockReasons"]


def test_reranker_sees_no_private_source_relevance_or_human_preference_labels():
    dataset = _verified_dataset()
    order = ["RB01-P2N6", "RB01-A7K2", "RB01-M4Q9", "RB01-T8X3", "RB01-C5V1"]
    reranker = _OrderedReranker(order)
    report = evaluate_rerank_blind(dataset, reranker)

    assert all(candidate.source == {} and candidate.metadata == {} for candidate in reranker.seen_candidates)
    assert all("private-doc" not in candidate.content for candidate in reranker.seen_candidates)
    assert report["rerank"]["recall@1"] == 1.0
    assert report["rerank"]["mrr"] == 1.0
    assert report["delta"]["mrr"] == 0.8
    assert report["humanPreference"]["rerankPreferenceRate"] == 1.0
    assert report["finalDecision"] == "PASS"


def test_any_metric_regression_is_reported_as_degraded_case_and_blocks():
    dataset = _verified_dataset()
    order = ["RB01-A7K2", "RB01-M4Q9", "RB01-T8X3", "RB01-C5V1", "RB01-P2N6"]
    report = evaluate_rerank_blind(dataset, _OrderedReranker(order))

    # Baseline already has the relevant item at rank 5; move it to rank 4 first,
    # then use a deliberately worse order by changing the frozen baseline rank.
    dataset.cases[0].candidates[4].baselineRank = 1
    dataset.cases[0].candidates[0].baselineRank = 5
    worse = evaluate_rerank_blind(dataset, _OrderedReranker(order))
    assert report["degradedCaseCount"] == 0
    assert worse["degradedCaseCount"] == 1
    assert worse["degradedCases"][0]["caseId"] == "fixture"
    assert worse["finalDecision"] == "BLOCK"
    assert "rerank.degraded_cases_present" in worse["blockReasons"]


def test_committed_result_is_real_model_output_but_human_gate_remains_blocked():
    report = json.loads(RESULTS.read_text(encoding="utf-8"))

    assert report["schemaVersion"] == "rerank-independent-blind-evaluation-v1"
    assert report["evaluationType"] == "offline_independent_blind_candidate_ranking"
    assert report["productionPerformance"] is False
    assert report["environment"]["modelName"] == "BAAI/bge-reranker-base"
    assert report["environment"]["weightHash"].startswith("sha256:")
    assert report["caseCount"] == 20
    assert set(report["baseline"]) == {
        "recall@1", "recall@3", "recall@5", "mrr", "ndcg@3", "ndcg@5"
    }
    assert set(report["rerank"]) == set(report["baseline"])
    assert report["humanPreference"]["rerankPreferenceRate"] is None
    assert report["gateEvidence"]["independentValidation"] is False
    assert report["finalDecision"] == "BLOCK"
