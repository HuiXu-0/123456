"""Freeze metric-only Trusted RAG Shadow observations for TASK-004-E3-A.

The source queries are read from the reviewed representative dataset and are
never copied to the output.  This is an offline measurement job and has no
production routing or Canary control-plane imports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.embeddings import Embedder, EmbeddingProfile, set_embedder_for_evaluation
from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.retriever import HybridRetriever, LegacyHybridRetriever
from app.rag.shadow_admission import ShadowEvaluationDataset
from app.rag.vector_store import _ChromaStore


QUERY_TYPE_RULES: dict[str, tuple[str, ...] | str] = {
    "concept_explanation": "concept_explanation",
    "operation_steps": "process_explanation",
    "programming_practice": "code_technical",
    "comprehensive_question": "multi_hop_reasoning",
    # Rare comparison questions are deliberately oversampled as independent
    # executions. Only source case ids, never query bodies, enter this plan.
    "method_comparison": (
        "t004a_concept_explanation_05",
        "t004a_concept_explanation_07",
        "t004a_concept_explanation_08",
        "t004a_concept_explanation_20",
        "t004a_process_explanation_10",
        "t004a_process_explanation_11",
        "t004a_process_explanation_12",
        "t004a_process_explanation_13",
        "t004a_process_explanation_14",
        "t004a_process_explanation_15",
        "t004a_process_explanation_16",
        "t004a_process_explanation_17",
        "t004a_process_explanation_18",
        "t004a_code_technical_03",
        "t004a_code_technical_04",
        "t004a_code_technical_05",
        "t004a_code_technical_07",
        "t004a_code_technical_09",
        "t004a_code_technical_11",
        "t004a_code_technical_12",
    ),
}


def _document_ids(items: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for item in items:
        metadata = item.get("metadata") or {}
        source = item.get("source") or {}
        value = metadata.get("document_id") or metadata.get("docId") or source.get("documentId")
        if value is not None:
            values.add(str(value))
    return values


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def _request_id(query_type: str, case_id: str, ordinal: int) -> str:
    raw = f"trusted-rag-shadow-e3a|{query_type}|{case_id}|{ordinal}"
    return "shadow_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _sampling_plan(cases: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {case["caseId"]: case for case in cases}
    for case in cases:
        by_category[case["category"]].append(case)
    plan: list[tuple[str, dict[str, Any]]] = []
    for query_type, rule in QUERY_TYPE_RULES.items():
        selected = [by_id[value] for value in rule] if isinstance(rule, tuple) else by_category[rule]
        if len(selected) < 20:
            raise ValueError(f"{query_type} sampling rule has fewer than 20 cases")
        plan.extend((query_type, case) for case in selected[:20])
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze content-free Trusted RAG Shadow metrics")
    parser.add_argument(
        "--source-dataset",
        default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_dataset.json"),
    )
    parser.add_argument(
        "--output",
        default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_e3a_frozen.json"),
    )
    parser.add_argument("--collection", default="kb_chunks")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("top-k must be positive")

    source_payload = json.loads(Path(args.source_dataset).read_text(encoding="utf-8"))
    cases = source_payload.get("cases") or []
    plan = _sampling_plan(cases)

    profile = EmbeddingProfile("hash", "deterministic-hash-v1", settings.embedding_dimension)
    embedder = Embedder(profile=profile, allow_fallback=True)
    set_embedder_for_evaluation(embedder)
    store = _ChromaStore(settings.chroma_dir, collection=args.collection, profile=profile)
    legacy = LegacyHybridRetriever(store_getter=lambda: store)
    trusted = TrustedRetrievalPipeline(retriever=HybridRetriever(store_getter=lambda: store))

    samples: list[dict[str, Any]] = []
    for ordinal, (query_type, case) in enumerate(plan, start=1):
        started = time.perf_counter()
        timed_out = False
        error_type: str | None = None
        legacy_items: list[dict[str, Any]] = []
        trusted_items: list[dict[str, Any]] = []
        confidence = 0.0
        source_coverage = 0.0
        try:
            legacy_items = legacy.search(case["query"], top_k=args.top_k)
            response = trusted.execute(
                {"query": case["query"], "user_id": "offline-shadow-evaluation", "top_k": args.top_k}
            )
            trusted_items = [item.model_dump(mode="json") for item in response.evidence]
            confidence = response.confidence
            source_coverage = response.trust_report.coverage if response.trust_report else 0.0
        except Exception as exc:  # output stores type only, never exception text/content
            error_type = type(exc).__name__
            timed_out = isinstance(exc, TimeoutError) or "timeout" in error_type.lower()
        elapsed_ms = round((time.perf_counter() - started) * 1_000, 6)
        samples.append(
            {
                "request_id": _request_id(query_type, case["caseId"], ordinal),
                "query_type": query_type,
                "latency_metrics": {"total_ms": elapsed_ms, "rag_ms": elapsed_ms, "tool_ms": 0.0},
                "quality_metrics": {
                    "evidence_overlap": _jaccard(_document_ids(legacy_items), _document_ids(trusted_items)),
                    "source_coverage": source_coverage,
                    "confidence": confidence,
                },
                "reliability_metrics": {
                    "timed_out": timed_out,
                    "error_type": error_type,
                    "timeout_reason": "shadow.measurement_timeout" if timed_out else None,
                    "cancellation_requested": False,
                    "worker_isolated": False,
                    "legacy_preserved": True,
                },
                "gate_features": {
                    "quality_metrics_complete": True,
                    "latency_metrics_complete": True,
                    "reliability_metrics_complete": True,
                    "target_environment_sample": False,
                },
            }
        )

    dataset = ShadowEvaluationDataset(
        environment="local-offline-shadow-measurement",
        evaluation_window=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        performance_verified=False,
        samples=samples,
    )
    dataset.assert_integrity()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(json.dumps(dataset.check_integrity().model_dump(), ensure_ascii=False))


if __name__ == "__main__":
    main()
