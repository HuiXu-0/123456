"""Offline Legacy/Trusted RAG benchmark for TASK-004-B.

This module has no production imports from API or Workflow code and never changes
runtime routing.  ``correctness`` is deliberately a retrieval-support proxy, not
an LLM-generated-answer or human-preference score.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


EvidenceRunner = Callable[[dict[str, Any]], list[dict[str, Any]]]


def load_canary_dataset(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("datasetType") != "representative_evaluation_dataset":
        raise ValueError("TASK-004-B只接受明确标记的representative evaluation dataset")
    if payload.get("productionShadowData") is not False:
        raise ValueError("不得将TASK-004-A数据集标记为生产Shadow数据")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测数据集cases不能为空")
    if payload.get("caseCount") != len(cases):
        raise ValueError("caseCount与cases实际数量不一致")
    version = payload.get("datasetVersion")
    for case in cases:
        required = {
            "caseId", "query", "standardAnswer", "expectedEvidence",
            "answerable", "refusal", "correctnessCriteria",
            "evidenceCoverageCriteria", "difficulty", "datasetVersion",
        }
        if not required <= set(case):
            raise ValueError(f"{case.get('caseId', '<unknown>')}缺少Benchmark字段")
        if case["datasetVersion"] != version:
            raise ValueError(f"{case['caseId']}的数据集版本不一致")
    metadata = {
        "datasetVersion": version,
        "datasetType": payload["datasetType"],
        "productionShadowData": payload["productionShadowData"],
        "caseCount": len(cases),
    }
    return metadata, cases


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 6)


def _document_id(item: dict[str, Any]) -> str | None:
    metadata = item.get("metadata") or {}
    source = item.get("source") or {}
    value = metadata.get("document_id") or metadata.get("docId") or source.get("documentId")
    return str(value) if value is not None else None


def _quality(case: dict[str, Any], evidence: list[dict[str, Any]], *, failed: bool) -> dict[str, float]:
    if failed:
        return {"evidenceCoverage": 0.0, "conceptCoverage": 0.0, "correctness": 0.0}
    if not case["answerable"]:
        score = 1.0 if not evidence else 0.0
        return {"evidenceCoverage": score, "conceptCoverage": score, "correctness": score}

    expected = {
        str(value)
        for value in case["evidenceCoverageCriteria"]["requiredDocumentIds"]
    }
    retrieved = {value for item in evidence if (value := _document_id(item))}
    evidence_coverage = len(expected & retrieved) / len(expected) if expected else 0.0

    required_concepts = [
        str(value).lower().replace(" ", "")
        for value in case["correctnessCriteria"]["requiredConcepts"]
    ]
    evidence_text = "".join(str(item.get("content") or "").lower().replace(" ", "") for item in evidence)
    concept_coverage = (
        sum(concept in evidence_text for concept in required_concepts) / len(required_concepts)
        if required_concepts
        else 0.0
    )
    correctness = 1.0 if evidence_coverage == 1.0 and concept_coverage == 1.0 else 0.0
    return {
        "evidenceCoverage": round(evidence_coverage, 6),
        "conceptCoverage": round(concept_coverage, 6),
        "correctness": correctness,
    }


class CanaryBenchmark:
    """Interleaved, content-free offline benchmark for two retrieval paths."""

    def __init__(self, *, timeout_ms: float = 5_000.0, clock: Callable[[], float] | None = None) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms必须为正数")
        self.timeout_ms = timeout_ms
        self.clock = clock or time.perf_counter

    def _observe(
        self,
        system: str,
        case: dict[str, Any],
        runner: EvidenceRunner,
        round_index: int,
    ) -> dict[str, Any]:
        started = self.clock()
        evidence: list[dict[str, Any]] = []
        error_type: str | None = None
        explicit_timeout = False
        try:
            result = runner(case)
            if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise TypeError("Benchmark runner必须返回Evidence字典列表")
            evidence = result
        except Exception as exc:  # offline result captures type only; no content is persisted
            error_type = type(exc).__name__
            explicit_timeout = isinstance(exc, TimeoutError) or "timeout" in error_type.lower()
        latency_ms = max(0.0, (self.clock() - started) * 1_000)
        timed_out = explicit_timeout or latency_ms > self.timeout_ms
        quality = _quality(case, evidence, failed=error_type is not None)
        return {
            "caseId": case["caseId"],
            "category": case["category"],
            "round": round_index,
            "latencyMs": round(latency_ms, 6),
            "timedOut": timed_out,
            "errorType": error_type,
            "evidenceCount": len(evidence),
            **quality,
            "system": system,
        }

    @staticmethod
    def _aggregate(system: str, case_count: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(rows)
        latencies = [float(row["latencyMs"]) for row in rows]
        return {
            "system": system,
            "caseCount": case_count,
            "sampleCount": count,
            "successCount": sum(row["errorType"] is None for row in rows),
            "latencyMs": {
                "mean": round(sum(latencies) / count, 6) if count else None,
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
            },
            "timeoutRate": round(sum(row["timedOut"] for row in rows) / count, 6) if count else None,
            "errorRate": round(sum(row["errorType"] is not None for row in rows) / count, 6) if count else None,
            "evidenceCoverage": round(sum(row["evidenceCoverage"] for row in rows) / count, 6) if count else None,
            "conceptCoverage": round(sum(row["conceptCoverage"] for row in rows) / count, 6) if count else None,
            "correctness": round(sum(row["correctness"] for row in rows) / count, 6) if count else None,
            "caseResults": rows,
        }

    def run_pair(
        self,
        cases: Sequence[dict[str, Any]],
        *,
        legacy_runner: EvidenceRunner,
        trusted_runner: EvidenceRunner,
        rounds: int = 1,
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("Benchmark cases不能为空")
        if rounds < 1:
            raise ValueError("rounds必须为正整数")
        rows: dict[str, list[dict[str, Any]]] = {"legacy": [], "trusted": []}
        runners = {"legacy": legacy_runner, "trusted": trusted_runner}
        sample_index = 0
        for round_index in range(1, rounds + 1):
            for case in cases:
                order = ("legacy", "trusted") if sample_index % 2 == 0 else ("trusted", "legacy")
                for system in order:
                    rows[system].append(
                        self._observe(system, case, runners[system], round_index)
                    )
                sample_index += 1
        legacy = self._aggregate("legacy", len(cases), rows["legacy"])
        trusted = self._aggregate("trusted", len(cases), rows["trusted"])
        return {
            "measurementPolicy": {
                "executionOrder": "per_case_interleaved_and_alternating",
                "percentileMethod": "nearest_rank",
                "timeoutBudgetMs": self.timeout_ms,
                "timeoutSemantics": "TimeoutError or completed latency above budget; no hard cancellation",
                "qualitySemantics": (
                    "correctness is a deterministic retrieval-support proxy requiring full expected "
                    "document and concept coverage; it is not generated-answer human correctness"
                ),
            },
            "legacy": legacy,
            "trusted": trusted,
            "comparison": {
                "trustedMinusLegacy": {
                    "p50LatencyMs": round(trusted["latencyMs"]["p50"] - legacy["latencyMs"]["p50"], 6),
                    "p95LatencyMs": round(trusted["latencyMs"]["p95"] - legacy["latencyMs"]["p95"], 6),
                    "p99LatencyMs": round(trusted["latencyMs"]["p99"] - legacy["latencyMs"]["p99"], 6),
                    "timeoutRate": round(trusted["timeoutRate"] - legacy["timeoutRate"], 6),
                    "errorRate": round(trusted["errorRate"] - legacy["errorRate"], 6),
                    "evidenceCoverage": round(trusted["evidenceCoverage"] - legacy["evidenceCoverage"], 6),
                    "correctness": round(trusted["correctness"] - legacy["correctness"], 6),
                }
            },
        }
