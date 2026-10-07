"""Run TASK-004-E4-A held-out Trusted RAG validation without production mutation."""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.embeddings import Embedder, EmbeddingProfile, set_embedder_for_evaluation
from app.rag.evidence_quality_evaluation import EvidenceEvaluationRecord, QualityEvaluationResult
from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.protocol import RetrievalCandidate
from app.rag.rerank_gate import OfflineRerankGate
from app.rag.reranker import RealCrossEncoderReranker
from app.rag.retriever import HybridRetriever, LegacyHybridRetriever
from app.rag.shadow_admission import ShadowDeadlineExecutor, ShadowEvaluationDataset
from app.rag.trusted_rag_gate import FaultInjectionResults, RerankMetrics, TrustedRAGGate
from app.rag.trusted_rag_validation import (
    TrustedRAGValidationCase,
    TrustedRAGValidationDataset,
    build_validation_dataset,
    query_fingerprint,
)
from app.rag.vector_store import _ChromaStore


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * percentile) - 1)], 6)


def _document_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str((item.get("source") or {}).get("documentId") or
            (item.get("metadata") or {}).get("document_id") or "")
        for item in items
    } - {""}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


class _MeasuredHybridRetriever(HybridRetriever):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_fallback_ms = 0.0
        self.last_fallback_used = False

    def search(self, *args: Any, **kwargs: Any) -> list[dict]:
        self.last_fallback_ms = 0.0
        self.last_fallback_used = False
        return super().search(*args, **kwargs)

    def _keyword_fallback(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        started = time.perf_counter()
        result = super()._keyword_fallback(*args, **kwargs)
        self.last_fallback_ms = round((time.perf_counter() - started) * 1000, 6)
        self.last_fallback_used = True
        return result


def _metric(ranked_documents: list[str], expected: set[str]) -> tuple[float, float, float]:
    first = next((index for index, value in enumerate(ranked_documents, 1) if value in expected), None)
    mrr = 1.0 / first if first else 0.0

    def ndcg(k: int) -> float:
        gains = [1.0 if value in expected else 0.0 for value in ranked_documents[:k]]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), k)))
        return dcg / ideal if ideal else 0.0

    return mrr, ndcg(3), ndcg(5)


def _mean_metric(values: list[tuple[float, float, float]]) -> dict[str, float]:
    if not values:
        return {"mrr": 0.0, "ndcg@3": 0.0, "ndcg@5": 0.0}
    return {
        "mrr": round(statistics.mean(value[0] for value in values), 6),
        "ndcg@3": round(statistics.mean(value[1] for value in values), 6),
        "ndcg@5": round(statistics.mean(value[2] for value in values), 6),
    }


def _delta(after: dict[str, float], before: dict[str, float]) -> dict[str, float]:
    return {name: round(after[name] - before[name], 6) for name in before}


def _assert_independent(dataset: Any, e3_path: Path) -> dict[str, Any]:
    e3 = json.loads(e3_path.read_text(encoding="utf-8"))
    e3_fingerprints = {query_fingerprint(case["query"]) for case in e3["cases"]}
    validation_fingerprints = dataset.query_fingerprints()
    overlap = validation_fingerprints & e3_fingerprints
    if overlap:
        raise ValueError(f"validation queries overlap E3 tuning data: {len(overlap)}")
    return {
        "e3_query_count": len(e3_fingerprints),
        "validation_query_count": len(validation_fingerprints),
        "overlap_count": 0,
        "fingerprint": "sha256(normalized_query)",
    }


def _load_faults(path: Path) -> FaultInjectionResults:
    return FaultInjectionResults.from_report(json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description="TASK-004-E4-A held-out validation")
    parser.add_argument("--dataset-output", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_validation_dataset.json"))
    parser.add_argument("--results-output", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_final_validation.json"))
    parser.add_argument("--gate-output", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_gate_decision_e4a.json"))
    parser.add_argument("--e3-source", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_dataset.json"))
    parser.add_argument("--fault-results", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_fault_results.json"))
    parser.add_argument("--collection", default="kb_chunks")
    parser.add_argument("--dataset-input")
    parser.add_argument("--environment", default="local-offline-final-validation")
    parser.add_argument("--performance-verified", action="store_true")
    parser.add_argument("--require-declared-profile", action="store_true")
    parser.add_argument("--profile-provider", default="hash")
    parser.add_argument("--profile-model", default="deterministic-hash-v1")
    parser.add_argument("--profile-dimension", type=int, default=settings.embedding_dimension)
    parser.add_argument("--rerank-policy", choices=("conditional", "never"), default="conditional")
    parser.add_argument("--deadline-ms", type=float, default=1500.0)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    dataset = (
        TrustedRAGValidationDataset.model_validate(
            json.loads(Path(args.dataset_input).read_text(encoding="utf-8"))
        )
        if args.dataset_input
        else build_validation_dataset()
    )
    independence = _assert_independent(dataset, Path(args.e3_source))
    if not args.dataset_input:
        Path(args.dataset_output).write_text(
            json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    profile = EmbeddingProfile(args.profile_provider, args.profile_model, args.profile_dimension)
    embedder = Embedder(profile=profile, allow_fallback=args.profile_provider == "hash")
    set_embedder_for_evaluation(embedder)
    store = _ChromaStore(settings.chroma_dir, collection=args.collection, profile=profile)
    embedding_status = embedder.status(load=True)
    collection_metadata = dict(store._col.metadata or {})
    sample_vectors = store._col.get(limit=1, include=["embeddings"]).get("embeddings")
    stored_dimension = len(sample_vectors[0]) if sample_vectors is not None and len(sample_vectors) else None
    declared_profile_id = collection_metadata.get("embedding_profile_id")
    if args.require_declared_profile and declared_profile_id != profile.profile_id:
        raise ValueError(
            "collection/query embedding profile mismatch: "
            f"collection={declared_profile_id!r}, query={profile.profile_id!r}"
        )
    embedding_profile = {
        "collection": store._name,
        "profile_id": profile.profile_id,
        "dimension": profile.dimension,
        "stored_dimension": stored_dimension,
        "embedding_mode": embedding_status["mode"],
        "classification": "Hash" if embedding_status["mode"] == "hash_fallback" else "Real BGE",
        "collection_declared_profile_id": declared_profile_id,
        "collection_profile_metadata_complete": all(
            key in collection_metadata for key in
            ("embedding_provider", "embedding_model", "embedding_dimension", "embedding_profile_id")
        ),
    }

    legacy = LegacyHybridRetriever(store_getter=lambda: store)
    measured = _MeasuredHybridRetriever(store_getter=lambda: store)
    pipeline = TrustedRetrievalPipeline(retriever=measured)
    deadline = ShadowDeadlineExecutor(max_isolated_workers=4)
    shadow_samples: list[dict[str, Any]] = []
    quality_records: list[EvidenceEvaluationRecord] = []
    fallback_latencies: list[float] = []
    retrieval_rows: list[dict[str, Any]] = []
    strata_quality: dict[str, list[dict[str, float]]] = defaultdict(list)

    for case in dataset.cases:
        request_id = "validation_" + query_fingerprint(case.query)[:24]
        legacy_items = legacy.search(case.query, top_k=args.top_k)
        outcome = deadline.run(
            lambda _cancel, current=case: pipeline.execute(
                {"query": current.query, "user_id": "offline-e4a-validation", "top_k": args.top_k}
            ),
            deadline_ms=args.deadline_ms,
        )
        timed_out = outcome.status.value == "deadline_exceeded"
        failed = outcome.status.value not in {"completed", "deadline_exceeded"}
        response = outcome.result if outcome.status.value == "completed" else None
        evidence = [item.model_dump(mode="json") for item in response.evidence] if response else []
        trusted_documents = _document_ids(evidence)
        expected = set(case.expected_document_ids)
        overlap = _jaccard(_document_ids(legacy_items), trusted_documents)
        source_coverage = response.trust_report.coverage if response and response.trust_report else 0.0
        confidence = response.confidence if response else 0.0
        support = bool(trusted_documents & expected)
        completeness = len(trusted_documents & expected) / len(expected)
        relevance = (
            sum(str(item.get("source", {}).get("documentId")) in expected for item in evidence) / len(evidence)
            if evidence else 0.0
        )
        strata_quality[case.query_type].append({
            "relevance": relevance,
            "support": float(support),
            "completeness": completeness,
            "source_coverage": source_coverage,
            "evidence_overlap": overlap,
        })
        for rank, item in enumerate(evidence, 1):
            document_id = str(item.get("source", {}).get("documentId") or "")
            content = str(item.get("content") or "").lower()
            concept_ratio = sum(value.lower() in content for value in case.required_concepts) / len(case.required_concepts)
            quality_records.append(EvidenceEvaluationRecord(
                request_id=request_id,
                evidence_rank=rank,
                relevance_score=3 if document_id in expected else 0,
                completeness_score=3 if concept_ratio == 1 else 2 if concept_ratio >= .5 else 1 if concept_ratio > 0 else 0,
                supports_answer=document_id in expected,
                source_quality=3 if document_id else 0,
            ))
        if measured.last_fallback_used:
            fallback_latencies.append(measured.last_fallback_ms)
        total_ms = outcome.elapsed_ms
        shadow_samples.append({
            "request_id": request_id,
            "query_type": case.query_type,
            "latency_metrics": {"total_ms": total_ms, "rag_ms": total_ms, "tool_ms": 0.0},
            "quality_metrics": {
                "evidence_overlap": overlap,
                "source_coverage": source_coverage,
                "confidence": confidence,
            },
            "reliability_metrics": {
                "timed_out": timed_out,
                "error_type": None if not failed else (outcome.error_type or "ShadowValidationFailure"),
                "timeout_reason": outcome.timeout_reason if timed_out else None,
                "cancellation_requested": outcome.cancellation_requested,
                "worker_isolated": outcome.worker_isolated,
                "legacy_preserved": True,
            },
            "gate_features": {
                "quality_metrics_complete": True,
                "latency_metrics_complete": True,
                "reliability_metrics_complete": True,
                "target_environment_sample": args.performance_verified,
            },
        })
        retrieval_rows.append({"case": case, "evidence": evidence, "confidence": confidence})

    evaluation_window = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    shadow = ShadowEvaluationDataset(
        environment=args.environment,
        evaluation_window=evaluation_window,
        performance_verified=args.performance_verified,
        samples=shadow_samples,
    )
    shadow.assert_integrity()
    aggregate = shadow.aggregate()
    request_ids = {item.request_id for item in shadow.samples}
    quality = QualityEvaluationResult.from_records(
        evaluation_window=evaluation_window,
        evidence_overlap=aggregate.evidence_overlap_mean or 0.0,
        source_coverage=aggregate.source_coverage_mean or 0.0,
        records=quality_records,
        expected_request_ids=request_ids,
    )

    baseline_values: list[tuple[float, float, float]] = []
    for row in retrieval_rows:
        case: TrustedRAGValidationCase = row["case"]
        baseline_documents = [
            str(item["source"].get("documentId") or "") for item in row["evidence"]
        ]
        expected = set(case.expected_document_ids)
        baseline_values.append(_metric(baseline_documents, expected))
    baseline_metrics = _mean_metric(baseline_values)
    if args.rerank_policy == "never":
        policies = [
            {
                "query_type": name,
                "enabled": False,
                "fallback": "hybrid",
                "reason": "independent_validation_regression_policy_disabled",
            }
            for name in sorted(Counter(case.query_type for case in dataset.cases))
        ]
        rerank_summary = {
            "policy_version": "rerank-policy-v1",
            "model": settings.reranker_model_name,
            "enabled_count": 0,
            "disabled_count": len(retrieval_rows),
            "fallback": "hybrid",
            "policies": policies,
            "baseline": baseline_metrics,
            "effective_metrics": baseline_metrics,
            "delta": {"mrr": 0.0, "ndcg@3": 0.0, "ndcg@5": 0.0},
            "degraded_case_count": 0,
        }
        rerank_gate_metrics = RerankMetrics(
            independent_validation=True,
            metrics_provisional=False,
            human_review_complete=False,
            mrr_delta=0.0,
            ndcg_at_3_delta=0.0,
            ndcg_at_5_delta=0.0,
            degraded_case_count=0,
            policy_enabled=False,
            fallback="hybrid",
            reason="independent_validation_regression_policy_disabled",
        )
    else:
        reranker = RealCrossEncoderReranker(
            settings.reranker_model_name,
            cache_folder=settings.model_cache_dir,
            device="cpu",
            batch_size=4,
            max_length=256,
            local_files_only=True,
        )
        conditional_gate = OfflineRerankGate(
            "conditional", beneficial_query_types=frozenset({"concept_explanation"}), min_confidence=.9883
        )
        always_values: list[tuple[float, float, float]] = []
        conditional_values: list[tuple[float, float, float]] = []
        degraded: dict[str, list[str]] = defaultdict(list)
        conditional_enabled = 0
        rerank_started = time.perf_counter()
        for row, baseline_metric in zip(retrieval_rows, baseline_values):
            case = row["case"]
            candidates = [
                RetrievalCandidate(
                    id=str(item["source"].get("chunkId") or f"{case.case_id}-{index}"),
                    content=item["content"], source=item["source"],
                    metadata=item.get("metadata") or {},
                    confidence_score=float(item.get("score") or 0.0),
                )
                for index, item in enumerate(row["evidence"], 1)
            ]
            source_by_id = {item.id: str(item.source.get("documentId") or "") for item in candidates}
            ranked = reranker.rerank(case.query, candidates)
            always_metric = _metric(
                [source_by_id[item.candidate_id] for item in sorted(ranked, key=lambda item: item.rerank_rank)],
                set(case.expected_document_ids),
            )
            always_values.append(always_metric)
            if sum(always_metric) + 1e-9 < sum(baseline_metric):
                degraded[case.query_type].append(case.case_id)
            decision = conditional_gate.decide(case.query_type, float(row["confidence"]))
            conditional_enabled += int(decision.enabled)
            conditional_values.append(always_metric if decision.enabled else baseline_metric)
        rerank_total_ms = round((time.perf_counter() - rerank_started) * 1000, 6)
        always_metrics = _mean_metric(always_values)
        conditional_metrics = _mean_metric(conditional_values)
        conditional_degraded_count = sum(
            sum(after) + 1e-9 < sum(before)
            for before, after in zip(baseline_values, conditional_values)
        )
        rerank_summary = {
            "model": settings.reranker_model_name,
            "independent_validation": True,
            "human_review_complete": False,
            "metrics_provisional": True,
            "baseline": baseline_metrics,
            "always": {"metrics": always_metrics, "delta": _delta(always_metrics, baseline_metrics),
                       "degraded_by_query_type": dict(sorted((name, len(ids)) for name, ids in degraded.items()))},
            "conditional": {"rule": {"query_types": ["concept_explanation"], "min_confidence": .9883},
                            "enabled_count": conditional_enabled,
                            "disabled_count": len(retrieval_rows) - conditional_enabled,
                            "metrics": conditional_metrics, "delta": _delta(conditional_metrics, baseline_metrics),
                            "degraded_case_count": conditional_degraded_count},
            "model_load_ms": reranker.load_latency_ms,
            "evaluation_total_ms": rerank_total_ms,
            "inference_p95_ms": _percentile(reranker.inference_latencies_ms, .95),
        }
        rerank_gate_metrics = RerankMetrics(
            independent_validation=True, metrics_provisional=True, human_review_complete=False,
            mrr_delta=rerank_summary["conditional"]["delta"]["mrr"],
            ndcg_at_3_delta=rerank_summary["conditional"]["delta"]["ndcg@3"],
            ndcg_at_5_delta=rerank_summary["conditional"]["delta"]["ndcg@5"],
            degraded_case_count=conditional_degraded_count,
        )
    faults = _load_faults(Path(args.fault_results))
    gate = TrustedRAGGate().evaluate(
        shadow,
        faults,
        rerank_metrics=rerank_gate_metrics,
        quality_evaluation=quality,
    )
    gate_payload = {
        "schemaVersion": "trusted-rag-canary-gate-v4",
        "qualityGateVersion": "quality-gate-v2",
        "evaluationType": "offline_final_admission_validation",
        "generatedAt": datetime.now(UTC).isoformat(),
        "productionMutation": False,
        "canaryEnabled": False,
        **gate.model_dump(mode="json"),
    }
    Path(args.gate_output).write_text(json.dumps(gate_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def stratum_summary(rows: list[dict[str, float]]) -> dict[str, float]:
        return {name: round(statistics.mean(row[name] for row in rows), 6) for name in rows[0]}

    latency_values = [item.latency_metrics.total_ms for item in shadow.samples]
    results = {
        "schema_version": "trusted-rag-final-validation-v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "production_mutation": False,
        "canary_enabled": False,
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "sample_count": len(dataset.cases),
            "query_type_counts": dict(sorted(Counter(case.query_type for case in dataset.cases).items())),
            "independence_check": independence,
        },
        "embedding_profile": embedding_profile,
        "quality": {
            "relevance": quality.human_relevance,
            "support_rate": quality.support_rate,
            "completeness": quality.completeness,
            "source_coverage": quality.source_coverage,
            "evidence_overlap": quality.evidence_overlap,
            "by_query_type": {name: stratum_summary(rows) for name, rows in sorted(strata_quality.items())},
        },
        "latency": {
            "environment": shadow.environment,
            "environment_fingerprint": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "device": "cpu",
                "execution": "local_target_validation_host",
            },
            "performance_verified": shadow.performance_verified,
            "sample_count": len(shadow.samples),
            "p50_ms": _percentile(latency_values, .50),
            "p95_ms": _percentile(latency_values, .95),
            "timeout_count": sum(item.reliability_metrics.timed_out for item in shadow.samples),
            "timeout_rate": aggregate.timeout_rate,
            "error_count": sum(
                item.reliability_metrics.error_type is not None
                for item in shadow.samples
            ),
            "error_rate": aggregate.error_rate,
            "fallback_trigger_count": len(fallback_latencies),
            "fallback_overhead_mean_ms": round(statistics.mean(fallback_latencies), 6) if fallback_latencies else 0.0,
            "fallback_overhead_p95_ms": _percentile(fallback_latencies, .95),
        },
        "reliability": {
            "error_rate": aggregate.error_rate,
            "legacy_preserved": aggregate.all_legacy_preserved,
            "timeouts_isolated": aggregate.all_timeouts_isolated,
            "fault_scenarios": faults.scenario_count,
            "fault_pass_count": faults.pass_count,
            "fault_block_count": faults.block_count,
        },
        "rerank": rerank_summary,
        "gate": gate_payload,
    }
    Path(args.results_output).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "dataset": results["dataset"],
        "embedding_profile": embedding_profile,
        "quality": results["quality"],
        "latency": results["latency"],
        "reliability": results["reliability"],
        "rerank": rerank_summary,
        "gate": gate_payload,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
