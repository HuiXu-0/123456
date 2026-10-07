"""Run the offline-only real CrossEncoder rerank experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.embeddings import Embedder, EmbeddingProfile, set_embedder_for_evaluation
from app.rag.evaluation import load_evaluation_cases
from app.rag.rerank_evaluation import FixedCandidateCase, candidate_from_dict, evaluate_fixed_candidates
from app.rag.reranker import RealCrossEncoderReranker
from app.rag.retriever import HybridRetriever
from app.rag.vector_store import _ChromaStore


def _weight_metadata(model_name: str) -> tuple[str, str]:
    model_root = Path(settings.model_cache_dir) / f"models--{model_name.replace('/', '--')}"
    revision_file = model_root / "refs" / "main"
    if not revision_file.exists():
        raise RuntimeError(f"real reranker revision is not cached: {revision_file}")
    revision = revision_file.read_text(encoding="utf-8").strip()
    weight_file = model_root / "snapshots" / revision / "model.safetensors"
    if not weight_file.exists():
        raise RuntimeError(f"real reranker weight is not cached: {weight_file}")
    digest = hashlib.sha256()
    with weight_file.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return revision, f"sha256:{digest.hexdigest()}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline-only real CrossEncoder rerank experiment")
    parser.add_argument("--dataset", default=str(_BACKEND_ROOT / "evaluation" / "retrieval_cases.json"))
    parser.add_argument("--collection", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=settings.reranker_model_name)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=512)
    args = parser.parse_args()

    profile = EmbeddingProfile("hash", "deterministic-hash-v1", settings.embedding_dimension)
    set_embedder_for_evaluation(Embedder(profile=profile, allow_fallback=True))
    store = _ChromaStore(settings.chroma_dir, collection=args.collection, profile=profile)
    retriever = HybridRetriever(store_getter=lambda: store)
    fixed_cases = []
    for case in load_evaluation_cases(args.dataset):
        filters = {"knowledge_scope": case.knowledge_scope} if case.knowledge_scope else None
        rows = retriever.search(case.query, top_k=20, filters=filters)
        fixed_cases.append(FixedCandidateCase(case, tuple(candidate_from_dict(row) for row in rows)))

    snapshot_path = Path(args.snapshot)
    snapshot = {
        "strategy": "fixed_hybrid_top20", "embedding_profile": profile.profile_id,
        "collection": args.collection,
        "cases": [{"query_id": fixed.case.case_id, "query": fixed.case.query,
                   "query_type": fixed.case.query_type,
                   "embedding_profile": profile.profile_id, "collection": args.collection,
                   "candidates": [candidate.model_dump(mode="json")
                                  for candidate in fixed.candidates]}
                  for fixed in fixed_cases],
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    version, weight_hash = _weight_metadata(args.model)
    reranker = RealCrossEncoderReranker(
        args.model, cache_folder=settings.model_cache_dir, device=args.device,
        batch_size=args.batch_size, max_length=args.max_length, local_files_only=True)
    report = evaluate_fixed_candidates(fixed_cases, reranker)
    latencies = reranker.inference_latencies_ms
    report.update({
        "embeddingProfile": profile.profile_id, "collection": args.collection,
        "candidateSnapshot": str(snapshot_path),
        "experimentEnvironment": {
            "model_name": args.model, "model_version": version,
            "weight_hash": weight_hash, "device": args.device,
            "batch_size": args.batch_size, "max_length": args.max_length,
            "latency": {"model_load_ms": reranker.load_latency_ms,
                        "total_inference_ms": round(sum(latencies), 3),
                        "mean_query_ms": round(statistics.mean(latencies), 3),
                        "median_query_ms": round(statistics.median(latencies), 3),
                        "p95_query_ms": round(sorted(latencies)[max(0, int(len(latencies) * .95) - 1)], 3)},
            "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        },
    })
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
