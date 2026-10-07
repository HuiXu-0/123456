"""Run TASK-004-B Legacy/Trusted RAG benchmark without production routing."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.canary_benchmark import CanaryBenchmark, load_canary_dataset
from app.rag.embeddings import Embedder, EmbeddingProfile, set_embedder_for_evaluation
from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.retriever import HybridRetriever, LegacyHybridRetriever
from app.rag.vector_store import _ChromaStore


def _profile(mode: str) -> tuple[EmbeddingProfile, Embedder]:
    if mode == "hash":
        profile = EmbeddingProfile("hash", "deterministic-hash-v1", settings.embedding_dimension)
        return profile, Embedder(profile=profile, allow_fallback=True)
    profile = EmbeddingProfile(
        "sentence-transformers", settings.embedding_model_name, settings.embedding_dimension
    )
    return profile, Embedder(profile=profile, allow_fallback=mode != "real")


def _trusted_items(response) -> list[dict]:
    return [item.model_dump(mode="json") for item in response.evidence]


def main() -> None:
    parser = argparse.ArgumentParser(description="TASK-004-B offline RAG canary benchmark")
    parser.add_argument(
        "--dataset",
        default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_dataset.json"),
    )
    parser.add_argument(
        "--output",
        default=str(_BACKEND_ROOT / "evaluation" / "legacy_rag_baseline.json"),
    )
    parser.add_argument("--collection", default="kb_chunks")
    parser.add_argument("--embedding-mode", choices=["configured", "hash", "real"], default="configured")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--warmup-cases", type=int, default=5)
    parser.add_argument("--timeout-ms", type=float, default=5_000.0)
    args = parser.parse_args()
    if args.top_k < 1 or args.rounds < 1 or args.warmup_cases < 0:
        parser.error("top-k/rounds必须为正数，warmup-cases不能为负数")

    dataset_meta, cases = load_canary_dataset(args.dataset)
    profile, embedder = _profile(args.embedding_mode)
    set_embedder_for_evaluation(embedder)
    embedding_status = embedder.require_real() if args.embedding_mode == "real" else embedder.status(load=True)
    store = _ChromaStore(settings.chroma_dir, collection=args.collection, profile=profile)
    legacy_retriever = LegacyHybridRetriever(store_getter=lambda: store)
    trusted_pipeline = TrustedRetrievalPipeline(retriever=HybridRetriever(store_getter=lambda: store))

    def legacy_runner(case: dict) -> list[dict]:
        return legacy_retriever.search(case["query"], top_k=args.top_k)

    def trusted_runner(case: dict) -> list[dict]:
        response = trusted_pipeline.execute(
            {"query": case["query"], "user_id": "offline-canary-benchmark", "top_k": args.top_k}
        )
        return _trusted_items(response)

    for index, case in enumerate(cases[: args.warmup_cases]):
        if index % 2 == 0:
            legacy_runner(case)
            trusted_runner(case)
        else:
            trusted_runner(case)
            legacy_runner(case)

    benchmark = CanaryBenchmark(timeout_ms=args.timeout_ms).run_pair(
        cases, legacy_runner=legacy_runner, trusted_runner=trusted_runner, rounds=args.rounds
    )
    report = {
        "schemaVersion": "trusted-rag-canary-benchmark-v1",
        "benchmarkType": "offline_representative_evaluation",
        "productionPerformance": False,
        "generatedAt": datetime.now(UTC).isoformat(),
        "environment": {
            **dataset_meta,
            "embeddingProfile": embedding_status,
            "collection": args.collection,
            "collectionDocumentCount": len(store.get_all()),
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "processor": platform.processor(),
            },
            "parameters": {
                "topK": args.top_k,
                "rounds": args.rounds,
                "warmupCases": args.warmup_cases,
                "timeoutMs": args.timeout_ms,
            },
        },
        **benchmark,
        "limitations": [
            "Representative dataset only; no production Shadow requests are included.",
            "Local single-process measurements are not production performance.",
            "Correctness is retrieval-support proxy, not generated-answer human correctness.",
            "Timeout budget is observational and does not hard-cancel a blocked dependency.",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
