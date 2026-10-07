"""Run TASK-004-C independent blind evaluation with a real local reranker."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.rerank_blind_evaluation import evaluate_rerank_blind, load_blind_dataset
from app.rag.reranker import RealCrossEncoderReranker


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
    parser = argparse.ArgumentParser(description="TASK-004-C independent Rerank blind evaluation")
    parser.add_argument(
        "--dataset", default=str(_BACKEND_ROOT / "evaluation" / "rerank_blind_dataset.json")
    )
    parser.add_argument(
        "--output", default=str(_BACKEND_ROOT / "evaluation" / "rerank_blind_results.json")
    )
    parser.add_argument("--model", default=settings.reranker_model_name)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=256)
    args = parser.parse_args()

    dataset = load_blind_dataset(args.dataset)
    version, weight_hash = _weight_metadata(args.model)
    reranker = RealCrossEncoderReranker(
        args.model,
        cache_folder=settings.model_cache_dir,
        device=args.device,
        batch_size=args.batch_size,
        max_length=args.max_length,
        local_files_only=True,
    )
    report = evaluate_rerank_blind(dataset, reranker)
    latencies = reranker.inference_latencies_ms
    payload = {
        "schemaVersion": "rerank-independent-blind-evaluation-v1",
        "evaluationType": "offline_independent_blind_candidate_ranking",
        "productionPerformance": False,
        "generatedAt": datetime.now(UTC).isoformat(),
        "environment": {
            "datasetVersion": dataset.datasetVersion,
            "modelName": args.model,
            "modelVersion": version,
            "weightHash": weight_hash,
            "device": args.device,
            "batchSize": args.batch_size,
            "maxLength": args.max_length,
            "modelLoadMs": reranker.load_latency_ms,
            "inferenceLatencyMs": {
                "total": round(sum(latencies), 3),
                "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
                "median": round(statistics.median(latencies), 3) if latencies else 0.0,
                "p95": round(sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)], 3)
                if latencies
                else 0.0,
            },
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
        },
        **report,
        "limitations": [
            "Frozen relevance labels remain pending independent human review; automatic metrics are provisional.",
            "Human Preference is not inferred from ranking metrics and remains pending.",
            "Offline single-machine latency is not production performance.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
