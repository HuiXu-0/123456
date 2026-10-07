"""TASK-003-D3-C offline gate and latency experiment on a fixed candidate snapshot."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.evaluation import load_evaluation_cases
from app.rag.rerank_evaluation import FixedCandidateCase, candidate_from_dict, evaluate_fixed_candidates
from app.rag.rerank_gate import DecisionReranker, OfflineRerankGate
from app.rag.reranker import MockReranker, RealCrossEncoderReranker


def _load_fixed_cases(dataset: str, snapshot_path: str) -> list[FixedCandidateCase]:
    cases = {case.case_id: case for case in load_evaluation_cases(dataset)}
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    return [FixedCandidateCase(cases[row["query_id"]],
                               tuple(candidate_from_dict(item) for item in row["candidates"]))
            for row in snapshot["cases"]]


def _latency(reranker: RealCrossEncoderReranker, query_count: int) -> dict[str, float | int]:
    values = reranker.inference_latencies_ms
    return {"evaluated_query_count": query_count,
            "model_load_ms": reranker.load_latency_ms,
            "total_inference_ms": round(sum(values), 3),
            "mean_query_ms": round(statistics.mean(values), 3) if values else 0.0,
            "median_query_ms": round(statistics.median(values), 3) if values else 0.0,
            "p95_query_ms": round(sorted(values)[max(0, int(len(values) * .95) - 1)], 3)
            if values else 0.0}


def _degraded(report: dict) -> list[dict]:
    return [{"caseId": row["caseId"], "queryType": row["queryType"],
             "metricDelta": row["metricDelta"]}
            for row in report["rankingChanges"]
            if sum(row["metricDelta"].values()) < 0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline rerank shadow gate experiment")
    parser.add_argument("--dataset", default=str(_BACKEND_ROOT / "evaluation" / "retrieval_cases.json"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=settings.reranker_model_name)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    fixed_cases = _load_fixed_cases(args.dataset, args.snapshot)

    baseline = evaluate_fixed_candidates(fixed_cases, MockReranker())
    configurations = [(4, 256), (8, 256), (4, 512), (8, 512)]
    latency_experiments = []
    for batch_size, max_length in configurations:
        reranker = RealCrossEncoderReranker(
            args.model, cache_folder=settings.model_cache_dir, device=args.device,
            batch_size=batch_size, max_length=max_length, local_files_only=True)
        report = evaluate_fixed_candidates(fixed_cases, reranker)
        latency_experiments.append({
            "batch_size": batch_size, "max_length": max_length,
            "metrics": report["rerank"], "delta": report["delta"],
            "latency": _latency(reranker, len(fixed_cases)),
            "degradedCases": _degraded(report),
        })

    # Choose the lowest total latency among variants whose MRR and nDCG metrics
    # are no worse than the D3-B 8x512 reference within deterministic precision.
    reference = next(row for row in latency_experiments
                     if row["batch_size"] == 8 and row["max_length"] == 512)
    eligible = [row for row in latency_experiments
                if all(row["metrics"][key] >= reference["metrics"][key]
                       for key in ("mrr", "ndcg@3", "ndcg@5"))]
    selected = min(eligible, key=lambda row: row["latency"]["total_inference_ms"])

    conditional_model = RealCrossEncoderReranker(
        args.model, cache_folder=settings.model_cache_dir, device=args.device,
        batch_size=selected["batch_size"], max_length=selected["max_length"],
        local_files_only=True)
    contexts = {fixed.case.query: (fixed.case.query_type,
                max((candidate.confidence_score for candidate in fixed.candidates), default=0.0))
                for fixed in fixed_cases}
    controlled = DecisionReranker(conditional_model, OfflineRerankGate("conditional"), contexts)
    conditional = evaluate_fixed_candidates(fixed_cases, controlled)
    conditional["decisions"] = [decision.model_dump(mode="json")
                                for decision in controlled.decisions]
    enabled_count = sum(decision.enabled for decision in controlled.decisions)
    conditional["latency"] = _latency(conditional_model, enabled_count)
    conditional["degradedCases"] = _degraded(conditional)

    result = {
        "scope": "offline_shadow_only", "caseCount": len(fixed_cases),
        "candidateSnapshot": args.snapshot,
        "policies": {
            "never": {"label": "Hybrid Only", "metrics": baseline["baseline"],
                      "incrementalRerankLatencyMs": 0.0, "degradedCases": []},
            "always": {"label": "Hybrid + Rerank",
                       "selectedConfiguration": {"batch_size": selected["batch_size"],
                                                 "max_length": selected["max_length"]},
                       "metrics": selected["metrics"], "delta": selected["delta"],
                       "latency": selected["latency"],
                       "degradedCases": selected["degradedCases"]},
            "conditional": {"label": "Type + Confidence Gate",
                            "rule": {"query_types": ["概念解释"],
                                     "min_confidence": 0.9883},
                            "enabledCount": enabled_count,
                            "disabledCount": len(fixed_cases) - enabled_count,
                            "metrics": conditional["rerank"], "delta": conditional["delta"],
                            "latency": conditional["latency"],
                            "degradedCases": conditional["degradedCases"],
                            "decisions": conditional["decisions"]},
        },
        "latencyExperiments": latency_experiments,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
